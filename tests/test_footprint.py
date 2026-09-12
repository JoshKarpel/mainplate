from __future__ import annotations

import os
from pathlib import Path

import pytest
from calling import calling
from conftest import DEFAULT_CHOICE
from conftest import WHEN
from conftest import already
from conftest import started
from without_asgi import ASGIApp

from mainplate.app import build_app
from mainplate.footprint import Footprints
from mainplate.footprint import Places
from mainplate.footprint import measured
from mainplate.footprint import swept
from mainplate.forge import Clones
from mainplate.forge import Reachable
from mainplate.forge import Reaching
from mainplate.forge import Workspaces
from mainplate.pages import footprint_note
from mainplate.pages import sized
from mainplate.service import Service
from mainplate.sessions import Footprint


def on_disk(*paths: Path) -> int:
    """What `du` would say these take, each counted as itself, which is the answer a walk must reach."""
    return sum(os.lstat(path).st_blocks * 512 for path in paths)


class TestSizing:
    @pytest.mark.parametrize(
        ("allocated", "drawn"),
        [
            (0, "0 B"),
            (512, "512 B"),
            (1024, "1 KiB"),
            (1536, "1.5 KiB"),
            (10_000, "9.8 KiB"),
            (812_000, "793 KiB"),
            (46_400_000, "44 MiB"),
            (1_290_000_000, "1.2 GiB"),
            (3 * 1024**4, "3 TiB"),
            (5000 * 1024**4, "5000 TiB"),
        ],
    )
    def test_a_size_is_drawn_to_the_figures_a_person_reads(self, allocated: int, drawn: str) -> None:
        assert sized(allocated) == drawn

    def test_the_sentence_behind_the_figure_names_the_directories_and_the_time(self) -> None:
        note = footprint_note(Footprint(allocated=46_400_000, measured_at=WHEN))

        assert note == "44 MiB on disk across this session's worktree, scratch and plugins, measured at 15:09"


class TestMeasuring:
    def test_a_directory_that_is_not_there_is_nothing(self, tmp_path: Path) -> None:
        assert measured([tmp_path / "never"]) == 0

    def test_a_directory_and_its_files_are_counted_as_the_disk_holds_them(self, tmp_path: Path) -> None:
        held = tmp_path / "held"
        nested = held / "deeper"
        nested.mkdir(parents=True)
        first = held / "first.bin"
        second = nested / "second.bin"
        first.write_bytes(b"x" * 10_000)
        second.write_bytes(b"y" * 70_000)

        assert measured([held]) == on_disk(held, nested, first, second)

    def test_a_file_linked_from_two_places_is_counted_once(self, tmp_path: Path) -> None:
        """`uv` links a worktree's venv to the cache in the scratch, and both are the session's."""
        scratch = tmp_path / "scratch"
        worktree = tmp_path / "worktree"
        scratch.mkdir()
        worktree.mkdir()
        cached = scratch / "wheel.bin"
        cached.write_bytes(b"z" * 50_000)
        os.link(cached, worktree / "wheel.bin")

        assert measured([worktree, scratch]) == on_disk(worktree, scratch, cached)

    def test_a_symbolic_link_is_counted_as_itself_and_not_followed(self, tmp_path: Path) -> None:
        elsewhere = tmp_path / "elsewhere.bin"
        elsewhere.write_bytes(b"w" * 200_000)
        held = tmp_path / "held"
        held.mkdir()
        link = held / "out"
        link.symlink_to(elsewhere)

        assert measured([held]) == on_disk(held, link)


@pytest.fixture
def places(tmp_path: Path) -> Places:
    """The roots as `open_console` names them, under one directory, with no repository reachable."""
    return Places(
        workspaces=Workspaces(
            clones=Clones(root=tmp_path / "clones"),
            root=tmp_path / "worktrees",
            scratch=tmp_path / "scratch",
            reaching=Reaching(current=Reachable(repositories=())),
        ),
        plugins=tmp_path / "plugins",
    )


