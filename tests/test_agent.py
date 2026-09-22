from __future__ import annotations

from pathlib import Path

import pytest

from mainplate.agent import Reach
from mainplate.agent import reaching
from mainplate.sandbox import Filesystem
from mainplate.sandbox import InAScratch
from mainplate.sandbox import InAWorktree
from mainplate.sandbox import Isolation
from mainplate.sandbox import OverEverything
from mainplate.snapshots import Worktree
from mainplate.tools import GitTracked
from mainplate.tools import Scratch
from mainplate.tools import System

BWRAP = "/usr/bin/bwrap"
WORKTREE = Worktree(root=Path("/var/lib/mainplate/worktrees/aaaa"), gitdir=Path("/var/lib/mainplate/clones/x.git"))
SCRATCH = Path("/var/lib/mainplate/scratch/aaaa")


class TestWhatASessionReaches:
    """
    What each isolation affords, decided by the isolation and not by what a caller was handed.

    Pure: four inputs in, one value out, so the arms are held here without a store, a sandbox or a
    provider anywhere near them.
    """

    def test_a_session_with_no_repository_reaches_its_scratch_and_commands_in_it(self) -> None:
        reach = reaching(Isolation(filesystem=Filesystem.NOTHING, network=False), None, SCRATCH, BWRAP)
        assert reach.roots == (Scratch(path=SCRATCH),)
        assert reach.confinement == InAScratch(scratch=SCRATCH)
        assert "`scratch`" in reach.note
        assert "no repository and no worktree" in reach.note
        assert "cannot reach the network" in reach.note
        assert str(SCRATCH) not in reach.note, "no path is ever said, so every session of a shape shares a prefix"

    def test_the_network_answer_is_said_either_way(self) -> None:
        connected = reaching(Isolation(filesystem=Filesystem.NOTHING, network=True), None, SCRATCH, BWRAP)
        assert "can reach the network" in connected.note

    @pytest.mark.parametrize(
        ("scratch", "bwrap"),
        [(None, BWRAP), (SCRATCH, None), (None, None)],
        ids=["no scratch", "no sandbox", "neither"],
    )
    def test_without_a_sandbox_or_a_scratch_a_session_with_no_repository_reaches_nothing(
        self, scratch: Path | None, bwrap: str | None
    ) -> None:
        """A `read` over a directory only a command makes exist is a tool that can only fail."""
        assert reaching(Isolation(filesystem=Filesystem.NOTHING), None, scratch, bwrap) == Reach()

    def test_a_worktree_session_reaches_the_worktree_first_and_its_scratch_beside_it(self) -> None:
        reach = reaching(Isolation(filesystem=Filesystem.WORKTREE, network=False), WORKTREE, SCRATCH, BWRAP)
        assert reach.roots == (GitTracked(worktree=WORKTREE), Scratch(path=SCRATCH))
        assert reach.confinement == InAWorktree(worktree=WORKTREE, scratch=SCRATCH)
        assert "git worktree" in reach.note

    def test_a_worktree_session_without_a_sandbox_keeps_its_file_tools_and_gets_no_bash(self) -> None:
        reach = reaching(Isolation(filesystem=Filesystem.WORKTREE), WORKTREE, SCRATCH, None)
        assert reach.roots == (GitTracked(worktree=WORKTREE),)
        assert reach.confinement is None

    def test_a_worktree_session_before_its_worktree_is_planted_reaches_nothing_yet(self) -> None:
        assert reaching(Isolation(filesystem=Filesystem.WORKTREE), None, SCRATCH, BWRAP) == Reach()

    def test_a_whole_machine_session_reaches_the_root_and_no_scratch(self) -> None:
        reach = reaching(Isolation(filesystem=Filesystem.EVERYTHING, network=True), None, SCRATCH, BWRAP)
        assert reach.roots == (System(path=Path("/")),)
        assert reach.confinement == OverEverything()
        assert "scratch" not in reach.note

    def test_a_whole_machine_session_without_a_sandbox_reaches_nothing(self) -> None:
        assert reaching(Isolation(filesystem=Filesystem.EVERYTHING), None, SCRATCH, None) == Reach()
