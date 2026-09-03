from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mainplate.config import read_config
from mainplate.exe import Gateway
from mainplate.install import SERVICE
from mainplate.install import ProgramFailed
from mainplate.install import Ran
from mainplate.install import Systemctl
from mainplate.install import Unit
from mainplate.install import converge
from mainplate.install import data_home
from mainplate.install import default_database
from mainplate.install import remove
from mainplate.install import render_config
from mainplate.install import run
from mainplate.install import running_executable
from mainplate.install import usable_config
from mainplate.install import write_config
from mainplate.install import write_environment
from mainplate.install import write_unit
from mainplate.reference import MODELS_DEV


@pytest.fixture
def unit(tmp_path: Path) -> Unit:
    return Unit(
        config_home=tmp_path / "config",
        executable=Path("/opt/venv/bin/python"),
        host="127.0.0.1",
        port=8100,
        database=tmp_path / "data" / "mainplate" / "mainplate.db",
    )


def asking(answers: dict[str, int] | None = None) -> tuple[Systemctl, list[tuple[str, ...]]]:
    """
    A stand-in `systemctl` that records what it was asked, and the record itself.

    Asserting on the calls rather than on a service manager is the whole reason `Systemctl` is
    injected: what matters here is the sequence, which is the part that has an order the manager
    insists on.
    """
    asked: list[tuple[str, ...]] = []
    codes = answers or {}

    async def call(arguments: tuple[str, ...]) -> Ran:
        asked.append(arguments)
        return Ran(
            command=("systemctl", "--user", *arguments),
            exit_code=codes.get(arguments[0], 0),
            stdout="",
            stderr="refused",
            timed_out=False,
        )

    return call, asked


class TestRunningAProgram:
    async def test_a_program_that_works_reports_what_it_said(self) -> None:
        ran = await run("printf", "hello")
        assert (ran.ok, ran.stdout) == (True, "hello")

    async def test_a_program_that_fails_is_not_ok_and_raises_when_checked(self) -> None:
        ran = await run("false")
        assert not ran.ok
        with pytest.raises(ProgramFailed):
            ran.checked()


class TestRenderingTheUnit:
    def test_it_names_the_interpreter_rather_than_a_command_on_the_path(self, unit: Unit) -> None:
        assert "ExecStart=/opt/venv/bin/python -m mainplate serve" in unit.text

    def test_it_passes_the_absolute_database_the_install_chose(self, unit: Unit) -> None:
        assert f"--database {unit.database}" in unit.text

    def test_it_never_writes_uv_run_into_a_unit(self, unit: Unit) -> None:
        """A `uv run` holds a shared lock on the uv cache for the life of the service."""
        assert "uv run" not in unit.text

    def test_no_credential_is_ever_written_into_the_unit(self, unit: Unit) -> None:
        """
        The unit is 0644 by design; the two files that can hold a key are 0600.

        Asserted against the unit's *directives* rather than its whole text, because the text
        includes comments explaining where credentials do live, and those name the very things
        this is checking for. A bare substring search is satisfied by its own explanation.
        """
        assert f"EnvironmentFile={unit.environment}" in unit.text
        directives = [line for line in unit.text.splitlines() if line and not line.startswith("#")]
        # `API_KEY` rather than either SDK's variable by name, so a wire added later is covered by
        # a check written before it existed.
        assert not [line for line in directives if "API_KEY" in line or "api_key" in line]

    def test_a_missing_environment_file_is_a_loud_start_failure(self, unit: Unit) -> None:
        """`EnvironmentFile=-` would start the service anyway and refuse every message instead."""
        assert "EnvironmentFile=-" not in unit.text

    def test_the_unit_and_its_two_files_sit_where_xdg_says(self, unit: Unit) -> None:
        assert unit.path == unit.config_home / "systemd" / "user" / f"{SERVICE}.service"
        assert unit.environment == unit.config_home / SERVICE / "environment"
        assert unit.config == unit.config_home / SERVICE / "config.yaml"