class TestWhereASessionsDirectoriesAre:
    def test_a_session_in_a_repository_has_its_worktree_gits_directory_for_it_its_scratch_and_its_plugins(
        self, places: Places, tmp_path: Path
    ) -> None:
        assert places.of("ab" * 16, "test:fixture") == (
            tmp_path / "worktrees" / ("ab" * 16),
            tmp_path / "clones" / "test:fixture.git" / "worktrees" / ("ab" * 16),
            tmp_path / "scratch" / ("ab" * 16),
            tmp_path / "plugins" / ("ab" * 16),
        )

    def test_a_session_in_no_repository_has_only_its_scratches(self, places: Places, tmp_path: Path) -> None:
        assert places.of("cd" * 16, None) == (
            tmp_path / "scratch" / ("cd" * 16),
            tmp_path / "plugins" / ("cd" * 16),
        )


class TestSweeping:
    async def test_a_sweep_measures_every_session_the_index_knows(self, service: Service, places: Places) -> None:
        first = await started(service, "first", DEFAULT_CHOICE)
        second = await started(service, "second", DEFAULT_CHOICE)
        scratch = places.of(first.id, None)[0]
        scratch.mkdir(parents=True)
        (scratch / "fetched.bin").write_bytes(b"f" * 30_000)
        holder = Footprints()

        await swept(holder, places, service.database, now=lambda: WHEN)

        assert holder.current == {
            first.id: Footprint(allocated=on_disk(scratch, scratch / "fetched.bin"), measured_at=WHEN),
            second.id: Footprint(allocated=0, measured_at=WHEN),
        }

    async def test_a_sweep_replaces_what_the_holder_held_rather_than_editing_it(
        self, service: Service, places: Places
    ) -> None:
        session = await started(service, "first", DEFAULT_CHOICE)
        holder = Footprints()
        await swept(holder, places, service.database)
        before = holder.current

        scratch = places.of(session.id, None)[0]
        scratch.mkdir(parents=True)
        (scratch / "fetched.bin").write_bytes(b"f" * 30_000)
        await swept(holder, places, service.database)

        assert before[session.id].allocated == 0
        assert holder.current[session.id].allocated == on_disk(scratch, scratch / "fetched.bin")
        assert holder.current is not before

    async def test_a_session_that_cannot_be_read_keeps_what_the_last_sweep_said(
        self, service: Service, places: Places
    ) -> None:
        """
        Kept rather than dropped, because dropped draws the row as never measured, which is a
        different state from measured and now unreadable; and logged rather than raised, because a
        background task that raises stops quietly until the process exits.
        """
        session = await started(service, "first", DEFAULT_CHOICE)
        scratch = places.of(session.id, None)[0]
        scratch.mkdir(parents=True)
        (scratch / "fetched.bin").write_bytes(b"f" * 30_000)
        holder = Footprints()
        await swept(holder, places, service.database)
        was = holder.current[session.id]

        scratch.chmod(0o000)
        try:
            await swept(holder, places, service.database)
        finally:
            scratch.chmod(0o700)

        assert holder.current[session.id] == was


class TestWhatAPageSaysASessionTakes:
    """
    The figure on a row and on the note under the message box, read out of the holder rather than
    walked, which is what lets these run over a store with no directories at all.
    """

    @pytest.fixture
    def app(self, service: Service) -> ASGIApp:
        return build_app(already(service))

    async def test_a_row_and_the_note_draw_the_figure_with_the_sentence_behind_it(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await started(service, "first", DEFAULT_CHOICE)
        service.footprints.current = {session.id: Footprint(allocated=46_400_000, measured_at=WHEN)}

        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session.id}")

        assert answered.status == 200
        note = "44 MiB on disk across this session&#39;s worktree, scratch and plugins, measured at 15:09"
        assert f'<span class="footprint" title="{note}">44 MiB</span>' in answered.text
        assert f'<span class="footprint" title="{note}">\N{MIDDLE DOT} 44 MiB on disk</span>' in answered.text

    async def test_a_session_measured_at_nothing_draws_no_figure(self, app: ASGIApp, service: Service) -> None:
        """A zero is a claim, and a session that has not worked yet is not worth one."""
        session = await started(service, "first", DEFAULT_CHOICE)
        service.footprints.current = {session.id: Footprint(allocated=0, measured_at=WHEN)}

        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session.id}")

        assert 'class="footprint"' not in answered.text

    async def test_a_session_nothing_has_measured_draws_no_figure(self, app: ASGIApp, service: Service) -> None:
        session = await started(service, "first", DEFAULT_CHOICE)

        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session.id}")

        assert 'class="footprint"' not in answered.text
