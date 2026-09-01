from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import DEFAULT_CHOICE
from conftest import INSTRUCTIONS
from conftest import Provider
from test_conversation import pass_at

from mainplate.conversation import conversing
from mainplate.conversation import parse_tree
from mainplate.conversation import tree_key
from mainplate.service import Service
from mainplate.snapshots import SNAPSHOT_REF
from mainplate.snapshots import NotAWorkspace
from mainplate.snapshots import Workspace
from mainplate.snapshots import Worktrees
from mainplate.snapshots import deletions


async def run(*arguments: str, cwd: Path) -> str:
    process = await asyncio.create_subprocess_exec(
        *arguments, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"{arguments} failed: {out.decode()}")
    return out.decode().strip()


@pytest.fixture
async def workspace(tmp_path: Path) -> Workspace:
    """
    A real repository, because everything worth checking here is what git actually does.

    A stand-in for git would be a second implementation of the thing under test, and the questions
    these tests ask - does a gitignored file come across, does the reader's index move, does a tree
    survive `gc` - are exactly the ones only git can answer.
    """
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    await run("git", "init", "-q", "-b", "main", cwd=root)
    await run("git", "config", "user.email", "probe@example.invalid", cwd=root)
    await run("git", "config", "user.name", "probe", cwd=root)
    (root / ".gitignore").write_text(".env\nbuilt/\n")
    (root / "src" / "kept.txt").write_text("original\n")
    (root / ".env").write_text("SECRET=shh\n")
    (root / "built").mkdir()
    (root / "built" / "artifact.bin").write_text("generated\n")
    await run("git", "add", "-A", cwd=root)
    await run("git", "commit", "-qm", "first", cwd=root)
    return Workspace(root=root)


@pytest.fixture
async def worktrees(workspace: Workspace, tmp_path: Path) -> Worktrees:
    """
    The repository above, with somewhere outside it for each session's worktree.

    Outside deliberately, and the tests below would not notice if it were not: a worktree planted
    *inside* the repository would be captured by the snapshots it exists to take, so every session
    would hold a copy of every other session's files.
    """
    return Worktrees(repo=workspace.root, root=tmp_path / "worktrees")


@pytest.fixture
async def planting(service: Service, worktrees: Worktrees) -> Service:
    """The service, with worktrees, so starting a session plants one and forking checks one out."""
    return replace(service, worktrees=worktrees)


class TestDecidingWhatToRemove:
    """Pure, and the half of a restore most easily got wrong: `checkout-index` deletes nothing."""

    def test_a_path_the_target_does_not_have_is_removed(self) -> None:
        assert deletions(["a.py", "b.py"], ["a.py"]) == ("b.py",)

    def test_a_path_both_have_is_left_alone(self) -> None:
        assert deletions(["a.py", "b.py"], ["a.py", "b.py"]) == ()

    def test_a_path_only_the_target_has_is_not_a_deletion(self) -> None:
        """Writing it is `checkout-index`'s job; this is only about what to take away."""
        assert deletions(["a.py"], ["a.py", "restored.py"]) == ()


class TestCapturing:
    async def test_a_capture_is_a_tree_that_holds_the_tracked_files(self, workspace: Workspace) -> None:
        tree = await workspace.capture("first")

        assert await workspace.paths(tree) == (".gitignore", "src/kept.txt")

    async def test_an_ignored_file_is_not_captured(self, workspace: Workspace) -> None:
        """
        The decision this module is built around. It is what makes going back to a turn keep the
        thing you installed between then and now, and what stops a snapshot carrying a secret.
        """
        held = await workspace.paths(await workspace.capture("first"))

        assert ".env" not in held
        assert not any(path.startswith("built/") for path in held)

    async def test_changing_a_file_changes_the_tree(self, workspace: Workspace) -> None:
        was = await workspace.capture("before")
        (workspace.root / "src" / "kept.txt").write_text("edited\n")

        assert await workspace.capture("after") != was

    async def test_an_unchanged_worktree_writes_no_new_commit(self, workspace: Workspace) -> None:
        """The dedupe: cost tracks what changed rather than how often this is called."""
        await workspace.capture("first")
        was = await workspace.tip()

        again = await workspace.capture("second")

        assert await workspace.tip() == was
        assert again == await workspace.demand("rev-parse", f"{was}^{{tree}}")

    async def test_an_untracked_file_is_captured(self, workspace: Workspace) -> None:
        """`-A` rather than `-u`: a file the agent has just written is not yet tracked."""
        (workspace.root / "src" / "new.txt").write_text("fresh\n")

        assert "src/new.txt" in await workspace.paths(await workspace.capture("with a new file"))


