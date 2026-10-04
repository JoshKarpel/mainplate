from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import DEFAULT_CHOICE
from conftest import INSTRUCTIONS
from conftest import Provider
from pydantic_ai.toolsets import FunctionToolset

from mainplate.agent import Reach
from mainplate.agent import agent_for
from mainplate.agent import reaching
from mainplate.sandbox import Filesystem
from mainplate.sandbox import InACheckout
from mainplate.sandbox import InAScratch
from mainplate.sandbox import Isolation
from mainplate.sandbox import OverEverything
from mainplate.snapshots import Checkout
from mainplate.snapshots import Store
from mainplate.tools import GitTracked
from mainplate.tools import JobsInTurn
from mainplate.tools import Listed
from mainplate.tools import Scratch
from mainplate.tools import System

BWRAP = "/usr/bin/bwrap"
CHECKOUT = Checkout(
    root=Path("/var/lib/mainplate/checkouts/aaaa"),
    store=Store(path=Path("/var/lib/mainplate/clones/x.git")),
    session="aaaa",
    bwrap=BWRAP,
)
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
        assert "no repository and no checkout" in reach.note
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

    def test_a_checkout_session_reaches_the_checkout_first_and_its_scratch_beside_it(self) -> None:
        reach = reaching(Isolation(filesystem=Filesystem.CHECKOUT, network=False), CHECKOUT, SCRATCH, BWRAP)
        assert reach.roots == (GitTracked(checkout=CHECKOUT), Scratch(path=SCRATCH))
        assert reach.confinement == InACheckout(checkout=CHECKOUT, scratch=SCRATCH)
        assert "git checkout" in reach.note

    @pytest.mark.parametrize("network", [False, True], ids=["offline", "online"])
    def test_a_checkout_session_is_told_origin_refuses_a_push_whatever_its_network(self, network: bool) -> None:
        reach = reaching(Isolation(filesystem=Filesystem.CHECKOUT, network=network), CHECKOUT, SCRATCH, BWRAP)
        assert "`origin` is a read-only copy of the repository, so a push to it is refused" in reach.note

    def test_a_session_with_no_network_is_told_nothing_it_runs_can_push(self) -> None:
        reach = reaching(Isolation(filesystem=Filesystem.CHECKOUT, network=False), CHECKOUT, SCRATCH, BWRAP)
        assert "nothing you run can push" in reach.note

    def test_a_session_with_the_network_is_not_told_nothing_it_runs_can_push(self) -> None:
        """On exe.dev a connected command reaches the forge with this console's authority."""
        reach = reaching(Isolation(filesystem=Filesystem.CHECKOUT, network=True), CHECKOUT, SCRATCH, BWRAP)
        assert "nothing you run can push" not in reach.note

    def test_a_checkout_session_without_a_sandbox_keeps_its_file_tools_and_gets_no_bash(self) -> None:
        reach = reaching(Isolation(filesystem=Filesystem.CHECKOUT), CHECKOUT, SCRATCH, None)
        assert reach.roots == (GitTracked(checkout=CHECKOUT),)
        assert reach.confinement is None

    def test_a_checkout_session_before_its_checkout_is_planted_reaches_nothing_yet(self) -> None:
        assert reaching(Isolation(filesystem=Filesystem.CHECKOUT), None, SCRATCH, BWRAP) == Reach()

    def test_a_whole_machine_session_reaches_the_root_and_no_scratch(self) -> None:
        reach = reaching(Isolation(filesystem=Filesystem.EVERYTHING, network=True), None, SCRATCH, BWRAP)
        assert reach.roots == (System(path=Path("/")),)
        assert reach.confinement == OverEverything()
        assert "scratch" not in reach.note

    def test_a_whole_machine_session_without_a_sandbox_reaches_nothing(self) -> None:
        assert reaching(Isolation(filesystem=Filesystem.EVERYTHING), None, SCRATCH, None) == Reach()


FILES = {"read", "edit", "create"}
REPOSITORY = {"list", "grep"}


