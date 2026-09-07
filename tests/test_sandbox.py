from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mainplate.sandbox import Filesystem
from mainplate.sandbox import InAWorktree
from mainplate.sandbox import Isolation
from mainplate.sandbox import OverEverything
from mainplate.sandbox import Sandbox
from mainplate.sandbox import Venue
from mainplate.sandbox import confined_by
from mainplate.sandbox import sandbox_command
from mainplate.snapshots import Worktree
from mainplate.tools.bash.tools import HEAD_LINES
from mainplate.tools.bash.tools import MAX_LINE
from mainplate.tools.bash.tools import TAIL_LINES
from mainplate.tools.bash.tools import bash_tools
from mainplate.tools.bash.tools import ran
from mainplate.tools.bash.tools import shortened


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
    """
    Where the sandbox binary is, and a loud failure if it is not anywhere.

    Not skipped when it is missing, for the same reason the browser tests are not: a check nobody
    runs is a check that catches nothing, and every assertion below is about what the sandbox
    actually does rather than about what this code believes it asks for.
    """
    return sandbox_command()


@pytest.fixture
async def worktree(tmp_path: Path) -> Worktree:
    """
    A **linked** worktree off a bare clone, which is the only shape this console ever makes.

    The shape is the point rather than scenery. A linked worktree's `.git` is a file holding an
    absolute pointer into the clone, so a sandbox that binds the worktree alone has no git in it at
    all, and every assertion here about what git can and cannot do would pass vacuously against a
    plain `git init` directory.
    """
    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    (source / "README.md").write_text("hi\n")
    (source / "src" / "app.py").write_text("print('hello')\n")
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
    """
    Where a session keeps what is not its repository's, beside the worktree rather than under it.

    Not created here: `ran` makes it, because bwrap will not bind a source that does not exist and
    a fixture that made it first would hide a tool that never did.
    """
    return tmp_path / "scratch" / "session"


async def inside(worktree: Worktree, scratch: Path, bwrap: str, command: str) -> str:
    """One command through the real tool, so these test what a session would actually get."""
    return await ran(InAWorktree(worktree=worktree, scratch=scratch), bwrap, Venue.CONFINED, command, seconds=20)


class TestWhereTheCloneIs:
    async def test_the_common_directory_is_what_is_bound_not_the_worktrees_own(
        self, worktree: Worktree, scratch: Path, tmp_path: Path
    ) -> None:
        """
        The distinction that decides whether git works in there at all.

        A linked worktree's own git directory sits *inside* the bare clone and points back out at it
        for objects and refs, so binding that one reaches neither the objects nor the refs. Binding
        the common one reaches both, because the other is underneath it.
        """
        sandbox = await confined_by(InAWorktree(worktree=worktree, scratch=scratch))
        own = Path(await run("git", "rev-parse", "--absolute-git-dir", cwd=worktree.root))

        tree, pointer, clone, kept = sandbox.places

        assert clone.path == tmp_path / "clones" / "fixture.git"
        assert clone.path in own.parents, "the worktree's own git directory is under the clone, not beside it"
        assert not clone.writable, "and it goes in read-only, which is what refuses a commit"
        assert tree.path == worktree.root
        assert tree.writable
        # After the tree and not before it: bwrap applies these in order, so a pointer bound first
        # would be covered by the writable tree that follows and protect nothing.
        assert pointer.path == worktree.root / ".git"
        assert not pointer.writable
        assert kept.path == scratch