class TestLeavingTheReaderAlone:
    async def test_capturing_does_not_stage_anything_in_the_reader_s_index(self, workspace: Workspace) -> None:
        (workspace.root / "src" / "kept.txt").write_text("edited but not staged\n")

        await workspace.capture("while they were working")

        assert await run("git", "diff", "--cached", "--name-only", cwd=workspace.root) == ""

    async def test_capturing_moves_no_branch_and_leaves_no_branch_behind(self, workspace: Workspace) -> None:
        was = await run("git", "rev-parse", "HEAD", cwd=workspace.root)

        await workspace.capture("a snapshot")

        assert await run("git", "rev-parse", "HEAD", cwd=workspace.root) == was
        assert await run("git", "branch", "--format=%(refname:short)", cwd=workspace.root) == "main"

    async def test_snapshots_do_not_show_up_in_the_log(self, workspace: Workspace) -> None:
        await workspace.capture("a snapshot")

        logged = await run("git", "log", "--oneline", cwd=workspace.root)

        assert len(logged.splitlines()) == 1, "the repository's own commit, and no snapshot beside it"


class TestSurvivingCollection:
    async def test_a_tree_is_still_there_after_an_aggressive_gc(self, workspace: Workspace) -> None:
        """
        Why the trees are chained into commits under a ref at all. A bare `write-tree` produces a
        hash nothing refers to, and the next `gc` prunes it: the checkpoint would hold a tree that
        no longer resolves, which is a rewind that fails long after the change that broke it.
        """
        first = await workspace.capture("first")
        (workspace.root / "src" / "kept.txt").write_text("second\n")
        await workspace.capture("second")

        await run("git", "reflog", "expire", "--expire=now", "--all", cwd=workspace.root)
        await run("git", "gc", "--prune=now", "-q", cwd=workspace.root)

        assert await workspace.paths(first) == (".gitignore", "src/kept.txt")

    async def test_every_snapshot_is_reachable_through_the_one_ref(self, workspace: Workspace) -> None:
        await workspace.capture("first")
        (workspace.root / "src" / "kept.txt").write_text("second\n")
        await workspace.capture("second")

        chain = await run("git", "rev-list", SNAPSHOT_REF, cwd=workspace.root)

        assert len(chain.splitlines()) == 2


class TestCapturingAtOnce:
    async def test_two_captures_in_flight_together_each_describe_a_real_tree(self, workspace: Workspace) -> None:
        """
        Two captures overlapping must not share a staging file.

        A single shadow index per workspace is one file that two `git add -A` runs write over each
        other, and the loser's `write-tree` then describes a tree that never existed. It is not a
        hypothetical: a worker answering several sessions over one workspace does exactly this.
        """
        trees = await asyncio.gather(*(workspace.capture(f"at once {n}") for n in range(6)))

        assert len(set(trees)) == 1, "nothing changed between them, so every capture is the same tree"
        for tree in trees:
            assert await workspace.paths(tree) == (".gitignore", "src/kept.txt")

    async def test_a_capture_beside_a_listing_leaves_no_staging_files_behind(self, workspace: Workspace) -> None:
        await asyncio.gather(workspace.capture("one"), workspace.living(), workspace.capture("two"))

        left = sorted(path.name for path in (workspace.root / ".git").glob("mainplate-index-*"))
        assert left == []


