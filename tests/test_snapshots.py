from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from calling import calling
from conftest import DEFAULT_CHOICE
from conftest import FIXTURE
from conftest import INSTRUCTIONS
from conftest import Provider
from conftest import Scripted
from conftest import already
from conftest import calls
from conftest import run
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
from mainplate.service import Service
from mainplate.settings import Settings
from mainplate.snapshots import SNAPSHOT_REF
from mainplate.snapshots import NotAWorktree
from mainplate.snapshots import Worktree
from mainplate.snapshots import branch_named


class TestWorkingFromARelativeDatabase:
    """
    A console started from a working directory, which is how every foreground run starts one.

    The fixtures above hand `Clones` and `Worktrees` an absolute root, so nothing else here would
    notice a root that was relative. These two go the whole way from the setting: `git` is run with
    a `cwd` of the caller's choosing, so a destination that is not absolute is resolved somewhere
    nobody asked for, and the idempotence checks that look at the asked-for path then never fire.
    """

    async def test_a_clone_lands_where_it_was_asked_for_and_is_made_once(
        self, worktree: Worktree, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        root = Settings(database=Path("mainplate.db")).workspace_root
        clones = Clones(root=root / "clones")
        repository = Repository(forge="test", key="fixture", name="me/fixture", url=str(worktree.root))

        assert await clones.ensure(repository) == clones.at(repository.id)
        assert clones.cloned(repository.id), "the clone is where `at` says it is, not one level deeper"
        assert list(root.rglob("*.git")) == [clones.at(repository.id)]
        # The second pass, which is what a session's second turn does. Cloning again would fail on
        # a destination that already exists, so this is the assertion that `ensure` is idempotent
        # rather than merely written to look it.
        assert await clones.ensure(repository) == clones.at(repository.id)

    async def test_a_worktree_is_planted_outside_the_repository_and_only_once(
        self, worktree: Worktree, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        root = Settings(database=Path("mainplate.db")).workspace_root
        clones = Clones(root=root / "clones")
        repository = Repository(forge="test", key="fixture", name="me/fixture", url=str(worktree.root))
        await clones.ensure(repository)
        worktrees = clones.worktrees(repository.id, root / "worktrees")

        planted = await worktrees.plant("a" * 32)
        assert planted.root == worktrees.at("a" * 32)
        assert planted.root.is_dir()
        # Outside the clone, which is the property the doubling breaks: a worktree resolved against
        # the repository's own directory would be captured by the snapshots it exists to take.
        assert not planted.root.is_relative_to(clones.at(repository.id))
        # `git worktree list` names the bare repository itself alongside its linked worktrees, so
        # this asks whether the planted one is in that answer rather than what the whole answer is.
        # It is the same question `plant` asks to decide whether it has anything to do.
        assert planted.root in await worktrees.planted()
        assert (await worktrees.plant("a" * 32)).root == planted.root


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
        was = await worktree.tip()

        again = await worktree.capture("second")

        assert await worktree.tip() == was
        assert again == await worktree.demand("rev-parse", f"{was}^{{tree}}")

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
        assert await run("git", "branch", "--format=%(refname:short)", cwd=worktree.root) == "main"

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
        first = await worktree.capture("first")
        (worktree.root / "src" / "kept.txt").write_text("second\n")
        await worktree.capture("second")

        await run("git", "reflog", "expire", "--expire=now", "--all", cwd=worktree.root)
        await run("git", "gc", "--prune=now", "-q", cwd=worktree.root)

        assert await worktree.paths(first) == (".gitignore", "src/kept.txt")

    async def test_every_snapshot_is_reachable_through_the_one_ref(self, worktree: Worktree) -> None:
        await worktree.capture("first")
        (worktree.root / "src" / "kept.txt").write_text("second\n")
        await worktree.capture("second")

        chain = await run("git", "rev-list", SNAPSHOT_REF, cwd=worktree.root)

        assert len(chain.splitlines()) == 2


class TestCapturingAtOnce:
    async def test_two_captures_in_flight_together_each_describe_a_real_tree(self, worktree: Worktree) -> None:
        """
        Two captures overlapping must not share a staging file.

        A single shadow index per worktree is one file that two `git add -A` runs write over each
        other, and the loser's `write-tree` then describes a tree that never existed. It is not a
        hypothetical: a worker answering several sessions over one worktree does exactly this.
        """
        trees = await asyncio.gather(*(worktree.capture(f"at once {n}") for n in range(6)))

        assert len(set(trees)) == 1, "nothing changed between them, so every capture is the same tree"
        for tree in trees:
            assert await worktree.paths(tree) == (".gitignore", "src/kept.txt")

    async def test_concurrent_captures_leave_no_staging_files_behind(self, worktree: Worktree) -> None:
        """
        The shadow index is a temporary file per operation, so every one of them has to be cleaned
        up. Asserted over a batch rather than a single capture, because what leaks one file leaks
        six, and a worker answering several sessions over one worktree is the case that produces it.
        """
        await asyncio.gather(*(worktree.capture(f"at once {n}") for n in range(6)))

        left = sorted(path.name for path in (worktree.root / ".git").glob("mainplate-index-*"))
        assert left == []


class TestAWorktreePerSession:
    async def test_creating_a_session_clones_nothing(
        self, planting: Service, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """
        Somebody is waiting on that request and a clone is a network fetch. The session exists,
        renders, and names its repository; the files arrive when the first pass runs.
        """
        session = await planting.start("hello", on_fixture)

        assert not workspaces.clones.cloned(FIXTURE)
        assert not workspaces.at(session.id).exists()

    async def test_the_first_pass_clones_the_repository_and_plants_the_worktree(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await planting.start("hello", on_fixture)

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
        one = await planting.start("first", on_fixture)
        two = await planting.start("second", on_fixture)
        await pass_at(planting, body, one.id)
        await pass_at(planting, body, two.id)

        (workspaces.at(one.id) / "src" / "only-mine.txt").write_text("mine\n")

        assert not (workspaces.at(two.id) / "src" / "only-mine.txt").exists()

    async def test_a_second_session_reuses_the_clone_rather_than_making_another(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        one = await planting.start("first", on_fixture)
        two = await planting.start("second", on_fixture)
        await pass_at(planting, body, one.id)
        await pass_at(planting, body, two.id)

        assert [each.name for each in workspaces.clones.root.iterdir()] == [workspaces.clones.at(FIXTURE).name]

    async def test_a_later_pass_keeps_the_work_in_a_worktree_already_planted(
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """Every pass reaches the planting; re-planting would throw away what the session had done."""
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await planting.start("hello", on_fixture)
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
        session = await planting.start("hello", replace(DEFAULT_CHOICE, repository="test:long-gone"))

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
        session = await planting.start("hello", on_fixture)

        await pass_at(planting, body, session.id)

        assert (workspaces.at(session.id) / "src" / "kept.txt").read_text() == "original\n"

    async def test_a_session_started_at_a_tag_holds_the_files_that_tag_names(
        self, planting: Service, provider: Provider, workspaces: Workspaces, worktree: Worktree
    ) -> None:
        """
        The point of naming one: the worktree holds what was there *then*, not what is there now.

        A tag rather than a branch, so that what is asserted is a resolution git had to perform
        rather than a name that happens to be `HEAD` anyway.
        """
        await run("git", "tag", "before-the-rewrite", cwd=worktree.root)
        (worktree.root / "src" / "kept.txt").write_text("rewritten\n")
        await run("git", "commit", "-aqm", "second", cwd=worktree.root)
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await planting.start("hello", replace(DEFAULT_CHOICE, repository=FIXTURE, base="before-the-rewrite"))

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
        worktree: Worktree,
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
        first = await planting.start("hello", on_fixture)
        await pass_at(planting, body, first.id)
        assert workspaces.clones.cloned(FIXTURE), "the clone is what goes stale, so it has to exist first"
        (worktree.root / "src" / "kept.txt").write_text("moved on\n")
        await run("git", "commit", "-aqm", "second", cwd=worktree.root)

        second = await planting.start("hello", replace(on_fixture, base=base))
        await pass_at(planting, body, second.id)

        assert (workspaces.at(second.id) / "src" / "kept.txt").read_text() == "moved on\n"

    async def test_a_fork_is_the_files_its_turn_saw_however_far_the_repository_has_moved(
        self, planting: Service, provider: Provider, workspaces: Workspaces, worktree: Worktree, on_fixture: Choice
    ) -> None:
        """
        The one plant that must *not* pick up whatever the remote now says.

        A fork is checked out at a tree this console recorded, which is an object it already holds, so
        a fetch there could not change the answer - and a fork that started at the current head would
        be re-asking its turn against files that turn never saw, which is a different question wearing
        the same words.
        """
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await planting.start("hello", on_fixture)
        await pass_at(planting, body, session.id)
        (worktree.root / "src" / "kept.txt").write_text("moved on\n")
        await run("git", "commit", "-aqm", "second", cwd=worktree.root)

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
        session = await planting.start("hello", replace(on_fixture, branch="try-it-this-way"))

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
        session = await planting.start("hello", on_fixture)

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
        one = await planting.start("first", on_fixture)
        two = await planting.start("second", on_fixture)

        await pass_at(planting, body, one.id)
        await pass_at(planting, body, two.id)

        on_one = await run("git", "branch", "--show-current", cwd=workspaces.at(one.id))
        on_two = await run("git", "branch", "--show-current", cwd=workspaces.at(two.id))
        assert on_one != on_two
        assert (on_one, on_two) == (branch_named(one.id), branch_named(two.id))

    async def test_a_branch_somebody_named_wins_over_the_one_this_console_would_make(
        self, planting: Service, on_fixture: Choice
    ) -> None:
        session = await planting.start("hello", replace(on_fixture, branch="try-it-this-way"))

        chosen = choice_of(await planting.checkpointer.load(session.id))
        assert chosen is not None
        assert chosen.branch == "try-it-this-way"

    async def test_a_session_with_no_repository_is_given_no_branch(self, planting: Service) -> None:
        """There is nothing for one to be a branch *of*, which `Choice.branching` answers first."""
        session = await planting.start("hello", replace(DEFAULT_CHOICE, repository=None))

        chosen = choice_of(await planting.checkpointer.load(session.id))
        assert chosen is not None
        assert chosen.branch is None

    async def test_a_session_with_no_repository_records_neither_a_base_nor_a_branch(self, planting: Service) -> None:
        """
        `Choice.settled`, which is what makes the start form unable to express a contradiction: a
        base and a branch are answers about a repository, so with none picked there is nothing for
        either to be about and the record says so rather than carrying words nothing will ever read.
        """
        session = await planting.start(
            "hello", replace(DEFAULT_CHOICE, repository=None, base="main", branch="somewhere")
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
        started = replace(on_fixture, base="main", branch="the-parent-s-branch")
        session = await planting.start("hello", started)
        await pass_at(planting, body, session.id)

        # With a message, because planting happens *after* the pass is told what to answer: a fork
        # left waiting never reaches the call this is about.
        forked = await planting.fork(session.id, at=1, chosen=started, said="try it again")
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

    async def test_the_branches_are_read_without_cloning_anything(
        self, workspaces: Workspaces, worktree: Worktree
    ) -> None:
        """
        The whole reason it asks the remote. There is no clone before a session's first pass, so
        reading one would leave the very first session on a repository - the case where you most want
        to say where to start - as the one with nothing to offer.
        """
        await run("git", "branch", "release/2.1", cwd=worktree.root)

        found = await workspaces.branches(FIXTURE)

        assert sorted(found) == ["main", "release/2.1"]
        assert not workspaces.clones.cloned(FIXTURE), "asking cost no clone"

    async def test_a_branch_pushed_since_the_clone_is_offered(
        self, planting: Service, provider: Provider, workspaces: Workspaces, worktree: Worktree, on_fixture: Choice
    ) -> None:
        """The other half of asking the remote: the answer cannot be as old as the local copy."""
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await planting.start("hello", on_fixture)
        await pass_at(planting, body, session.id)
        await run("git", "branch", "landed-later", cwd=worktree.root)

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
        session = await planting.start("first", on_fixture)
        await pass_at(planting, body, session.id)

        # What turn 1 will see, and then a later edit that turn 1 never saw.
        (workspaces.at(session.id) / "src" / "kept.txt").write_text("as turn one saw it\n")
        await planting.say(session.id, turn=1, said="second")
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
        session = await planting.start("first", on_fixture)
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
        session = await planting.start("first", on_fixture)

        forked = await planting.fork(session.id, at=0, chosen=on_fixture, said="again")
        assert forked is not None
        await pass_at(planting, body, forked.id)

        assert (workspaces.at(forked.id) / "src" / "kept.txt").read_text() == "original\n"

    async def forked_into(self, planting: Service, started: Choice, asked: str | None) -> str | None:
        """The repository a fork ends up in, having started from `started` and asked for `asked`."""
        session = await planting.start("first", started)
        forked = await planting.fork(session.id, at=0, chosen=replace(started, repository=asked), said="again")
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

    async def test_the_picker_offers_what_the_forges_reach(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/")

        assert 'value="test:fixture"' in answered.text
        assert ">me/fixture<" in answered.text

    async def test_a_posted_repository_is_recorded_on_the_session(self, app: ASGIApp, planting: Service) -> None:
        async with calling(app) as caller:
            answered = await caller.post(
                "/sessions",
                {"prompt": "hello", "endpoint": "here", "model": "ripe/fast", "workspace": FIXTURE},
            )

        assert answered.status == 303
        started = answered.location.rsplit("/", 1)[-1]
        chosen = choice_of(await planting.checkpointer.load(started))
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
        session = await planting.start("first", replace(DEFAULT_CHOICE, repository=FIXTURE))

        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session.id}/forks/new?at=0")

        assert answered.status == 200
        assert 'id="repository"' not in answered.text

    async def test_the_fork_page_offers_one_to_a_session_that_has_none(self, app: ASGIApp, planting: Service) -> None:
        """The other half of the rule: a fork may attach a repository where there was none."""
        session = await planting.start("first", DEFAULT_CHOICE)

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
        session = await planting.start("first", replace(DEFAULT_CHOICE, repository=FIXTURE))

        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session.id}")

        assert answered.status == 200
        assert '<span class="where" title="me/fixture">me/fixture</span>' in answered.text

    async def test_a_row_for_a_session_working_in_nothing_says_nothing(self, app: ASGIApp, planting: Service) -> None:
        """Most of a list is one or the other, and the majority does not need labelling."""
        session = await planting.start("first", DEFAULT_CHOICE)

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
        session = await planting.start("first", replace(DEFAULT_CHOICE, repository="test:detached"))

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
        session = await planting.start("first", replace(DEFAULT_CHOICE, repository=FIXTURE))

        held = await planting.database.run(
            lambda connection: {str(row[1]) for row in connection.execute("PRAGMA table_info(sessions)")}
        )
        assert "repository" not in held

        listed = await planting.listed()
        assert [one.repository for one in listed if one.id == session.id] == [FIXTURE]

    async def test_attaching_a_repository_through_the_fork_form_works(self, app: ASGIApp, planting: Service) -> None:
        session = await planting.start("first", DEFAULT_CHOICE)

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
        session = await planting.start("first", replace(DEFAULT_CHOICE, repository=FIXTURE))

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
        session = await planting.start("hello", on_fixture)

        await pass_at(planting, body, session.id)

        recorded = await planting.checkpointer.load(session.id)
        assert parse_tree(recorded[opening_tree_key(0)]) == await workspaces.worktree(session.id).capture(
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
        session = await planting.start("hello", on_fixture)
        await pass_at(planting, body, session.id)
        was = parse_tree((await planting.checkpointer.load(session.id))[opening_tree_key(0)])

        (workspaces.at(session.id) / "src" / "kept.txt").write_text("changed since\n")
        await pass_at(planting, body, session.id)

        assert parse_tree((await planting.checkpointer.load(session.id))[opening_tree_key(0)]) == was

    async def test_a_console_with_no_repository_records_that_it_had_none(
        self, service: Service, provider: Provider
    ) -> None:
        """Distinguishable from a turn nobody has reached, which has no key at all."""
        session = await service.start("hello", DEFAULT_CHOICE)

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
        session = await planting.start("hello", on_fixture)

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
        session = await planting.start("hello", on_fixture)

        await pass_at(planting, conversing(scripted.endpoints(), INSTRUCTIONS, workspaces), session.id)

        recorded = await planting.checkpointer.load(session.id)
        after = parse_tree(recorded[tree_key(0, 1)])
        assert after is not None
        held = await workspaces.worktree(session.id).paths(after)
        assert "src/added.txt" in held
        assert "src/added.txt" not in await workspaces.worktree(session.id).paths(
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
        session = await planting.start("hello", on_fixture)
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
        session = await planting.start("first", on_fixture)
        await pass_at(planting, body, session.id)

        (workspaces.at(session.id) / "src" / "kept.txt").write_text("they edited it\n")
        await planting.say(session.id, turn=1, said="second")
        await pass_at(planting, body, session.id)

        recorded = await planting.checkpointer.load(session.id)
        assert parse_tree(recorded[opening_tree_key(0)]) != parse_tree(recorded[opening_tree_key(1)])


class TestRefusingWhatIsNotAWorkspace:
    async def test_a_directory_that_is_not_a_repository_is_refused_by_name(self, tmp_path: Path) -> None:
        bare = tmp_path / "not-a-repo"
        bare.mkdir()

        with pytest.raises(NotAWorktree, match=str(bare)):
            await Worktree(root=bare).confirm()

    async def test_a_real_worktree_is_accepted(self, worktree: Worktree) -> None:
        await worktree.confirm()
