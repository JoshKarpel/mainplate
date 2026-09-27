from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from calling import calling
from conftest import DEFAULT_CHOICE
from conftest import FIXTURE
from conftest import INSTRUCTIONS
from conftest import PLANTED
from conftest import Provider
from conftest import Scripted
from conftest import already
from conftest import calls
from conftest import run
from conftest import started
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from test_conversation import pass_at
from without_asgi import ASGIApp

from mainplate.agent import Choice
from mainplate.app import build_app
from mainplate.conversation import NoSuchRepository
from mainplate.conversation import choice_of
from mainplate.conversation import conversing
from mainplate.conversation import opening_tree_key
from mainplate.conversation import tree_key
from mainplate.durability import parse_tree
from mainplate.forge import Clones
from mainplate.forge import Reachable
from mainplate.forge import Reaching
from mainplate.forge import Repository
from mainplate.forge import Workspaces
from mainplate.sandbox import InAWorktree
from mainplate.sandbox import Venue
from mainplate.service import Service
from mainplate.settings import Settings
from mainplate.snapshots import TRANSFER
from mainplate.snapshots import NotAWorktree
from mainplate.snapshots import SnapshotFailed
from mainplate.snapshots import Store
from mainplate.snapshots import Worktree
from mainplate.snapshots import branch_named
from mainplate.tools import GitTracked
from mainplate.tools.bash.tools import ran