class TestRestoring:
    async def test_a_changed_file_goes_back(self, workspace: Workspace) -> None:
        tree = await workspace.capture("before")
        (workspace.root / "src" / "kept.txt").write_text("edited\n")

        await workspace.restore(tree)

        assert (workspace.root / "src" / "kept.txt").read_text() == "original\n"

    async def test_a_file_added_after_the_snapshot_is_removed(self, workspace: Workspace) -> None:
        """`checkout-index` writes what a tree holds and removes nothing, so this is the second pass."""
        tree = await workspace.capture("before")
        (workspace.root / "src" / "added.txt").write_text("later\n")

        await workspace.restore(tree)

        assert not (workspace.root / "src" / "added.txt").exists()

    async def test_an_ignored_file_written_after_the_snapshot_survives_the_restore(self, workspace: Workspace) -> None:
        """
        The motivating case, stated as a test: install the missing thing, go back to before the
        call, and the install is still there.
        """
        tree = await workspace.capture("before")
        (workspace.root / "built" / "installed.bin").write_text("the thing you installed\n")

        await workspace.restore(tree)

        assert (workspace.root / "built" / "installed.bin").exists()

    async def test_restoring_stages_nothing_in_the_reader_s_index(self, workspace: Workspace) -> None:
        tree = await workspace.capture("before")
        (workspace.root / "src" / "kept.txt").write_text("edited\n")

        await workspace.restore(tree)

        assert await run("git", "diff", "--cached", "--name-only", cwd=workspace.root) == ""


class TestAWorktreePerSession:
    async def test_starting_a_session_plants_it_a_worktree_of_its_own(
        self, planting: Service, worktrees: Worktrees
    ) -> None:
        session = await planting.start("hello", DEFAULT_CHOICE)

        assert worktrees.at(session.id) in await worktrees.planted()
        assert (worktrees.at(session.id) / "src" / "kept.txt").read_text() == "original\n"

    async def test_two_sessions_do_not_see_each_other_s_files(self, planting: Service, worktrees: Worktrees) -> None:
        """
        The reason for a worktree apiece. Two writers in one directory make a snapshot
        unattributable, and the person is always one of the two.
        """
        one = await planting.start("first", DEFAULT_CHOICE)
        two = await planting.start("second", DEFAULT_CHOICE)

        (worktrees.at(one.id) / "src" / "only-mine.txt").write_text("mine\n")

        assert not (worktrees.at(two.id) / "src" / "only-mine.txt").exists()

    async def test_planting_a_worktree_that_is_already_there_keeps_the_work_in_it(
        self, planting: Service, worktrees: Worktrees
    ) -> None:
        """Re-planting would either refuse the request or throw away what a session had done."""
        session = await planting.start("hello", DEFAULT_CHOICE)
        (worktrees.at(session.id) / "src" / "in-progress.txt").write_text("half done\n")

        await worktrees.plant(session.id)

        assert (worktrees.at(session.id) / "src" / "in-progress.txt").read_text() == "half done\n"


class TestForkingTheWorktreeToo:
    async def test_a_fork_starts_from_the_files_the_forked_turn_saw(
        self, planting: Service, provider: Provider, worktrees: Worktrees
    ) -> None:
        """
        The consistency the whole thing is for. Turn 1 ran against one state of the files; forking
        turn 1 has to re-ask it against *that* state, or the new model is answering a different
        question in the same words and nothing in the transcript would say so.
        """
        body = conversing(provider.endpoints(), INSTRUCTIONS, worktrees)
        session = await planting.start("first", DEFAULT_CHOICE)
        await pass_at(planting, body, session.id)

        # What turn 1 will see, and then a later edit that turn 1 never saw.
        (worktrees.at(session.id) / "src" / "kept.txt").write_text("as turn one saw it\n")
        await planting.say(session.id, turn=1, said="second")
        await pass_at(planting, body, session.id)
        (worktrees.at(session.id) / "src" / "kept.txt").write_text("changed long after\n")

        forked = await planting.fork(session.id, at=1, chosen=DEFAULT_CHOICE, said="second, differently")

        assert forked is not None
        assert (worktrees.at(forked.id) / "src" / "kept.txt").read_text() == "as turn one saw it\n"

    async def test_the_parent_s_worktree_is_untouched_by_being_forked(
        self, planting: Service, provider: Provider, worktrees: Worktrees
    ) -> None:
        body = conversing(provider.endpoints(), INSTRUCTIONS, worktrees)
        session = await planting.start("first", DEFAULT_CHOICE)
        await pass_at(planting, body, session.id)
        (worktrees.at(session.id) / "src" / "kept.txt").write_text("where the parent is now\n")

        await planting.fork(session.id, at=0, chosen=DEFAULT_CHOICE, said="again")

        assert (worktrees.at(session.id) / "src" / "kept.txt").read_text() == "where the parent is now\n"

    async def test_a_fork_of_a_session_that_had_no_workspace_starts_where_the_repository_is(
        self, planting: Service, worktrees: Worktrees
    ) -> None:
        """No recorded tree is not a failure to handle; it is a session that never had one."""
        session = await planting.start("first", DEFAULT_CHOICE)

        forked = await planting.fork(session.id, at=0, chosen=DEFAULT_CHOICE, said="again")

        assert forked is not None
        assert (worktrees.at(forked.id) / "src" / "kept.txt").read_text() == "original\n"