class TestXdgPaths:
    def test_the_variable_wins_when_it_is_set(self) -> None:
        assert data_home({"XDG_DATA_HOME": "/somewhere/data"}) == Path("/somewhere/data")

    def test_an_empty_variable_falls_back_rather_than_becoming_a_relative_path(self) -> None:
        """An unset variable arrives as `""` often enough that treating it as a path is a real bug."""
        assert data_home({"XDG_DATA_HOME": ""}).is_absolute()

    def test_the_database_defaults_under_the_data_home_rather_than_the_working_directory(self) -> None:
        assert default_database(Path("/somewhere/data")) == Path("/somewhere/data/mainplate/mainplate.db")


class TestWritingTheFiles:
    def test_writing_a_unit_the_first_time_is_a_change(self, tmp_path: Path) -> None:
        assert write_unit(tmp_path / "a" / "m.service", "text") is True

    def test_writing_the_same_unit_again_is_not(self, tmp_path: Path) -> None:
        """`daemon-reload` is gated on this, and the manager needs one only when the file moved."""
        path = tmp_path / "m.service"
        write_unit(path, "text")
        assert write_unit(path, "text") is False

    def test_a_changed_unit_is_a_change(self, tmp_path: Path) -> None:
        path = tmp_path / "m.service"
        write_unit(path, "text")
        assert write_unit(path, "other") is True

    def test_a_fresh_environment_file_is_created_private_to_its_owner(self, tmp_path: Path) -> None:
        path = tmp_path / "config" / "mainplate" / "environment"
        assert write_environment(path) is True
        assert path.stat().st_mode & 0o777 == 0o600

    def test_an_environment_file_that_exists_is_never_overwritten(self, tmp_path: Path) -> None:
        """A person edits it, so an install that rewrote it would undo that on every upgrade."""
        path = tmp_path / "environment"
        path.write_text("MAINPLATE_INSTRUCTIONS=mine\n")
        assert write_environment(path) is False
        assert path.read_text() == "MAINPLATE_INSTRUCTIONS=mine\n"

    def test_a_fresh_config_is_created_private_to_its_owner(self, tmp_path: Path) -> None:
        """It holds credentials, so it is 0600 like the environment file and unlike the unit."""
        path = tmp_path / "config" / "mainplate" / "config.yaml"
        assert write_config(path, ()) is True
        assert path.stat().st_mode & 0o777 == 0o600

    def test_a_config_that_exists_is_never_overwritten(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("mine")
        assert write_config(path, ()) is False
        assert path.read_text() == "mine"

    def test_the_template_declares_no_profile_so_the_service_refuses_to_start(self, tmp_path: Path) -> None:
        """
        A template that parsed would be worse than one that does not.

        A placeholder endpoint would start a console that fails on the first message; refusing at
        boot is the loud version, and it is what `startable` reports so the install can say so.
        """
        path = tmp_path / "config.yaml"
        write_config(path, ())
        assert usable_config(path) is False

    def test_a_discovered_gateway_is_written_as_keyless_profiles_that_start(self, tmp_path: Path) -> None:
        """
        One hostname, one endpoint per wire, because each reaches models the other does not.

        The Anthropic wire is the default of the two, since its list is the one written to be read:
        every entry carries a display name and none of them is an embedding model.
        """
        path = tmp_path / "config.yaml"
        write_config(path, (Gateway(name="llm", base_url="https://llm.int.exe.xyz"),))
        assert usable_config(path) is True
        config = read_config(path)
        assert config.default == "llm-anthropic"
        assert config.endpoints["llm-anthropic"].url == "https://llm.int.exe.xyz"
        assert config.endpoints["llm-openai"].url == "https://llm.int.exe.xyz/v1", "only the OpenAI SDK wants /v1"
        assert all(endpoint.api_key is None for endpoint in config.endpoints.values())

    def test_a_written_config_points_at_a_model_reference(self, tmp_path: Path) -> None:
        """
        Written active rather than commented out, because it is most of what a model card shows.

        No gateway reached so far publishes a price, so a console installed without this offers
        models by name and id alone - and a setting nobody knows exists is a setting nobody enables.
        """
        path = tmp_path / "config.yaml"
        write_config(path, (Gateway(name="llm", base_url="https://llm.int.exe.xyz"),))
        reference = read_config(path).model_reference
        assert reference is not None
        assert reference.source == MODELS_DEV
        assert reference.format == "models.dev"

    def test_the_reference_can_be_deleted_without_taking_anything_else_with_it(self, tmp_path: Path) -> None:
        """The comment above it promises this, so a console with no outbound access can honour it."""
        rendered = render_config((Gateway(name="llm", base_url="https://llm.int.exe.xyz"),))
        without = rendered[: rendered.index("model_reference:")]
        path = tmp_path / "config.yaml"
        path.write_text(without)
        assert usable_config(path) is True
        assert read_config(path).model_reference is None


class TestConverging:
    async def test_a_first_install_reloads_enables_and_restarts(self, unit: Unit) -> None:
        systemctl, asked = asking()
        done = await converge(unit, systemctl)
        assert asked == [("daemon-reload",), ("enable", SERVICE), ("restart", SERVICE)]
        assert (done.unit_changed, done.environment_created, done.config_created) == (True, True, True)

    async def test_an_unchanged_unit_is_restarted_without_a_reload(self, unit: Unit) -> None:
        """
        The restart is unconditional and the reload is not.

        An upgrade in place renders the same text, so an install that restarted only on a
        difference would report success while leaving the old code serving.
        """
        systemctl, asked = asking()
        await converge(unit, systemctl)
        asked.clear()
        done = await converge(unit, systemctl)
        assert asked == [("enable", SERVICE), ("restart", SERVICE)]
        assert (done.unit_changed, done.environment_created, done.config_created) == (False, False, False)

    async def test_the_database_directory_exists_before_the_service_starts(self, unit: Unit) -> None:
        systemctl, _asked = asking()
        await converge(unit, systemctl)
        assert unit.database.parent.is_dir()

    async def test_a_refused_restart_is_a_failure_rather_than_a_quiet_success(self, unit: Unit) -> None:
        systemctl, _asked = asking({"restart": 1})
        with pytest.raises(ProgramFailed):
            await converge(unit, systemctl)


class TestRemoving:
    async def test_an_installed_service_is_disabled_deleted_and_reloaded_in_that_order(self, unit: Unit) -> None:
        """`systemctl` cannot disable a unit whose file is gone, so the delete goes second."""
        systemctl, asked = asking()
        await converge(unit, systemctl)
        asked.clear()
        done = await remove(unit, systemctl)
        assert asked == [("disable", "--now", SERVICE), ("reset-failed", SERVICE), ("daemon-reload",)]
        assert done.unit_removed is True
        assert not unit.path.exists()

    async def test_removing_what_is_not_there_asks_nothing_and_is_not_an_error(self, unit: Unit) -> None:
        systemctl, asked = asking()
        done = await remove(unit, systemctl)
        assert asked == []
        assert done.unit_removed is False

    async def test_the_profiles_the_settings_and_the_sessions_are_left_alone(self, unit: Unit) -> None:
        systemctl, _asked = asking()
        await converge(unit, systemctl)
        unit.database.write_text("not really a database, but it is the user's")
        await remove(unit, systemctl)
        assert unit.environment.exists()
        assert unit.config.exists()
        assert unit.database.exists()


class TestNamingTheInterpreter:
    def test_it_is_the_running_one_and_is_not_resolved_through_a_venv_symlink(self) -> None:
        """Resolving a venv's `bin/python` yields the base interpreter, which cannot import this."""
        assert running_executable() == Path(sys.executable)