class TestWhichToolsASessionIsGiven:
    """
    The table in `src/mainplate/tools/AGENTS.md`, held against the agent a pass actually builds.

    Asked of `agent_for` rather than of `reaching`, because the roots alone do not say it: `list` and
    `grep` ask git, so they are offered only where a root is a checkout, and a session that reaches
    only its scratch or the whole machine gets neither rather than two tools that can only refuse.
    """

    @pytest.mark.parametrize(
        ("filesystem", "checkout", "bwrap", "given"),
        [
            (Filesystem.CHECKOUT, CHECKOUT, BWRAP, FILES | REPOSITORY | {"bash"}),
            (Filesystem.CHECKOUT, CHECKOUT, None, FILES | REPOSITORY),
            (Filesystem.CHECKOUT, None, BWRAP, set()),
            (Filesystem.EVERYTHING, None, BWRAP, FILES | {"bash"}),
            (Filesystem.EVERYTHING, None, None, set()),
            (Filesystem.NOTHING, None, BWRAP, FILES | {"bash"}),
            (Filesystem.NOTHING, None, None, set()),
        ],
        ids=[
            "checkout",
            "checkout-no-sandbox",
            "checkout-not-planted",
            "everything",
            "everything-no-sandbox",
            "nothing",
            "nothing-no-sandbox",
        ],
    )
    def test_each_isolation_is_given_the_tools_its_table_row_names(
        self, filesystem: Filesystem, checkout: Checkout | None, bwrap: str | None, given: set[str]
    ) -> None:
        chosen = replace(DEFAULT_CHOICE, isolation=Isolation(filesystem=filesystem))
        agent = agent_for(Provider().endpoints(), chosen, INSTRUCTIONS, checkout, SCRATCH, bwrap)
        named = {name for toolset in agent.toolsets if isinstance(toolset, FunctionToolset) for name in toolset.tools}
        assert named == given

    @pytest.mark.parametrize(
        ("filesystem", "checkout", "bwrap"),
        [
            (Filesystem.CHECKOUT, CHECKOUT, BWRAP),
            (Filesystem.CHECKOUT, CHECKOUT, None),
            (Filesystem.NOTHING, None, BWRAP),
            (Filesystem.NOTHING, None, None),
        ],
        ids=["checkout", "checkout-no-sandbox", "nothing", "nothing-no-sandbox"],
    )
    def test_the_job_tools_go_where_bash_goes(
        self, filesystem: Filesystem, checkout: Checkout | None, bwrap: str | None
    ) -> None:
        """A job runs in the sandbox a command runs in, so a session with no shell has nothing for one."""
        chosen = replace(DEFAULT_CHOICE, isolation=Isolation(filesystem=filesystem))
        jobs = JobsInTurn(jobs=Unrun(), session="s", turn=0)
        agent = agent_for(Provider().endpoints(), chosen, INSTRUCTIONS, checkout, SCRATCH, bwrap, jobs=jobs)
        named = {name for toolset in agent.toolsets if isinstance(toolset, FunctionToolset) for name in toolset.tools}
        assert ("bash" in named) == JOB_TOOLS.issubset(named)
        assert ("bash" in named) == bool(JOB_TOOLS & named)


JOB_TOOLS = {"start_job", "list_jobs", "read_job", "wait_job", "stop_job"}


class Unrun:
    """Jobs nothing calls, for a test about which tools are offered rather than what they do."""

    async def start(  # pragma: no cover
        self, session: str, said: str, port: int | None, asked: str | None, plugin: str | None
    ) -> str:
        raise AssertionError("not called")

    async def stop(self, session: str, entry: str, why: str, telling: bool) -> bool:  # pragma: no cover
        raise AssertionError("not called")

    async def listed(self, session: str) -> tuple[Listed, ...]:  # pragma: no cover
        raise AssertionError("not called")

    async def waited(self, session: str, entry: str, within: timedelta) -> Listed | None:  # pragma: no cover
        raise AssertionError("not called")