class TestWhatATurnRecords:
    async def test_a_turn_records_the_tree_of_its_own_worktree(
        self, planting: Service, provider: Provider, worktrees: Worktrees
    ) -> None:
        body = conversing(provider.endpoints(), INSTRUCTIONS, worktrees)
        session = await planting.start("hello", DEFAULT_CHOICE)

        await pass_at(planting, body, session.id)

        recorded = await planting.checkpointer.load(session.id)
        assert parse_tree(recorded[tree_key(0)]) == await worktrees.workspace(session.id).capture("the same tree")

    async def test_a_later_pass_replays_the_recorded_tree_rather_than_reading_the_worktree_again(
        self, planting: Service, provider: Provider, worktrees: Worktrees
    ) -> None:
        """
        The rule the mechanism asks for. Reading a worktree answers differently every time it is
        asked, so a pass that re-read it would resume a conversation against a directory that has
        moved since; a recorded step hands back what the first pass saw.
        """
        body = conversing(provider.endpoints(), INSTRUCTIONS, worktrees)
        session = await planting.start("hello", DEFAULT_CHOICE)
        await pass_at(planting, body, session.id)
        was = parse_tree((await planting.checkpointer.load(session.id))[tree_key(0)])

        (worktrees.at(session.id) / "src" / "kept.txt").write_text("changed since\n")
        await pass_at(planting, body, session.id)

        assert parse_tree((await planting.checkpointer.load(session.id))[tree_key(0)]) == was

    async def test_a_console_with_no_repository_records_that_it_had_none(
        self, service: Service, provider: Provider
    ) -> None:
        """Distinguishable from a turn nobody has reached, which has no key at all."""
        session = await service.start("hello", DEFAULT_CHOICE)

        await pass_at(service, conversing(provider.endpoints(), INSTRUCTIONS, None), session.id)

        recorded = await service.checkpointer.load(session.id)
        assert tree_key(0) in recorded
        assert parse_tree(recorded[tree_key(0)]) is None

    async def test_two_turns_edited_between_record_different_trees(
        self, planting: Service, provider: Provider, worktrees: Worktrees
    ) -> None:
        """A person editing between turns is the only writer there is until tools arrive."""
        body = conversing(provider.endpoints(), INSTRUCTIONS, worktrees)
        session = await planting.start("first", DEFAULT_CHOICE)
        await pass_at(planting, body, session.id)

        (worktrees.at(session.id) / "src" / "kept.txt").write_text("they edited it\n")
        await planting.say(session.id, turn=1, said="second")
        await pass_at(planting, body, session.id)

        recorded = await planting.checkpointer.load(session.id)
        assert parse_tree(recorded[tree_key(0)]) != parse_tree(recorded[tree_key(1)])


class TestRefusingWhatIsNotAWorkspace:
    async def test_a_directory_that_is_not_a_repository_is_refused_by_name(self, tmp_path: Path) -> None:
        bare = tmp_path / "not-a-repo"
        bare.mkdir()

        with pytest.raises(NotAWorkspace, match=str(bare)):
            await Workspace(root=bare).confirm()

    async def test_a_real_worktree_is_accepted(self, workspace: Workspace) -> None:
        await workspace.confirm()