class TestWorkingFromARelativeDatabase:
    """
    A console started from a working directory, which is how every foreground run starts one.

    The fixtures above hand `Clones` and `Worktrees` an absolute root, so nothing else here would
    notice a root that was relative. These two go the whole way from the setting: `git` is run with
    a `cwd` of the caller's choosing, so a destination that is not absolute is resolved somewhere
    nobody asked for, and the idempotence checks that look at the asked-for path then never fire.
    """

    async def test_a_clone_lands_where_it_was_asked_for_and_is_made_once(
        self, origin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        root = Settings(database=Path("mainplate.db")).workspace_root
        clones = Clones(root=root / "clones")
        repository = Repository(forge="test", key="fixture", name="me/fixture", url=str(origin))

        assert await clones.ensure(repository) == clones.at(repository.id)
        assert clones.cloned(repository.id), "the clone is where `at` says it is, not one level deeper"
        assert list(root.rglob("*.git")) == [clones.at(repository.id)]
        # The second pass, which is what a session's second turn does. Cloning again would fail on
        # a destination that already exists, so this is the assertion that `ensure` is idempotent
        # rather than merely written to look it.
        assert await clones.ensure(repository) == clones.at(repository.id)

    async def test_a_checkout_is_planted_outside_the_repository_and_only_once(
        self, origin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bwrap: str
    ) -> None:
        monkeypatch.chdir(tmp_path)
        root = Settings(database=Path("mainplate.db")).workspace_root
        clones = Clones(root=root / "clones")
        repository = Repository(forge="test", key="fixture", name="me/fixture", url=str(origin))
        await clones.ensure(repository)
        worktrees = clones.worktrees(repository.id, root / "worktrees", bwrap)

        planted = await worktrees.plant("a" * 32)
        assert planted.root == worktrees.at("a" * 32)
        assert (planted.root / "src" / "kept.txt").is_file()
        # Outside the clone, which is the property the doubling breaks: a checkout resolved against
        # the repository's own directory would be captured by the snapshots it exists to take.
        assert not planted.root.is_relative_to(clones.at(repository.id))
        assert worktrees.planted("a" * 32)
        (planted.root / "src" / "in-progress.txt").write_text("half done\n")
        assert (await worktrees.plant("a" * 32)).root == planted.root
        assert (planted.root / "src" / "in-progress.txt").read_text() == "half done\n"
        assert [each.name for each in worktrees.root.iterdir()] == ["a" * 32], "and nothing half-built beside it"


class TestWhatAPoisonedCheckoutCanRun:
    """
    Git configuration in a checkout runs a program only inside its sandbox.

    `core.fsmonitor` is the payload because it is the one that fires inside `add` and exits 0
    regardless, so the capture succeeds and nothing reports it. It writes a file where the test can
    see it and the sandbox cannot, so the file existing afterwards is the payload having escaped.
    """

    @pytest.fixture
    async def poisoned(self, worktree: Worktree, tmp_path: Path) -> Path:
        outside = tmp_path / "escaped"
        await run("git", "config", "core.fsmonitor", f"touch {outside}; false", cwd=worktree.root)
        return outside

    async def test_the_control_runs_the_payload_when_git_is_not_confined(
        self, worktree: Worktree, poisoned: Path
    ) -> None:
        """The control, run first, so a pass below is the confinement working and not a dud payload."""
        await run("git", "status", "--short", cwd=worktree.root)

        assert poisoned.exists()

    async def test_capturing_runs_it_only_where_it_cannot_reach(self, worktree: Worktree, poisoned: Path) -> None:
        (worktree.root / "src" / "written.txt").write_text("by the session\n")

        held = await worktree.paths(await worktree.capture("after"))

        assert not poisoned.exists()
        assert "src/written.txt" in held

    async def test_listing_runs_it_only_where_it_cannot_reach(self, worktree: Worktree, poisoned: Path) -> None:
        listed = await GitTracked(worktree=worktree).entries(worktree.root)

        assert not poisoned.exists()
        assert "src/kept.txt" in listed

    async def test_a_diff_runs_nothing_the_checkout_configured(self, worktree: Worktree, tmp_path: Path) -> None:
        """
        A diff is asked of the store, so a diff driver the session named for every file is never read.

        The attribute and the driver are both the checkout's, which is where a session can put them.
        """
        outside = tmp_path / "differ-ran"
        await run("git", "config", "diff.evil.command", f"touch {outside}; false", cwd=worktree.root)
        (worktree.root / ".gitattributes").write_text("* diff=evil\n")
        before = await worktree.capture("before")
        (worktree.root / "src" / "kept.txt").write_text("changed\n")
        after = await worktree.capture("after")

        said = await worktree.diff(before, after)

        assert not outside.exists()
        assert "+changed" in said


async def objects_in(store: Store) -> int:
    """How many objects a store holds, loose and packed, which is what a capture adds to."""
    counted = dict(line.split(": ") for line in (await store.demand("count-objects", "-v")).splitlines())
    return int(counted["count"]) + int(counted["in-pack"])


@pytest.fixture
def on_fixture() -> Choice:
    """The default choice, working in the fixture repository."""
    return replace(DEFAULT_CHOICE, repository=FIXTURE)


@pytest.fixture
async def planting(service: Service, workspaces: Workspaces) -> Service:
    """The service, with workspaces, so a session names a repository and a fork carries its tree."""
    return replace(service, workspaces=workspaces)


class TestCapturing:
    async def test_a_capture_is_a_tree_that_holds_the_tracked_files(self, worktree: Worktree) -> None:
        tree = await worktree.capture("first")

        assert await worktree.paths(tree) == (".gitignore", "src/kept.txt")

    async def test_an_ignored_file_is_not_captured(self, worktree: Worktree) -> None:
        """
        The decision this module is built around. It is what makes going back to a turn keep the
        thing you installed between then and now, and what stops a snapshot carrying a secret.
        """
        held = await worktree.paths(await worktree.capture("first"))

        assert ".env" not in held
        assert not any(path.startswith("built/") for path in held)

    async def test_changing_a_file_changes_the_tree(self, worktree: Worktree) -> None:
        was = await worktree.capture("before")
        (worktree.root / "src" / "kept.txt").write_text("edited\n")

        assert await worktree.capture("after") != was

    async def test_an_unchanged_worktree_writes_no_new_commit(self, worktree: Worktree) -> None:
        """The dedupe: cost tracks what changed rather than how often this is called."""
        await worktree.capture("first")
        was = await worktree.store.commit_at(worktree.snapshots_ref)

        again = await worktree.capture("second")

        assert await worktree.store.commit_at(worktree.snapshots_ref) == was
        assert again == await worktree.store.demand("rev-parse", f"{was}^{{tree}}")

    async def test_a_capture_the_store_already_holds_sends_nothing(self, worktree: Worktree) -> None:
        """
        An untouched checkout is its base's tree, which the store has, so no bundle crosses.

        Asserted by what the store is left holding, which is the one commit that chains the tree.
        """
        was = await objects_in(worktree.store)

        await worktree.capture("untouched")

        assert await objects_in(worktree.store) - was == 1

    async def test_a_capture_sends_only_what_changed_since_the_base(self, worktree: Worktree) -> None:
        """
        What the bundle is thin against, which is the difference between a snapshot costing a file
        and one costing the repository. The one changed blob is the only blob in the pack it left,
        and `.gitignore`, unchanged beside it, did not cross.
        """
        was = await objects_in(worktree.store)
        (worktree.root / "src" / "kept.txt").write_text("the one change\n")

        await worktree.capture("changed")

        # The carried commit, the two trees above the file, the file, and the link in the chain.
        assert await objects_in(worktree.store) - was == 5

    async def test_an_untracked_file_is_captured(self, worktree: Worktree) -> None:
        """`-A` rather than `-u`: a file the agent has just written is not yet tracked."""
        (worktree.root / "src" / "new.txt").write_text("fresh\n")

        assert "src/new.txt" in await worktree.paths(await worktree.capture("with a new file"))


class TestLeavingTheReaderAlone:
    async def test_capturing_does_not_stage_anything_in_the_reader_s_index(self, worktree: Worktree) -> None:
        (worktree.root / "src" / "kept.txt").write_text("edited but not staged\n")

        await worktree.capture("while they were working")

        assert await run("git", "diff", "--cached", "--name-only", cwd=worktree.root) == ""

    async def test_capturing_moves_no_branch_and_leaves_no_branch_behind(self, worktree: Worktree) -> None:
        was = await run("git", "rev-parse", "HEAD", cwd=worktree.root)

        await worktree.capture("a snapshot")

        assert await run("git", "rev-parse", "HEAD", cwd=worktree.root) == was
        assert await run("git", "branch", "--format=%(refname:short)", cwd=worktree.root) == branch_named(PLANTED)

    async def test_snapshots_do_not_show_up_in_the_log(self, worktree: Worktree) -> None:
        await worktree.capture("a snapshot")

        logged = await run("git", "log", "--oneline", cwd=worktree.root)

        assert len(logged.splitlines()) == 1, "the repository's own commit, and no snapshot beside it"


class TestSurvivingCollection:
    async def test_a_tree_is_still_there_after_an_aggressive_gc(self, worktree: Worktree) -> None:
        """
        Why the trees are chained into commits under a ref at all. A bare `write-tree` produces a
        hash nothing refers to, and the next `gc` prunes it: the checkpoint would hold a tree that
        no longer resolves, which is a rewind that fails long after the change that broke it.
        """
        (worktree.root / "src" / "kept.txt").write_text("first\n")
        first = await worktree.capture("first")
        (worktree.root / "src" / "kept.txt").write_text("second\n")
        await worktree.capture("second")

        await worktree.store.demand("reflog", "expire", "--expire=now", "--all")
        await worktree.store.demand("gc", "--prune=now", "-q")

        assert await worktree.paths(first) == (".gitignore", "src/kept.txt")

    async def test_a_tree_outlives_the_checkout_it_came_from(self, worktree: Worktree, workspaces: Workspaces) -> None:
        """What the store is for: a fork from the end of an archived session still has files to plant."""
        (worktree.root / "src" / "kept.txt").write_text("the session's own\n")
        tree = await worktree.capture("last")

        await workspaces.worktrees(FIXTURE).uproot(PLANTED)

        assert not worktree.root.exists()
        assert await worktree.store.demand("show", f"{tree}:src/kept.txt") == "the session's own"

    async def test_every_snapshot_is_reachable_through_the_one_ref(self, worktree: Worktree) -> None:
        await worktree.capture("first")
        (worktree.root / "src" / "kept.txt").write_text("second\n")
        await worktree.capture("second")

        chain = await worktree.store.demand("rev-list", worktree.snapshots_ref)

        assert len(chain.splitlines()) == 2


class TestCapturingAtOnce:
    async def test_two_captures_in_flight_together_each_describe_a_real_tree(self, worktree: Worktree) -> None:
        """
        Two captures overlapping must not share a staging file.

        A single shadow index per worktree is one file that two `git add -A` runs write over each
        other, and the loser's `write-tree` then describes a tree that never existed. It is not a
        hypothetical: a worker answering several sessions over one worktree does exactly this.
        """
        (worktree.root / "src" / "kept.txt").write_text("sent by every one of them\n")

        trees = await asyncio.gather(*(worktree.capture(f"at once {n}") for n in range(6)))

        assert len(set(trees)) == 1, "nothing changed between them, so every capture is the same tree"
        for tree in trees:
            assert await worktree.paths(tree) == (".gitignore", "src/kept.txt")

    async def test_concurrent_captures_leave_nothing_in_flight_behind(self, worktree: Worktree) -> None:
        """
        Every capture names its own transfer ref in the checkout and its own incoming ref in the
        store, so every one of them has to be cleaned up. Asserted over a batch rather than a single
        capture, because what leaks one leaks six.
        """
        (worktree.root / "src" / "kept.txt").write_text("sent by every one of them\n")

        await asyncio.gather(*(worktree.capture(f"at once {n}") for n in range(6)))

        assert await run("git", "for-each-ref", TRANSFER, cwd=worktree.root) == ""
        assert await worktree.store.demand("for-each-ref", f"{worktree.refs}/incoming") == ""


HANGING = ("-c", "alias.hang=!sleep 5", "hang")


class TestBeingStoppedPartWay:
    @pytest.mark.parametrize(
        "running",
        [
            pytest.param(lambda worktree: worktree.store.git(*HANGING), id="the store, which the fetch loop reaches"),
            pytest.param(lambda worktree: worktree.git(*HANGING), id="the checkout, behind its sandbox"),
        ],
    )
    async def test_a_git_cancelled_part_way_leaves_nothing_open_behind_it(
        self,
        worktree: Worktree,
        running: Callable[[Worktree], Awaitable[object]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        A pass is cancelled when the worker is and the fetch loop when the console stops, so a git
        still running then has a `communicate` that never resumes. Left alone, the process runs on
        and its pipes are collected at some later moment, as a `ResourceWarning` failing whichever
        test happens to be running.

        Asserted on the process and its pipes rather than by forcing a collection, because none can
        find the leak here: asyncio holds a running child's transport, and a pipe whose write end a
        grandchild still holds stays registered with the loop. Killing git does not kill what git
        started, which is why the pipes are closed as well rather than left to reach end of file.
        """
        cancelled: list[asyncio.subprocess.Process] = []
        communicate = asyncio.subprocess.Process.communicate

        async def recorded(process: asyncio.subprocess.Process, sent: bytes | None = None) -> tuple[bytes, bytes]:
            cancelled.append(process)
            return await communicate(process, sent)

        monkeypatch.setattr(asyncio.subprocess.Process, "communicate", recorded)

        with pytest.raises(TimeoutError):
            async with asyncio.timeout(1):
                await running(worktree)

        [process] = cancelled
        assert await process.wait() == -signal.SIGKILL
        assert [reader is not None and reader.at_eof() for reader in (process.stdout, process.stderr)] == [True, True]


class TestAWorktreePerSession:
    async def test_creating_a_session_clones_nothing(
        self, planting: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        Somebody is waiting on that request and a clone is a network fetch. The session exists,
        renders, and names its repository; the files arrive when the first pass runs.
        """
        session = await started(planting, "hello", on_fixture)

        assert not workspaces.clones.cloned(FIXTURE)
        assert not workspaces.at(session.id).exists()

    async def test_the_first_pass_clones_the_repository_and_plants_the_worktree(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await started(planting, "hello", on_fixture)

        await pass_at(planting, body, session.id)

        assert workspaces.clones.cloned(FIXTURE)
        assert (workspaces.at(session.id) / "src" / "kept.txt").read_text() == "original\n"

    async def test_two_sessions_do_not_see_each_other_s_files(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        The reason for a worktree apiece. Two writers in one directory make a snapshot
        unattributable, and the person is always one of the two.
        """
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        one = await started(planting, "first", on_fixture)
        two = await started(planting, "second", on_fixture)
        await pass_at(planting, body, one.id)
        await pass_at(planting, body, two.id)

        (workspaces.at(one.id) / "src" / "only-mine.txt").write_text("mine\n")

        assert not (workspaces.at(two.id) / "src" / "only-mine.txt").exists()

    async def test_a_session_can_commit_and_rebase_without_moving_another_session(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        one = await started(planting, "first", on_fixture)
        two = await started(planting, "second", on_fixture)
        await pass_at(planting, body, one.id)
        await pass_at(planting, body, two.id)
        first = workspaces.at(one.id)
        second = workspaces.at(two.id)
        second_head = await run("git", "rev-parse", "HEAD", cwd=second)
        second_branch = await run("git", "branch", "--show-current", cwd=second)

        (first / "src" / "only-mine.txt").write_text("mine\n")
        await run(
            "git",
            "-c",
            "user.email=probe@example.invalid",
            "-c",
            "user.name=probe",
            "add",
            "-A",
            cwd=first,
        )
        await run(
            "git",
            "-c",
            "user.email=probe@example.invalid",
            "-c",
            "user.name=probe",
            "commit",
            "-qm",
            "mine",
            cwd=first,
        )
        await run("git", "rebase", "HEAD~1", cwd=first)

        assert await run("git", "rev-parse", "HEAD", cwd=second) == second_head
        assert await run("git", "branch", "--show-current", cwd=second) == second_branch
        assert not (second / "src" / "only-mine.txt").exists()

    async def test_a_second_session_reuses_the_clone_rather_than_making_another(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        one = await started(planting, "first", on_fixture)
        two = await started(planting, "second", on_fixture)
        await pass_at(planting, body, one.id)
        await pass_at(planting, body, two.id)

        assert [each.name for each in workspaces.clones.root.iterdir()] == [workspaces.clones.at(FIXTURE).name]

    async def test_a_later_pass_keeps_the_work_in_a_worktree_already_planted(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """Every pass reaches the planting; re-planting would throw away what the session had done."""
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await started(planting, "hello", on_fixture)
        await pass_at(planting, body, session.id)
        (workspaces.at(session.id) / "src" / "in-progress.txt").write_text("half done\n")

        await pass_at(planting, body, session.id)

        assert (workspaces.at(session.id) / "src" / "in-progress.txt").read_text() == "half done\n"

    async def test_a_session_naming_a_repository_nobody_reaches_says_so(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        Loud, because the answer is a person's: an integration was detached, and attaching it again
        is the fix. Only a repository that was *never cloned* reaches here.
        """
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await started(planting, "hello", replace(DEFAULT_CHOICE, repository="test:long-gone"))

        with pytest.raises(NoSuchRepository, match="long-gone"):
            await pass_at(planting, body, session.id)


class TestStartingSomewhereInParticular:
    """
    Where a session's worktree begins, which is a base somebody named or the repository's own head.

    A real repository throughout, for the reason every other test here uses one: what is being asked
    is what `git worktree add` and `git rev-parse` actually do with a name, and a stand-in for git
    would be a second implementation of the thing under test.
    """

    async def test_a_session_with_no_base_starts_at_the_repository_s_head(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await started(planting, "hello", on_fixture)

        await pass_at(planting, body, session.id)

        assert (workspaces.at(session.id) / "src" / "kept.txt").read_text() == "original\n"

    async def test_a_session_started_at_a_tag_holds_the_files_that_tag_names(
        self, planting: Service, provider: Provider, workspaces: Workspaces, origin: Path
    ) -> None:
        """
        The point of naming one: the checkout holds what was there *then*, not what is there now.

        A tag rather than a branch, so that what is asserted is a resolution git had to perform
        rather than a name that happens to be `HEAD` anyway.
        """
        await run("git", "tag", "before-the-rewrite", cwd=origin)
        (origin / "src" / "kept.txt").write_text("rewritten\n")
        await run("git", "commit", "-aqm", "second", cwd=origin)
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await started(
            planting, "hello", replace(DEFAULT_CHOICE, repository=FIXTURE, base="before-the-rewrite")
        )

        await pass_at(planting, body, session.id)

        assert (workspaces.at(session.id) / "src" / "kept.txt").read_text() == "original\n"

    @pytest.mark.parametrize(
        "base",
        [
            pytest.param(None, id="saying nothing at all"),
            pytest.param("main", id="naming the branch"),
        ],
    )
    async def test_a_new_session_starts_at_the_repository_as_it_is_now(
        self,
        planting: Service,
        provider: Provider,
        workspaces: Workspaces,
        origin: Path,
        on_fixture: Choice,
        base: str | None,
    ) -> None:
        """
        The reason planting a worktree fetches, and why it does so whether or not a base was named.

        The first session clones the repository, and nothing else here ever refreshes that copy: it
        would answer out of whatever the repository looked like the first time anybody used it, for
        as long as the machine lives. So starting a session is where a person gets to say when this
        console catches up, and it has to work for the common case of naming nothing.

        Saying nothing is the arm that catches the subtle half. A fetch writes
        `refs/remotes/origin/`, so a plant that read the clone's own `HEAD` commit would refresh the
        refs and then check out the stale commit beside them - a round trip that changes nothing.
        Resolving the *default branch by name* through the same path a base takes is what fixes it.
        """
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        first = await started(planting, "hello", on_fixture)
        await pass_at(planting, body, first.id)
        assert workspaces.clones.cloned(FIXTURE), "the clone is what goes stale, so it has to exist first"
        (origin / "src" / "kept.txt").write_text("moved on\n")
        await run("git", "commit", "-aqm", "second", cwd=origin)

        second = await started(planting, "hello", replace(on_fixture, base=base))
        await pass_at(planting, body, second.id)

        assert (workspaces.at(second.id) / "src" / "kept.txt").read_text() == "moved on\n"

    async def test_a_fork_is_the_files_its_turn_saw_however_far_the_repository_has_moved(
        self, planting: Service, provider: Provider, workspaces: Workspaces, origin: Path, on_fixture: Choice
    ) -> None:
        """
        The one plant that must *not* pick up whatever the remote now says.

        A fork is checked out at a tree this console recorded, which is an object it already holds, so
        a fetch there could not change the answer - and a fork that started at the current head would
        be re-asking its turn against files that turn never saw, which is a different question wearing
        the same words.
        """
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await started(planting, "hello", on_fixture)
        await pass_at(planting, body, session.id)
        (origin / "src" / "kept.txt").write_text("moved on\n")
        await run("git", "commit", "-aqm", "second", cwd=origin)

        forked = await planting.fork(session.id, at=0, chosen=on_fixture, said="try it again")
        assert forked is not None
        await pass_at(planting, body, forked.id)

        assert (workspaces.at(forked.id) / "src" / "kept.txt").read_text() == "original\n"

    async def test_a_session_that_named_a_branch_is_on_it_rather_than_detached(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        What a branch buys: somewhere for a commit to go. A detached `HEAD` is fine for editing files
        and a dead end the moment somebody runs `git commit` in the box under the conversation.
        """
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await started(planting, "hello", replace(on_fixture, branch="try-it-this-way"))

        await pass_at(planting, body, session.id)

        assert await run("git", "branch", "--show-current", cwd=workspaces.at(session.id)) == "try-it-this-way"

    async def test_a_session_that_named_no_branch_is_given_one_rather_than_left_detached(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        The default, and it changed once `Run` put `git commit` in the box under the conversation: a
        commit on a detached `HEAD` is reachable only through the reflog, which is a way to lose work
        that nobody should have to know about.
        """
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await started(planting, "hello", on_fixture)

        await pass_at(planting, body, session.id)

        assert await run("git", "branch", "--show-current", cwd=workspaces.at(session.id)) == branch_named(session.id)

    async def test_two_sessions_get_branches_of_their_own_so_both_can_plant(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        Why the name is the session's id and not the repository's default branch or the session's
        title: `git worktree add -b` refuses a name already in use, so two sessions sharing one would
        mean the second failing to get files at all.
        """
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        one = await started(planting, "first", on_fixture)
        two = await started(planting, "second", on_fixture)

        await pass_at(planting, body, one.id)
        await pass_at(planting, body, two.id)

        on_one = await run("git", "branch", "--show-current", cwd=workspaces.at(one.id))
        on_two = await run("git", "branch", "--show-current", cwd=workspaces.at(two.id))
        assert on_one != on_two
        assert (on_one, on_two) == (branch_named(one.id), branch_named(two.id))

    async def test_a_branch_somebody_named_wins_over_the_one_this_console_would_make(
        self, planting: Service, on_fixture: Choice
    ) -> None:
        session = await started(planting, "hello", replace(on_fixture, branch="try-it-this-way"))

        chosen = choice_of(await planting.checkpointer.load(session.id))
        assert chosen is not None
        assert chosen.branch == "try-it-this-way"

    async def test_a_session_with_no_repository_is_given_no_branch(self, planting: Service) -> None:
        """There is nothing for one to be a branch *of*, which `Choice.branching` answers first."""
        session = await started(planting, "hello", replace(DEFAULT_CHOICE, repository=None))

        chosen = choice_of(await planting.checkpointer.load(session.id))
        assert chosen is not None
        assert chosen.branch is None

    async def test_a_session_with_no_repository_records_neither_a_base_nor_a_branch(self, planting: Service) -> None:
        """
        `Choice.settled`, which is what makes the start form unable to express a contradiction: a
        base and a branch are answers about a repository, so with none picked there is nothing for
        either to be about and the record says so rather than carrying words nothing will ever read.
        """
        session = await started(
            planting, "hello", replace(DEFAULT_CHOICE, repository=None, base="main", branch="somewhere")
        )

        chosen = choice_of(await planting.checkpointer.load(session.id))
        assert chosen is not None
        assert (chosen.base, chosen.branch) == (None, None)

    async def test_a_fork_takes_a_branch_of_its_own_rather_than_the_one_it_came_from(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        Two rules meeting. The parent's base and branch are both dropped - a base is a second answer
        to where the fork's files come from, which the recorded tree has already settled, and a
        branch is a name `git worktree add -b` refuses outright because the parent's worktree still
        holds it. And then a fork is given one of its own, because dropping it alone would land every
        fork on a detached `HEAD`, and a fork is exactly where somebody carries on working.
        """
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        naming = replace(on_fixture, base="main", branch="the-parent-s-branch")
        session = await started(planting, "hello", naming)
        await pass_at(planting, body, session.id)

        # With a message, because planting happens *after* the pass is told what to answer: a fork
        # left waiting never reaches the call this is about.
        forked = await planting.fork(session.id, at=1, chosen=naming, said="try it again")
        assert forked is not None
        chosen = choice_of(await planting.checkpointer.load(forked.id))
        assert chosen is not None
        assert chosen.base is None
        assert chosen.branch == branch_named(forked.id)
        # Driven rather than stopped at the record, because planting is what would have failed on an
        # inherited branch.
        await pass_at(planting, body, forked.id)
        assert (workspaces.at(forked.id) / "src" / "kept.txt").read_text() == "original\n"
        assert await run("git", "branch", "--show-current", cwd=workspaces.at(forked.id)) == branch_named(forked.id)


class TestReadingARepositorysBranches:
    """
    Where the start page's completions come from: the repository itself, not this console's copy.

    A real repository, because what is being asked is what `git ls-remote` says about one. The
    parsing of what it prints is pinned in `test_forge.py`, and what the *page* does with the answer
    is pinned in `test_commands.py`; these three are the three questions, kept apart.
    """

    async def test_the_branches_are_read_without_cloning_anything(self, workspaces: Workspaces, origin: Path) -> None:
        """
        The whole reason it asks the remote. There is no clone before a session's first pass, so
        reading one would leave the very first session on a repository - the case where you most want
        to say where to start - as the one with nothing to offer.
        """
        await run("git", "branch", "release/2.1", cwd=origin)

        found = await workspaces.branches(FIXTURE)

        assert sorted(found) == ["main", "release/2.1"]
        assert not workspaces.clones.cloned(FIXTURE), "asking cost no clone"

    async def test_a_branch_pushed_since_the_clone_is_offered(
        self, planting: Service, provider: Provider, workspaces: Workspaces, origin: Path, on_fixture: Choice
    ) -> None:
        """The other half of asking the remote: the answer cannot be as old as the local copy."""
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await started(planting, "hello", on_fixture)
        await pass_at(planting, body, session.id)
        await run("git", "branch", "landed-later", cwd=origin)

        assert "landed-later" in await workspaces.branches(FIXTURE)

    async def test_a_repository_no_forge_reaches_offers_nothing_rather_than_failing(
        self, workspaces: Workspaces
    ) -> None:
        """
        A field with no completions is the field as it was before it offered any, so this costs a
        suggestion rather than an ability. `forge.offers`'s promise, one level down.
        """
        assert await workspaces.branches("test:long-gone") == ()

    async def test_a_repository_that_cannot_be_reached_offers_nothing_rather_than_failing(
        self, workspaces: Workspaces, tmp_path: Path
    ) -> None:
        gone = Reachable(
            repositories=(Repository(forge="test", key="nowhere", name="me/nowhere", url=str(tmp_path / "nothing")),)
        )

        assert await replace(workspaces, reaching=Reaching(current=gone)).branches("test:nowhere") == ()


class TestForkingTheWorktreeToo:
    async def test_a_fork_starts_from_the_files_the_forked_turn_saw(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        The consistency the whole thing is for. Turn 1 ran against one state of the files; forking
        turn 1 has to re-ask it against *that* state, or the new model is answering a different
        question in the same words and nothing in the transcript would say so.
        """
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await started(planting, "first", on_fixture)
        await pass_at(planting, body, session.id)

        # What turn 1 will see, and then a later edit that turn 1 never saw.
        (workspaces.at(session.id) / "src" / "kept.txt").write_text("as turn one saw it\n")
        await planting.say(session.id, "second")
        await pass_at(planting, body, session.id)
        (workspaces.at(session.id) / "src" / "kept.txt").write_text("changed long after\n")

        forked = await planting.fork(session.id, at=1, chosen=on_fixture, said="second, differently")
        assert forked is not None
        # The fork's own first pass plants it, exactly as a new session's does. What the fork
        # carried across is the *tree* turn 1 started on, so the planting checks that out rather
        # than the repository's head.
        await pass_at(planting, body, forked.id)

        assert (workspaces.at(forked.id) / "src" / "kept.txt").read_text() == "as turn one saw it\n"

    async def test_the_parent_s_worktree_is_untouched_by_being_forked(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await started(planting, "first", on_fixture)
        await pass_at(planting, body, session.id)
        (workspaces.at(session.id) / "src" / "kept.txt").write_text("where the parent is now\n")

        forked = await planting.fork(session.id, at=0, chosen=on_fixture, said="again")
        assert forked is not None
        await pass_at(planting, body, forked.id)

        assert (workspaces.at(session.id) / "src" / "kept.txt").read_text() == "where the parent is now\n"

    async def test_a_fork_of_a_turn_that_never_ran_starts_where_the_repository_is(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        No recorded tree is not a failure to handle; it is a turn nothing has answered yet, so
        there is no earlier state to reproduce and the repository's head is the honest start.
        """
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await started(planting, "first", on_fixture)

        forked = await planting.fork(session.id, at=0, chosen=on_fixture, said="again")
        assert forked is not None
        await pass_at(planting, body, forked.id)

        assert (workspaces.at(forked.id) / "src" / "kept.txt").read_text() == "original\n"

    async def forked_into(self, planting: Service, opening: Choice, asked: str | None) -> str | None:
        """The repository a fork ends up in, having started from `opening` and asked for `asked`."""
        session = await started(planting, "first", opening)
        forked = await planting.fork(session.id, at=0, chosen=replace(opening, repository=asked), said="again")
        assert forked is not None
        recorded = choice_of(await planting.checkpointer.load(forked.id))
        assert recorded is not None
        return recorded.repository

    async def test_a_fork_inherits_the_repository_even_when_the_caller_names_none(
        self, planting: Service, on_fixture: Choice
    ) -> None:
        """
        The fork page offers no repository control to a session that has one, so the form it posts
        names none. Deciding this in the service rather than trusting the caller is what stops that
        omission silently moving a branch out of its repository.
        """
        assert await self.forked_into(planting, on_fixture, None) == FIXTURE

    async def test_a_fork_cannot_be_swapped_to_another_repository(self, planting: Service, on_fixture: Choice) -> None:
        """
        Re-asking a turn against *different* files is a different question wearing the same words,
        and nothing in the transcript would say so.
        """
        assert await self.forked_into(planting, on_fixture, "test:somewhere-else") == FIXTURE

    async def test_a_fork_may_attach_a_repository_to_a_session_that_had_none(self, planting: Service) -> None:
        """
        Not the same act as swapping, and the difference is why both rules exist. The turns being
        inherited were not asked against *other* files, they were asked against none, so picking a
        repository up here breaks nothing: it is the ordinary shape of thinking something through
        and then going to work on it.
        """
        assert await self.forked_into(planting, DEFAULT_CHOICE, FIXTURE) == FIXTURE

    async def test_a_fork_of_a_session_with_none_may_still_choose_none(self, planting: Service) -> None:
        assert await self.forked_into(planting, DEFAULT_CHOICE, None) is None


class TestPickingOneThroughTheConsole:
    """
    The path a browser actually takes, which is the one a test that calls `Service` directly
    misses. Forking lost its repository exactly this way: the service was right and the form that
    reached it was not.
    """

    @pytest.fixture
    def app(self, planting: Service) -> ASGIApp:
        return build_app(already(planting))

    async def test_the_dashboard_offers_a_session_in_what_the_forges_reach(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/")

        assert 'href="/sessions/new?workspace=test%3Afixture"' in answered.text
        assert 'data-name="me/fixture"' in answered.text

    async def test_the_new_session_page_is_about_the_repository_and_asks_for_its_branches(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/sessions/new?workspace=test%3Afixture")

        assert answered.status == 200
        assert "New session in me/fixture" in answered.text
        assert 'name="workspace" value="test:fixture"' in answered.text
        assert 'hx-get="/fragments/branches?workspace=test%3Afixture"' in answered.text
        assert 'hx-trigger="load"' in answered.text

    async def test_the_fetch_the_first_pass_makes_is_what_the_dashboard_says(
        self, app: ASGIApp, planting: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        session = await started(planting, "hello", on_fixture)
        await workspaces.plant(session.id, FIXTURE)
        async with calling(app) as caller:
            answered = await caller.get("/")

        assert "not fetched since the console started" not in answered.text
        assert 'class="fetch"' in answered.text

    async def test_a_posted_repository_is_recorded_on_the_session(self, app: ASGIApp, planting: Service) -> None:
        async with calling(app) as caller:
            answered = await caller.post(
                "/sessions",
                {"prompt": "hello", "endpoint": "here", "model": "ripe/fast", "workspace": FIXTURE},
            )

        assert answered.status == 303
        forked = answered.location.rsplit("/", 1)[-1]
        chosen = choice_of(await planting.checkpointer.load(forked))
        assert chosen is not None
        assert chosen.repository == FIXTURE

    async def test_choosing_no_repository_is_an_ordinary_session(self, app: ASGIApp, planting: Service) -> None:
        """The empty option, which is what this console was before there were repositories."""
        async with calling(app) as caller:
            answered = await caller.post(
                "/sessions", {"prompt": "hello", "endpoint": "here", "model": "ripe/fast", "workspace": ""}
            )

        assert answered.status == 303
        chosen = choice_of(await planting.checkpointer.load(answered.location.rsplit("/", 1)[-1]))
        assert chosen is not None
        assert chosen.repository is None

    async def test_a_repository_no_forge_reaches_is_refused(self, app: ASGIApp) -> None:
        """A select is a suggestion the page made, not a constraint on what can be posted."""
        async with calling(app) as caller:
            answered = await caller.post(
                "/sessions",
                {"prompt": "hello", "endpoint": "here", "model": "ripe/fast", "workspace": "test:invented"},
            )

        assert answered.status == 422

    async def test_the_fork_page_offers_no_repository_to_a_session_that_has_one(
        self, app: ASGIApp, planting: Service
    ) -> None:
        """Because it inherits that one; offering a choice that cannot be honoured would be a lie."""
        session = await started(planting, "first", replace(DEFAULT_CHOICE, repository=FIXTURE))

        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session.id}/forks/new?at=0")

        assert answered.status == 200
        assert 'id="repository"' not in answered.text

    async def test_the_fork_page_offers_one_to_a_session_that_has_none(self, app: ASGIApp, planting: Service) -> None:
        """The other half of the rule: a fork may attach a repository where there was none."""
        session = await started(planting, "first", DEFAULT_CHOICE)

        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session.id}/forks/new?at=0")

        assert answered.status == 200
        assert 'id="repository"' in answered.text
        assert f'value="{FIXTURE}"' in answered.text


class TestWhatTheSidebarSaysASessionWorksIn:
    """
    The repository on a session's row, which is read out of its `choice` rather than held in the
    index. A session page is where these are asked, because it draws the sidebar and no picker, so
    a repository name in the markup came from a row and not from a control.
    """

    @pytest.fixture
    def app(self, planting: Service) -> ASGIApp:
        return build_app(already(planting))

    async def test_a_row_names_the_repository_its_session_works_in(self, app: ASGIApp, planting: Service) -> None:
        session = await started(planting, "first", replace(DEFAULT_CHOICE, repository=FIXTURE))

        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session.id}")

        assert answered.status == 200
        assert '<span class="where" title="me/fixture">me/fixture</span>' in answered.text

    async def test_a_row_for_a_session_working_in_nothing_says_nothing(self, app: ASGIApp, planting: Service) -> None:
        """Most of a list is one or the other, and the majority does not need labelling."""
        session = await started(planting, "first", DEFAULT_CHOICE)

        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session.id}")

        assert answered.status == 200
        assert 'class="where"' not in answered.text

    async def test_a_row_falls_back_to_the_recorded_id_when_no_forge_reaches_it(
        self, app: ASGIApp, planting: Service
    ) -> None:
        """
        An integration detached this morning does not stop the session being readable, and the id
        is all anybody knows about the repository now. Saying nothing would be the quieter wrong
        answer, since the session is still working somewhere.
        """
        session = await started(planting, "first", replace(DEFAULT_CHOICE, repository="test:detached"))

        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session.id}")

        assert answered.status == 200
        assert '<span class="where" title="test:detached">test:detached</span>' in answered.text

    async def test_the_repository_is_read_without_the_index_holding_a_column_for_it(self, planting: Service) -> None:
        """
        The whole point of the join: `sessions` keeps the three settled facts it always did, and
        the fourth is reached in the checkpoint the session already records it in. A column here
        would be the second copy of what was said that this console does not keep.
        """
        session = await started(planting, "first", replace(DEFAULT_CHOICE, repository=FIXTURE))

        held = await planting.database.run(
            lambda connection: {str(row[1]) for row in connection.execute("PRAGMA table_info(sessions)")}
        )
        assert "repository" not in held

        listed = await planting.listed()
        assert [one.repository for one in listed if one.id == session.id] == [FIXTURE]

    async def test_attaching_a_repository_through_the_fork_form_works(self, app: ASGIApp, planting: Service) -> None:
        session = await started(planting, "first", DEFAULT_CHOICE)

        async with calling(app) as caller:
            answered = await caller.post(
                f"/sessions/{session.id}/forks",
                {
                    "at": "0",
                    "endpoint": "here",
                    "model": "ripe/fast",
                    "prompt": "now let us work",
                    "workspace": FIXTURE,
                },
            )

        assert answered.status == 303
        chosen = choice_of(await planting.checkpointer.load(answered.location.rsplit("/", 1)[-1]))
        assert chosen is not None
        assert chosen.repository == FIXTURE

    async def test_forking_through_the_console_keeps_the_repository(self, app: ASGIApp, planting: Service) -> None:
        """The bug this class exists for: the form carries no repository, so the service supplies it."""
        session = await started(planting, "first", replace(DEFAULT_CHOICE, repository=FIXTURE))

        async with calling(app) as caller:
            answered = await caller.post(
                f"/sessions/{session.id}/forks",
                {"at": "0", "endpoint": "here", "model": "ripe/fast", "prompt": "again"},
            )

        assert answered.status == 303
        chosen = choice_of(await planting.checkpointer.load(answered.location.rsplit("/", 1)[-1]))
        assert chosen is not None
        assert chosen.repository == FIXTURE


class TestWhatATurnRecords:
    async def test_a_turn_records_the_tree_of_its_own_worktree(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await started(planting, "hello", on_fixture)

        await pass_at(planting, body, session.id)

        recorded = await planting.checkpointer.load(session.id)
        assert parse_tree(recorded[opening_tree_key(0)]) == await workspaces.worktree(session.id, FIXTURE).capture(
            "the same tree"
        )

    async def test_a_later_pass_replays_the_recorded_tree_rather_than_reading_the_worktree_again(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        The rule the mechanism asks for. Reading a worktree answers differently every time it is
        asked, so a pass that re-read it would resume a conversation against a directory that has
        moved since; a recorded step hands back what the first pass saw.
        """
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await started(planting, "hello", on_fixture)
        await pass_at(planting, body, session.id)
        was = parse_tree((await planting.checkpointer.load(session.id))[opening_tree_key(0)])

        (workspaces.at(session.id) / "src" / "kept.txt").write_text("changed since\n")
        await pass_at(planting, body, session.id)

        assert parse_tree((await planting.checkpointer.load(session.id))[opening_tree_key(0)]) == was

    async def test_a_console_with_no_repository_records_that_it_had_none(
        self, service: Service, provider: Provider
    ) -> None:
        """Distinguishable from a turn nobody has reached, which has no key at all."""
        session = await started(service, "hello", DEFAULT_CHOICE)

        await pass_at(service, conversing(provider.endpoints(), INSTRUCTIONS, None), session.id)

        recorded = await service.checkpointer.load(session.id)
        assert opening_tree_key(0) in recorded
        assert parse_tree(recorded[opening_tree_key(0)]) is None

    async def test_a_turn_that_calls_a_tool_records_a_tree_on_each_side_of_it(
        self, planting: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        Why the tree is recorded per model request rather than per turn. With tools the worktree
        changes *during* a turn, so a single snapshot at the top would describe only the state the
        first request saw and a rewind to anywhere later would have nothing to go back to.
        """
        scripted = Scripted(
            script=(
                calls(("create", {"path": "src/added.txt", "content": "written by a tool\n"})),
                ModelResponse(parts=[TextPart("made it")]),
            )
        )
        session = await started(planting, "hello", on_fixture)

        await pass_at(planting, conversing(scripted.endpoints(), INSTRUCTIONS, workspaces), session.id)

        recorded = await planting.checkpointer.load(session.id)
        opening = parse_tree(recorded[tree_key(0, 0)])
        after = parse_tree(recorded[tree_key(0, 1)])
        assert opening is not None
        assert after is not None
        assert opening != after, "the tool wrote a file between the two requests"
        assert (workspaces.at(session.id) / "src" / "added.txt").read_text() == "written by a tool\n"

    async def test_the_tree_after_a_tool_call_holds_what_the_tool_wrote(
        self, planting: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """The snapshot is of the worktree, so what a tool created is reachable from it afterwards."""
        scripted = Scripted(
            script=(
                calls(("create", {"path": "src/added.txt", "content": "written by a tool\n"})),
                ModelResponse(parts=[TextPart("made it")]),
            )
        )
        session = await started(planting, "hello", on_fixture)

        await pass_at(planting, conversing(scripted.endpoints(), INSTRUCTIONS, workspaces), session.id)

        recorded = await planting.checkpointer.load(session.id)
        after = parse_tree(recorded[tree_key(0, 1)])
        assert after is not None
        held = await workspaces.worktree(session.id, FIXTURE).paths(after)
        assert "src/added.txt" in held
        assert "src/added.txt" not in await workspaces.worktree(session.id, FIXTURE).paths(
            str(parse_tree(recorded[tree_key(0, 0)]))
        )

    async def test_a_later_pass_takes_no_new_snapshots(
        self, planting: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """A replayed request replays its snapshot too, so nothing runs git and the pair stay in step."""
        scripted = Scripted(
            script=(
                calls(("create", {"path": "src/added.txt", "content": "written by a tool\n"})),
                ModelResponse(parts=[TextPart("made it")]),
            )
        )
        body = conversing(scripted.endpoints(), INSTRUCTIONS, workspaces)
        session = await started(planting, "hello", on_fixture)
        await pass_at(planting, body, session.id)
        was = await planting.checkpointer.load(session.id)

        (workspaces.at(session.id) / "src" / "kept.txt").write_text("moved on since\n")
        await pass_at(planting, body, session.id)

        now = await planting.checkpointer.load(session.id)
        assert [now[tree_key(0, at)] for at in (0, 1)] == [was[tree_key(0, at)] for at in (0, 1)]

    async def test_two_turns_edited_between_record_different_trees(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """The other writer besides the tools: a person editing the worktree between two turns."""
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await started(planting, "first", on_fixture)
        await pass_at(planting, body, session.id)

        (workspaces.at(session.id) / "src" / "kept.txt").write_text("they edited it\n")
        await planting.say(session.id, "second")
        await pass_at(planting, body, session.id)

        recorded = await planting.checkpointer.load(session.id)
        assert parse_tree(recorded[opening_tree_key(0)]) != parse_tree(recorded[opening_tree_key(1)])


class TestRefusingWhatIsNotAStore:
    async def test_a_directory_that_is_not_a_repository_is_refused_by_name(self, tmp_path: Path) -> None:
        bare = tmp_path / "not-a-repo"
        bare.mkdir()

        with pytest.raises(NotAWorktree, match=str(bare)):
            await Store(path=bare).confirm()

    async def test_a_real_store_is_accepted(self, worktree: Worktree) -> None:
        await worktree.store.confirm()


class TestWhatTheStoreBelieves:
    """
    What crosses from a checkout is checked by the store, not taken on the checkout's word.

    The checkout's git is the session's, so what it prints is something the session chose. These
    replace the one step a session could lie in with a stand-in that lies, and ask what the store
    records.
    """

    async def test_a_checkout_whose_git_names_a_tree_it_did_not_send_is_refused(
        self, worktree: Worktree, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (worktree.root / "src" / "kept.txt").write_text("what was really there\n")
        honest = Worktree.demand

        async def lying(self: Worktree, *arguments: str, **named: object) -> str:
            said = await honest(self, *arguments, **named)  # type: ignore[arg-type]
            if arguments[0] == "commit-tree":
                # A commit of a different tree than the one `write-tree` reported.
                empty = await honest(self, "hash-object", "-t", "tree", "-w", "/dev/null")
                return await honest(self, "commit-tree", empty, "-p", arguments[3], "-m", "lie")
            return said

        monkeypatch.setattr(Worktree, "demand", lying)

        with pytest.raises(SnapshotFailed, match="said"):
            await worktree.capture("lying")

        assert await worktree.store.commit_at(worktree.snapshots_ref) is None, "and nothing was chained"

    async def test_a_bundle_that_is_a_link_is_not_read(self, worktree: Worktree, tmp_path: Path) -> None:
        """A sandbox can leave a link where its bundle should be; the store reads no file through one."""
        secret = tmp_path / "secret"
        secret.write_text("the parent's, not the session's\n")
        link = tmp_path / "carried.bundle"
        link.symlink_to(secret)

        with pytest.raises(SnapshotFailed, match="regular file"):
            await worktree.store.fetched(link, "refs/heads/main", f"{worktree.refs}/incoming/linked")


class TestASessionThatBreaksItsOwnGit:
    """
    A checkout whose `.git` the session deleted or replaced, from inside its own sandbox.

    Whether a checkout is planted is read from the directory and not from `.git`, so planting again
    is still a no-op and never a rebuild over the session's files. What fails is the capture, which
    is git inside the sandbox finding no repository, and that failure is the pass's.
    """

    async def in_the_sandbox(self, worktree: Worktree, tmp_path: Path, bwrap: str, command: str) -> None:
        scratch = tmp_path / "breaking-scratch"
        scratch.mkdir(exist_ok=True)
        await ran(InAWorktree(worktree=worktree, scratch=scratch), bwrap, Venue.CONFINED, command, seconds=20)

    @pytest.mark.parametrize("breaking", ["rm -rf .git", "rm -rf .git && echo 'gitdir: /nowhere' > .git"])
    async def test_it_is_still_planted_and_planting_leaves_its_files_alone(
        self, workspaces: Workspaces, worktree: Worktree, tmp_path: Path, bwrap: str, breaking: str
    ) -> None:
        (worktree.root / "src" / "in-progress.txt").write_text("half done\n")
        await self.in_the_sandbox(worktree, tmp_path, bwrap, breaking)

        again = await workspaces.plant(worktree.session, FIXTURE)

        assert again == worktree
        assert (worktree.root / "src" / "in-progress.txt").read_text() == "half done\n"

    async def test_the_next_capture_fails_naming_git(self, worktree: Worktree, tmp_path: Path, bwrap: str) -> None:
        await self.in_the_sandbox(worktree, tmp_path, bwrap, "rm -rf .git")

        with pytest.raises(SnapshotFailed, match="git add"):
            await worktree.capture("after the session broke it")

    async def test_the_sandbox_cannot_take_the_directory_itself_away(
        self, workspaces: Workspaces, worktree: Worktree, tmp_path: Path, bwrap: str
    ) -> None:
        """What `planted` rests on: the checkout is the mount point, so its contents go and it stays."""
        await self.in_the_sandbox(worktree, tmp_path, bwrap, f"rm -rf {worktree.root}; true")

        assert workspaces.worktrees(FIXTURE).planted(worktree.session)


class TestPushing:
    """
    A session's branch reaching the repository, which is the one thing its sandbox cannot do.

    Into the fixture's own origin, which is a path and so takes a push the way a forge would.
    """

    async def test_a_commit_made_in_the_checkout_arrives_on_the_origin(
        self, worktree: Worktree, origin: Path, tmp_path: Path, bwrap: str
    ) -> None:
        scratch = tmp_path / "pushing-scratch"
        scratch.mkdir()
        await ran(
            InAWorktree(worktree=worktree, scratch=scratch),
            bwrap,
            Venue.CONFINED,
            "git -c user.email=probe@example.invalid -c user.name=probe commit -qam 'the session made this' "
            "--allow-empty",
            seconds=20,
        )
        made = await run("git", "rev-parse", "HEAD", cwd=worktree.root)

        came = await worktree.push(str(origin), branch_named(PLANTED))

        assert came.ok, came.err
        assert await run("git", "rev-parse", f"refs/heads/{branch_named(PLANTED)}", cwd=origin) == made

    async def test_the_branch_pushed_is_the_one_named_whatever_head_is_on(
        self, worktree: Worktree, origin: Path, tmp_path: Path, bwrap: str
    ) -> None:
        """A session that moved its `HEAD` to `main` and committed there cannot move `main` through a push."""
        main_before = await run("git", "rev-parse", "refs/heads/main", cwd=origin)
        scratch = tmp_path / "pushing-scratch"
        scratch.mkdir()
        await ran(
            InAWorktree(worktree=worktree, scratch=scratch),
            bwrap,
            Venue.CONFINED,
            "git checkout -qB main origin/main && "
            "git -c user.email=probe@example.invalid -c user.name=probe commit -q --allow-empty -m 'onto main'",
            seconds=20,
        )
        recorded = await run("git", "rev-parse", f"refs/heads/{branch_named(PLANTED)}", cwd=worktree.root)

        came = await worktree.push(str(origin), branch_named(PLANTED))

        assert came.ok, came.err
        assert await run("git", "rev-parse", "refs/heads/main", cwd=origin) == main_before
        assert await run("git", "rev-parse", f"refs/heads/{branch_named(PLANTED)}", cwd=origin) == recorded

    async def test_a_branch_the_checkout_does_not_have_is_refused(self, worktree: Worktree, origin: Path) -> None:
        with pytest.raises(SnapshotFailed, match="nowhere-at-all"):
            await worktree.push(str(origin), "nowhere-at-all")

    async def test_pushing_runs_nothing_the_checkout_configured(
        self, worktree: Worktree, origin: Path, tmp_path: Path
    ) -> None:
        """The checkout's hooks are the session's; the push is the store's, and it has none."""
        escaped = tmp_path / "hooked"
        hook = worktree.root / ".git" / "hooks" / "pre-push"
        hook.write_text(f"#!/bin/sh\ntouch {escaped}\n")
        hook.chmod(0o755)

        came = await worktree.push(str(origin), branch_named(PLANTED))

        assert came.ok, came.err
        assert not escaped.exists()
