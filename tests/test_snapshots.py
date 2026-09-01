from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from calling import calling
from conftest import DEFAULT_CHOICE
from conftest import INSTRUCTIONS
from conftest import Provider
from conftest import already
from test_conversation import pass_at
from without_asgi import ASGIApp

from mainplate.agent import Choice
from mainplate.app import build_app
from mainplate.conversation import NoSuchRepository
from mainplate.conversation import choice_of
from mainplate.conversation import conversing
from mainplate.conversation import parse_tree
from mainplate.conversation import tree_key
from mainplate.forge import Clones
from mainplate.forge import Reachable
from mainplate.forge import Reaching
from mainplate.forge import Repository
from mainplate.forge import Workspaces
from mainplate.service import Service
from mainplate.settings import Settings
from mainplate.snapshots import SNAPSHOT_REF
from mainplate.snapshots import NotAWorkspace
from mainplate.snapshots import Workspace
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


# What a stand-in forge reaches, which is the repository above. `git clone` takes a path as
# readily as a URL, so a test needs no server to exercise the whole path a real session takes:
# reach a repository, clone it, plant a worktree of the clone.
FIXTURE = "test:fixture"


@pytest.fixture
async def workspaces(workspace: Workspace, tmp_path: Path) -> Workspaces:
    """
    Somewhere to clone the repository above and to plant each session's worktree of it.

    Both outside the repository deliberately, and these tests would not notice if they were not: a
    worktree planted *inside* it would be captured by the snapshots it exists to take, so every
    session would hold a copy of every other session's files.
    """
    reaching = Reaching(
        current=Reachable(
            repositories=(Repository(forge="test", key="fixture", name="me/fixture", url=str(workspace.root)),)
        )
    )
    return Workspaces(clones=Clones(root=tmp_path / "clones"), root=tmp_path / "worktrees", reaching=reaching)


class TestWorkingFromARelativeDatabase:
    """
    A console started from a working directory, which is how every foreground run starts one.

    The fixtures above hand `Clones` and `Worktrees` an absolute root, so nothing else here would
    notice a root that was relative. These two go the whole way from the setting: `git` is run with
    a `cwd` of the caller's choosing, so a destination that is not absolute is resolved somewhere
    nobody asked for, and the idempotence checks that look at the asked-for path then never fire.
    """

    async def test_a_clone_lands_where_it_was_asked_for_and_is_made_once(
        self, workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        root = Settings(database=Path("mainplate.db")).workspace_root
        clones = Clones(root=root / "clones")
        repository = Repository(forge="test", key="fixture", name="me/fixture", url=str(workspace.root))

        assert await clones.ensure(repository) == clones.at(repository.id)
        assert clones.cloned(repository.id), "the clone is where `at` says it is, not one level deeper"
        assert list(root.rglob("*.git")) == [clones.at(repository.id)]
        # The second pass, which is what a session's second turn does. Cloning again would fail on
        # a destination that already exists, so this is the assertion that `ensure` is idempotent
        # rather than merely written to look it.
        assert await clones.ensure(repository) == clones.at(repository.id)

    async def test_a_worktree_is_planted_outside_the_repository_and_only_once(
        self, workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        root = Settings(database=Path("mainplate.db")).workspace_root
        clones = Clones(root=root / "clones")
        repository = Repository(forge="test", key="fixture", name="me/fixture", url=str(workspace.root))
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
                {"prompt": "hello", "endpoint": "here", "model": "ripe/fast", "repository": FIXTURE},
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
                "/sessions", {"prompt": "hello", "endpoint": "here", "model": "ripe/fast", "repository": ""}
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
                {"prompt": "hello", "endpoint": "here", "model": "ripe/fast", "repository": "test:invented"},
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
                    "repository": FIXTURE,
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
        assert parse_tree(recorded[tree_key(0)]) == await workspaces.workspace(session.id).capture("the same tree")

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
        was = parse_tree((await planting.checkpointer.load(session.id))[tree_key(0)])

        (workspaces.at(session.id) / "src" / "kept.txt").write_text("changed since\n")
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
        self, planting: Service, provider: Provider, workspaces: Workspaces, on_fixture: Choice
    ) -> None:
        """A person editing between turns is the only writer there is until tools arrive."""
        body = conversing(provider.endpoints(), INSTRUCTIONS, workspaces)
        session = await planting.start("first", on_fixture)
        await pass_at(planting, body, session.id)

        (workspaces.at(session.id) / "src" / "kept.txt").write_text("they edited it\n")
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