class TestTheWorktreesPointerCannotBeReplaced:
    """
    That a command cannot swap `.git` for a repository of its own.

    The whole vector rests on it: `.git` in a linked worktree is a one-line pointer sitting in the
    one directory a session may write, and git reads the configuration of whatever it names, where
    several settings name a program git then runs. The control below runs the same commands in a
    sandbox built without the extra bind, so what these assert is the bind and not the filesystem.
    """

    async def unbound(self, worktree: Worktree, scratch: Path, bwrap: str, command: str) -> str:
        """
        The same sandbox with the pointer bind taken back out, which is what this had before.

        The scratch is made here because this does not go through `ran`, which is what normally
        makes it. bwrap refuses to bind a source that does not exist, so without this the control
        fails to start and its assertion reads as "the bind worked".
        """
        scratch.mkdir(parents=True, exist_ok=True)
        built = await confined_by(InAWorktree(worktree=worktree, scratch=scratch))
        without = Sandbox(places=tuple(each for each in built.places if each.path.name != ".git"))
        process = await asyncio.create_subprocess_exec(
            bwrap,
            *without.argv(at=str(worktree.root), venue=Venue.CONFINED),
            "/bin/sh",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await process.communicate()
        return out.decode()

    async def test_without_the_bind_a_command_can_plant_a_repository(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        """The control. Without it the four assertions below would hold on any read-only filesystem."""
        await self.unbound(worktree, scratch, bwrap, "rm -f .git && git init -q . && echo planted")

        assert (worktree.root / ".git").is_dir(), "the control has to actually succeed"

    async def test_the_pointer_cannot_be_removed(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        said = await inside(worktree, scratch, bwrap, "rm -f .git; echo done")

        assert (worktree.root / ".git").is_file()
        assert "busy" in said.lower() or "read-only" in said.lower()

    async def test_the_pointer_cannot_be_rewritten(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        was = (worktree.root / ".git").read_text()

        await inside(worktree, scratch, bwrap, "echo 'gitdir: /elsewhere' > .git; echo done")

        assert (worktree.root / ".git").read_text() == was

    async def test_the_pointer_cannot_be_moved_aside(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        """`rm` is not the only way to get a directory where a file was."""
        await inside(worktree, scratch, bwrap, "mv .git .gitold; echo done")

        assert (worktree.root / ".git").is_file()
        assert not (worktree.root / ".gitold").exists()

    async def test_the_bind_cannot_be_unmounted_from_inside(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        """A namespace the command is inside is not a namespace it may take apart."""
        said = await inside(worktree, scratch, bwrap, "umount .git 2>&1; echo done")

        assert "superuser" in said.lower() or "permitted" in said.lower() or "denied" in said.lower()
        assert (worktree.root / ".git").is_file()

    async def test_ordinary_work_is_untouched(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        """The cost of the bind, measured: a session still writes files and still asks git about them."""
        said = await inside(
            worktree, scratch, bwrap, "echo new > added.txt && git status --porcelain && git log --oneline"
        )

        assert "?? added.txt" in said
        assert "first" in said


class TestWhatTheVenueDecides:
    async def test_a_confined_command_gets_a_network_namespace_of_its_own(
        self, worktree: Worktree, scratch: Path
    ) -> None:
        sandbox = await confined_by(InAWorktree(worktree=worktree, scratch=scratch))

        assert "--unshare-net" in sandbox.argv(at=str(worktree.root), venue=Venue.CONFINED)

    async def test_a_connected_command_keeps_the_hosts_network(self, worktree: Worktree, scratch: Path) -> None:
        """The only difference between the two, so the rest of the policy cannot drift between them."""
        sandbox = await confined_by(InAWorktree(worktree=worktree, scratch=scratch))
        confined = sandbox.argv(at=str(worktree.root), venue=Venue.CONFINED)
        connected = sandbox.argv(at=str(worktree.root), venue=Venue.CONNECTED)

        assert "--unshare-net" not in connected
        assert [each for each in confined if each != "--unshare-net"] == list(connected)


class TestWhatACommandCanReach:
    async def test_it_can_write_in_the_worktree(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        said = await inside(worktree, scratch, bwrap, "echo written > new.txt && cat new.txt")

        assert "written" in said
        assert "exit 0" in said
        assert (worktree.root / "new.txt").read_text() == "written\n", "the write reached the real worktree"

    async def test_the_home_directory_does_not_exist_in_there(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        """
        The containment that matters most, since the home directory is where the keys are.

        Asserted against the real `$HOME` of whoever is running the suite rather than a planted
        stand-in, because what a stand-in would prove is that a path nobody has does not exist.
        """
        home = Path.home()
        assert home.is_dir(), "the control: this path really is there outside the sandbox"

        said = await inside(worktree, scratch, bwrap, f"ls {home} 2>&1 || echo DENIED")

        assert "DENIED" in said

    async def test_there_is_no_network(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        said = await inside(
            worktree, scratch, bwrap, "getent hosts example.com >/dev/null 2>&1 && echo REACHED || echo DENIED"
        )

        assert "DENIED" in said

    async def test_nothing_persists_between_two_calls(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        """
        The property the per-call shape buys, and the one a long-lived executor would take away.

        It is asserted rather than assumed because it is what the tool's own description promises,
        and a model that believed otherwise would chain commands that quietly lose their state.
        """
        await inside(worktree, scratch, bwrap, "echo transient > /tmp/left-behind")
        said = await inside(worktree, scratch, bwrap, "cat /tmp/left-behind 2>&1 || echo GONE")

        assert "GONE" in said


class TestTheScratchDirectory:
    """
    The half of the filesystem that outlives a call, and the reason it is not the worktree.

    Two things have to be true together and neither implies the other: what is written there is
    still there next call, and none of it is anything git will ever mention. A scratch inside the
    worktree would pass the first and fail the second, which is the shape this rules out.
    """

    async def test_it_is_made_on_demand_rather_than_planted(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        assert not scratch.exists(), "the control: nothing has made it yet"

        await inside(worktree, scratch, bwrap, "true")

        assert scratch.is_dir()

    async def test_what_is_written_there_survives_the_next_call(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        await inside(worktree, scratch, bwrap, f"echo kept > {scratch}/notes.txt")
        said = await inside(worktree, scratch, bwrap, f"cat {scratch}/notes.txt")

        assert "kept" in said
        assert "exit 0" in said

    async def test_it_is_reachable_by_name_rather_than_by_its_path(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        """
        The same absolute path under a name, so nothing has to reproduce a session id from memory.

        Asked by *writing through* the variable rather than by echoing it, because an environment
        variable that holds the right characters and points somewhere unreachable would pass the
        echo and fail the only thing it is for.
        """
        await inside(worktree, scratch, bwrap, 'echo named > "$MAINPLATE_SCRATCH/by-name.txt"')

        assert (scratch / "by-name.txt").read_text() == "named\n"

    async def test_the_worktree_is_named_too_so_a_command_can_come_back_to_it(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        # A command starts in the worktree, so this matters only once it has gone somewhere else,
        # which is exactly when a path is otherwise retyped.
        #
        # It leaves for `/` rather than for the scratch, and that is the difference between a test
        # and a test that passes anyway: `cd ""` leaves a shell exactly where it was, so a command
        # that had never left the worktree printed the worktree with the variable unset and proved
        # nothing. From `/` an unset variable prints `/`.
        said = await inside(worktree, scratch, bwrap, 'cd / && cd "$MAINPLATE_WORKTREE" && pwd')

        assert str(worktree.root) in said

    async def test_the_clone_is_not_named_because_nothing_should_be_writing_paths_into_it(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        # It is bound so that git works, not so that anybody addresses it. A name would invite a
        # write to the one place the read-only bind exists to refuse.
        said = await inside(worktree, scratch, bwrap, "env | grep -c MAINPLATE_ || true")

        assert "2" in said, "the worktree and the scratch, and nothing else"

    async def test_nothing_in_it_reaches_git(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        """
        What a scratch directory under the worktree would get wrong.

        `list` passes `--others`, so an untracked directory inside the worktree is in every listing
        and every `status` until something excludes it, and the only place to write that exclusion
        is a git directory this command cannot write to.
        """
        await inside(worktree, scratch, bwrap, f"mkdir -p {scratch}/cache && echo x > {scratch}/cache/blob")

        said = await inside(worktree, scratch, bwrap, "git status --porcelain; git ls-files --others")

        assert "scratch" not in said
        assert "cache" not in said


class TestWhatGitCanDoInThere:
    """
    Both directions of the mount policy, which is the pair no single assertion covers.

    Bound too tightly and git is not there at all, which silently takes `list` with it. Bound too
    loosely and the agent can rewrite the history that the snapshots are chained onto. Each of these
    passes under exactly one of the three policies, so together they pin the one that is right.
    """

    async def test_reading_git_works(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        said = await inside(worktree, scratch, bwrap, "git ls-files")

        assert "README.md" in said
        assert "src/app.py" in said
        assert "exit 0" in said

    async def test_status_and_log_work(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        (worktree.root / "README.md").write_text("hi\nedited\n")

        said = await inside(worktree, scratch, bwrap, "git status --porcelain && git log --oneline")

        assert "M README.md" in said
        assert "first" in said

    async def test_committing_is_refused(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        (worktree.root / "README.md").write_text("hi\nedited\n")

        said = await inside(worktree, scratch, bwrap, "git add -A && git commit -m 'from the agent'")

        assert "Read-only file system" in said
        assert "exit 0" not in said

    async def test_stashing_is_refused_and_the_work_survives(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        """
        `git stash` reads as safe and is the sharpest case against a command allowlist.

        Outside a sandbox it reverts every tracked edit, which here would be the turns since the
        last snapshot, and leaves untracked files in place so what is left is a mixture no snapshot
        describes. The assertion is on the worktree rather than on the message: what matters is that
        the work is still on disk.
        """
        (worktree.root / "README.md").write_text("hi\nedited\n")

        said = await inside(worktree, scratch, bwrap, "git stash")

        assert "Read-only file system" in said
        assert (worktree.root / "README.md").read_text() == "hi\nedited\n", "the edit is still there"


class TestWhatABashCallSaysBack:
    async def test_a_command_that_worked_says_so(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        """
        Success is stated rather than left to be inferred from an empty answer.

        A model reading no output and no status cannot tell "it worked and printed nothing" from
        "it never ran", and runs the thing a second time.
        """
        said = await inside(worktree, scratch, bwrap, "true")

        assert said.endswith("exit 0")
        assert "$ true" in said

    async def test_a_command_that_failed_carries_its_code_and_its_output(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        said = await inside(worktree, scratch, bwrap, "echo trouble >&2; exit 3")

        assert "trouble" in said, "stderr is folded in rather than dropped"
        assert "exit 3" in said

    async def test_an_empty_command_is_something_to_ask_again(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        with pytest.raises(ValueError, match="a command to run is required"):
            await inside(worktree, scratch, bwrap, "   ")

    async def test_a_command_that_runs_too_long_is_stopped(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        with pytest.raises(ValueError, match="ran longer than"):
            await ran(InAWorktree(worktree=worktree, scratch=scratch), bwrap, Venue.CONFINED, "sleep 30", seconds=1)

    def test_the_toolset_offers_exactly_one_tool_under_the_name_the_model_sees(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        """
        The name is what a model writes, so a rename is a silently broken call rather than an error.

        What a refusal *becomes* is deliberately not asserted here. It is the same three lines the
        file tools' `guarded` already carries a test for, and reaching it would mean building a
        `RunContext` to drive a library dataclass whose shape is not ours to depend on.
        """
        confinement = InAWorktree(worktree=worktree, scratch=scratch)

        assert set(bash_tools(confinement, bwrap, Venue.CONFINED).tools) == {"bash"}


class TestHowMuchOutputComesBack:
    def test_a_short_result_comes_back_whole(self) -> None:
        assert shortened("one\ntwo\nthree") == "one\ntwo\nthree"

    def test_a_long_result_keeps_both_ends_and_counts_the_middle(self) -> None:
        """
        Head and tail rather than a head alone, because a build says what it was doing at the top
        and what went wrong at the bottom.
        """
        lines = [f"line {at}" for at in range(HEAD_LINES + TAIL_LINES + 500)]

        said = shortened("\n".join(lines))

        assert "line 0" in said
        assert lines[-1] in said
        assert "500 more lines" in said
        assert f"line {HEAD_LINES + 250}" not in said

    def test_one_enormous_line_is_cut_with_its_length(self) -> None:
        """A line cap as well as a line count, since one minified bundle is a single line."""
        said = shortened("x" * (MAX_LINE + 40))

        assert said.startswith("x" * 80)
        assert "40 more characters" in said


class TestSettlingTheTwoAxes:
    """
    The filesystem answer is not free of the repository, and this is where that is made true.

    Not checked at read time and not reconciled by any consumer: a contradictory pair is simply never
    recorded, because the one place a session is created settles it first.
    """

    @pytest.mark.parametrize(
        "asked",
        [
            pytest.param(Filesystem.NOTHING, id="a form that named no files"),
            pytest.param(Filesystem.EVERYTHING, id="a form that named the whole machine"),
            pytest.param(Filesystem.WORKTREE, id="a form that already named the worktree"),
        ],
    )
    def test_a_repository_forces_the_worktree_whatever_was_asked(self, asked: Filesystem) -> None:
        settled = Isolation(filesystem=asked, network=True).settled("exe-github:blog")

        assert settled.filesystem is Filesystem.WORKTREE
        assert settled.network, "and the network answer is untouched, because the axes are independent"

    def test_without_a_repository_the_worktree_becomes_no_files(self) -> None:
        """
        The direction that stops a form widening a session by naming something it cannot have.

        `NOTHING` rather than a refusal, because the honest reading of "a worktree" with no
        repository is the state a session with no repository already had.
        """
        settled = Isolation(filesystem=Filesystem.WORKTREE).settled(None)

        assert settled.filesystem is Filesystem.NOTHING

    def test_the_other_two_are_left_alone_without_a_repository(self) -> None:
        for asked in (Filesystem.NOTHING, Filesystem.EVERYTHING):
            assert Isolation(filesystem=asked).settled(None).filesystem is asked

    def test_the_network_answer_becomes_the_sandboxs_own_word(self) -> None:
        assert Isolation(network=False).venue is Venue.CONFINED
        assert Isolation(network=True).venue is Venue.CONNECTED


class TestReachingTheWholeMachine:
    async def test_it_binds_the_root_read_write(self) -> None:
        (only,) = Sandbox.everywhere().places

        assert only.path == Path("/")
        assert only.writable

    async def test_a_command_sees_outside_any_worktree(self, bwrap: str) -> None:
        """
        What choosing it means, asserted rather than described.

        Against the real home directory, because a planted stand-in would only prove that a path
        nobody has is visible. `TestWhatACommandCanReach` asserts the same path is *denied* under a
        worktree, so the pair is what says the setting does anything.
        """
        home = Path.home()

        said = await ran(OverEverything(), bwrap, Venue.CONFINED, f"ls {home} >/dev/null && echo VISIBLE", seconds=20)

        assert "VISIBLE" in said

    async def test_the_network_is_still_off_unless_the_session_asked(self, bwrap: str) -> None:
        """
        The reason `EVERYTHING` is still a sandbox rather than no sandbox.

        Dropping it for this arm would take the network switch with it, so the whole machine would
        silently imply the whole internet and one of the two axes would stop being expressible.
        """
        reaching = "getent hosts example.com >/dev/null 2>&1 && echo REACHED || echo DENIED"

        assert "DENIED" in await ran(OverEverything(), bwrap, Venue.CONFINED, reaching, seconds=20)
