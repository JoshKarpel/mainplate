from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mainplate.preparing import ENV_FILE
from mainplate.preparing import SCRIPT
from mainplate.preparing import PreparationFailed
from mainplate.preparing import environment_in
from mainplate.preparing import has_setup
from mainplate.preparing import prepared
from mainplate.sandbox import sandbox_command
from mainplate.snapshots import Worktree


async def run(*arguments: str, cwd: Path) -> str:
    process = await asyncio.create_subprocess_exec(
        *arguments, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"{arguments} failed: {out.decode()}")
    return out.decode().strip()


@pytest.fixture
def bwrap() -> str:
    """Where the sandbox is, and a loud failure without one, for `test_sandbox.py`'s reason."""
    return sandbox_command()


@pytest.fixture
async def worktree(tmp_path: Path) -> Worktree:
    """
    A linked worktree off a bare clone, which is the only shape this console ever makes.

    The setup script runs behind the same sandbox `bash` gets, and that sandbox binds the clone the
    worktree points into, so a plain checkout would prove nothing about the namespace a real session
    runs its script in.
    """
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("fixture\n")
    await run("git", "init", "-q", "-b", "main", cwd=source)
    await run("git", "config", "user.email", "probe@example.invalid", cwd=source)
    await run("git", "config", "user.name", "Probe", cwd=source)
    await run("git", "add", "-A", cwd=source)
    await run("git", "commit", "-qm", "first", cwd=source)
    clone = tmp_path / "clones" / "fixture.git"
    clone.parent.mkdir(parents=True)
    await run("git", "clone", "-q", "--bare", str(source), str(clone), cwd=tmp_path)
    planted = tmp_path / "worktrees" / "session"
    await run("git", "worktree", "add", "-q", str(planted), "HEAD", cwd=clone)
    return Worktree(root=planted)


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    return tmp_path / "scratch" / "session"


def carrying(worktree: Worktree, script: str) -> None:
    """Give the fixture repository a setup script saying `script`, executable, in the worktree."""
    at = worktree.root / SCRIPT
    at.parent.mkdir(exist_ok=True)
    at.write_text(script)
    at.chmod(0o755)


class TestReadingTheEnvironmentFile:
    def test_key_value_lines_are_what_the_session_gets(self) -> None:
        assert environment_in("PATH=/a/bin:/usr/bin\nGREETING=hi there\n") == {
            "PATH": "/a/bin:/usr/bin",
            "GREETING": "hi there",
        }

    def test_blank_lines_and_comments_are_passed_over(self) -> None:
        """A script that echoes a heading into the file has done nothing wrong."""
        assert environment_in("\n# what the script set\nONE=1\n\n") == {"ONE": "1"}

    def test_a_value_may_hold_an_equals_sign(self) -> None:
        assert environment_in("QUERY=a=b\n") == {"QUERY": "a=b"}

    @pytest.mark.parametrize("line", ["just words", "=nothing", "   =blank name"])
    def test_a_line_that_is_not_key_value_is_refused_naming_it(self, line: str) -> None:
        """
        Refused rather than skipped: a line meant to set `PATH` that quietly set nothing is a session
        whose tools are not on it, discovered one command at a time.
        """
        with pytest.raises(PreparationFailed, match="not KEY=value"):
            environment_in(f"FINE=1\n{line}\n")


class TestWhetherARepositoryCarriesOne:
    def test_a_script_at_the_path_is_one(self, tmp_path: Path) -> None:
        (tmp_path / SCRIPT).parent.mkdir()
        (tmp_path / SCRIPT).write_text("#!/bin/sh\n")
        assert has_setup(tmp_path)

    def test_a_directory_at_the_path_is_not(self, tmp_path: Path) -> None:
        (tmp_path / SCRIPT).mkdir(parents=True)
        assert not has_setup(tmp_path)

    def test_most_repositories_carry_none(self, tmp_path: Path) -> None:
        assert not has_setup(tmp_path)


class TestRunningTheScript:
    """
    The script runs behind the real sandbox, and these assert what it can reach from there.

    Over `bwrap` rather than a stub, because what is under test is where the script runs: that its
    `$HOME` is the session's scratch, that the worktree is where it starts, and that what it writes
    to the environment file is the whole of what crosses back.
    """

    async def test_what_it_installs_lands_in_the_sessions_scratch(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        """
        `$HOME` is the scratch, which is what a session's own commands get as theirs, so everything
        the script fetches under it is exactly where a later command looks.
        """
        carrying(
            worktree,
            '#!/bin/sh\nmkdir -p "$HOME/.local/bin" && echo installed > "$HOME/.local/bin/tool"\n'
            'echo "HOME=$HOME" >> "$MAINPLATE_ENV"\n',
        )

        environment = await prepared(worktree, scratch, bwrap)

        assert (scratch / ".local" / "bin" / "tool").read_text() == "installed\n"
        assert environment == {"HOME": str(scratch)}

    async def test_only_what_it_writes_to_the_file_crosses_back(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        """
        An allowlist by construction: the script's own environment holds more than this, and none of
        it is the session's unless the script said so.
        """
        carrying(worktree, '#!/bin/sh\nexport SECRET=hidden\necho "PATH=$HOME/bin:/usr/bin" >> "$MAINPLATE_ENV"\n')

        environment = await prepared(worktree, scratch, bwrap)

        assert environment == {"PATH": f"{scratch}/bin:/usr/bin"}

    async def test_it_starts_in_the_worktree(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        carrying(worktree, '#!/bin/sh\necho "AT=$(pwd)" >> "$MAINPLATE_ENV"\n')

        assert (await prepared(worktree, scratch, bwrap)) == {"AT": str(worktree.root)}

    async def test_the_environment_file_is_gone_afterwards(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        """What a session's commands later find in their `$HOME` is what the script installed."""
        carrying(worktree, "#!/bin/sh\ntrue\n")

        await prepared(worktree, scratch, bwrap)

        assert not (scratch / ENV_FILE).exists()

    async def test_a_script_that_writes_nothing_sets_nothing(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        carrying(worktree, "#!/bin/sh\necho getting ready\n")

        assert (await prepared(worktree, scratch, bwrap)) == {}

    async def test_a_failing_script_fails_the_setup_carrying_what_it_said(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        """
        Loudly, because there is no quiet version: a session that opened over a repository whose
        dependencies never arrived would find out one command at a time.
        """
        carrying(worktree, "#!/bin/sh\necho could not fetch the toolchain >&2\nexit 3\n")

        with pytest.raises(PreparationFailed, match="exited 3") as failed:
            await prepared(worktree, scratch, bwrap)

        assert "could not fetch the toolchain" in str(failed.value)
        assert not (scratch / ENV_FILE).exists(), "and the file is gone either way"

    async def test_a_malformed_environment_line_fails_the_setup(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        carrying(worktree, '#!/bin/sh\necho "this is not a variable" >> "$MAINPLATE_ENV"\n')

        with pytest.raises(PreparationFailed, match="not KEY=value"):
            await prepared(worktree, scratch, bwrap)

    async def test_the_clone_is_still_read_only_in_there(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        """The same namespace `bash` gets, so a setup script cannot write a history either."""
        carrying(
            worktree,
            "#!/bin/sh\nif git commit -qam nothing >/dev/null 2>&1; then echo COMMIT=DID; else echo COMMIT=DENIED; fi "
            '>> "$MAINPLATE_ENV"\n',
        )

        assert (await prepared(worktree, scratch, bwrap)) == {"COMMIT": "DENIED"}
