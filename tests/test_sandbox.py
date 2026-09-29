from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from conftest import PLANTED

from mainplate.sandbox import Filesystem
from mainplate.sandbox import InAScratch
from mainplate.sandbox import InAWorktree
from mainplate.sandbox import Isolation
from mainplate.sandbox import OverEverything
from mainplate.sandbox import Sandbox
from mainplate.sandbox import Venue
from mainplate.sandbox import confined_by
from mainplate.sandbox import home_in
from mainplate.sandbox import starting_at
from mainplate.snapshots import Worktree
from mainplate.snapshots import branch_named
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


class TestWhatAScratchOnlySessionReaches:
    """
    A session with no repository gets its scratch and nothing else of the machine.

    Through the real tool and the real namespace, for the reason everything here is: what is
    asserted is what a mount namespace with one bind in it actually does.
    """

    async def alone(self, scratch: Path, bwrap: str, command: str) -> str:
        return await ran(InAScratch(scratch=scratch), bwrap, Venue.CONFINED, command, seconds=20)

    async def test_a_command_starts_in_the_scratch_which_is_also_home_and_named(
        self, scratch: Path, bwrap: str
    ) -> None:
        said = await self.alone(scratch, bwrap, 'echo "$PWD"; echo "$HOME"; echo "$MAINPLATE_SCRATCH"')
        assert said.count(str(scratch)) == 3, said

    async def test_the_scratch_is_made_by_the_first_command_and_kept_for_the_next(
        self, scratch: Path, bwrap: str
    ) -> None:
        assert not scratch.exists(), "the fixture leaves making it to the tool"
        await self.alone(scratch, bwrap, "echo kept > note.txt")
        assert (scratch / "note.txt").read_text() == "kept\n"
        assert "kept" in await self.alone(scratch, bwrap, "cat note.txt")

    async def test_nothing_of_the_machine_is_in_there(self, scratch: Path, bwrap: str, worktree: Worktree) -> None:
        """A worktree that exists on this machine is exactly the kind of thing a scratch-only session must not see."""
        said = await self.alone(scratch, bwrap, f"ls {worktree.root} >/dev/null 2>&1 && echo VISIBLE || echo DENIED")
        assert "DENIED" in said
        said = await self.alone(scratch, bwrap, 'echo "worktree=${MAINPLATE_WORKTREE:-unset}"')
        assert "worktree=unset" in said

    async def test_a_scratch_only_sandbox_binds_the_scratch_and_nothing_else(self, scratch: Path) -> None:
        sandbox = confined_by(InAScratch(scratch=scratch))
        assert [(bind.path, bind.writable, bind.name) for bind in sandbox.places] == [(scratch, True, "scratch")]
        assert starting_at(InAScratch(scratch=scratch)) == scratch
        assert home_in(InAScratch(scratch=scratch)) == scratch


class TestWhereTheWorktreeIs:
    async def test_the_worktree_and_scratch_are_writable_and_the_store_is_read_only(
        self, worktree: Worktree, scratch: Path
    ) -> None:
        sandbox = confined_by(InAWorktree(worktree=worktree, scratch=scratch))

        assert [(bind.path, bind.writable) for bind in sandbox.places] == [
            (worktree.root, True),
            (worktree.store.path, False),
            (scratch, True),
        ]

    async def test_git_this_console_runs_sees_the_worktree_as_a_command_does(
        self, worktree: Worktree, scratch: Path
    ) -> None:
        """
        A capture that bound the worktree or its store differently from the command that wrote them
        would be reading another repository than the one the session worked in, and saying nothing.
        """
        commands = confined_by(InAWorktree(worktree=worktree, scratch=scratch)).places

        assert worktree.confined().places == commands[:2]

    async def test_git_writes_work_and_stay_in_the_worktree(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        """
        What owning its `.git` buys a session: commit, branch and rebase, as git does them anywhere.

        Read back through the store for the other half: nothing a session's git did reached it.
        """
        before = await worktree.store.demand("for-each-ref", "--format=%(refname) %(objectname)")

        said = await inside(
            worktree,
            scratch,
            bwrap,
            "echo edited >> src/kept.txt && git commit -qam second && git branch topic && git log -1 --format=%s",
        )

        assert "second" in said
        assert await run("git", "branch", "--show-current", cwd=worktree.root) == branch_named(PLANTED)
        assert "topic" in await run("git", "branch", "--format=%(refname:short)", cwd=worktree.root)
        assert await worktree.store.demand("for-each-ref", "--format=%(refname) %(objectname)") == before

    async def test_the_store_cannot_be_written_from_in_there(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        said = await inside(
            worktree, scratch, bwrap, f"touch {worktree.store.path}/planted 2>&1 && echo WROTE || echo DENIED"
        )

        assert "DENIED" in said
        assert not (worktree.store.path / "planted").exists()

    async def test_fetching_brings_the_stores_refreshed_branches_without_a_network(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        said = await inside(worktree, scratch, bwrap, "git fetch -q origin && git branch -r")

        assert "origin/main" in said


class TestWhatTheVenueDecides:
    async def test_a_confined_command_gets_a_network_namespace_of_its_own(
        self, worktree: Worktree, scratch: Path
    ) -> None:
        sandbox = confined_by(InAWorktree(worktree=worktree, scratch=scratch))

        assert "--unshare-net" in sandbox.argv(at=str(worktree.root), venue=Venue.CONFINED)

    async def test_a_connected_command_keeps_the_hosts_network(self, worktree: Worktree, scratch: Path) -> None:
        """The only difference between the two, so the rest of the policy cannot drift between them."""
        sandbox = confined_by(InAWorktree(worktree=worktree, scratch=scratch))
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

    async def test_it_is_the_home_directory_of_every_command(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        """
        The scratch rather than the tmpfs, because that is where a toolchain the repository's setup
        installed keeps what it fetched, and a `$HOME` anywhere else is a shell that cannot find its
        own tools. Asked by writing through it, for the reason the name below is.
        """
        await inside(worktree, scratch, bwrap, 'echo home > "$HOME/dotfile"')

        assert (scratch / "dotfile").read_text() == "home\n"

    async def test_what_the_setup_recorded_is_set_for_every_command(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        """
        And set whole, after everything the sandbox sets itself, so a `PATH` a setup recorded is the
        `PATH` rather than a fragment of one.
        """
        said = await ran(
            InAWorktree(worktree=worktree, scratch=scratch),
            bwrap,
            Venue.CONFINED,
            'echo "$GREETING" && echo "$PATH"',
            seconds=20,
            environment={"GREETING": "hi there", "PATH": f"{scratch}/bin:/usr/bin:/bin"},
        )

        assert "hi there" in said
        assert f"{scratch}/bin:/usr/bin:/bin" in said

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

    async def test_the_store_is_not_named_because_nothing_should_be_writing_paths_into_it(
        self, worktree: Worktree, scratch: Path, bwrap: str
    ) -> None:
        # It is bound so that borrowed objects resolve, not so that anybody addresses it. A name
        # would invite a write to the one place the read-only bind exists to refuse.
        said = await inside(worktree, scratch, bwrap, "env | grep -c MAINPLATE_ || true")

        assert "2" in said, "the worktree and the scratch, and nothing else"

    async def test_nothing_in_it_reaches_git(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        """
        What a scratch directory under the worktree would get wrong.

        `list` passes `--others`, so an untracked directory inside the worktree is in every listing
        and every `status` until something excludes it, and excluding it would mean an entry in the
        session's own `.git/info/exclude` that anything the session runs could take out again.
        """
        await inside(worktree, scratch, bwrap, f"mkdir -p {scratch}/cache && echo x > {scratch}/cache/blob")

        said = await inside(worktree, scratch, bwrap, "git status --porcelain; git ls-files --others")

        assert "scratch" not in said
        assert "cache" not in said


class TestWhatGitCanDoInThere:
    """Ordinary local git works in there, because this worktree's `.git` belongs to this session."""

    async def test_reading_git_works(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        said = await inside(worktree, scratch, bwrap, "git ls-files")

        assert ".gitignore" in said
        assert "src/kept.txt" in said
        assert "exit 0" in said

    async def test_status_and_log_work(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        (worktree.root / "src" / "kept.txt").write_text("edited\n")

        said = await inside(worktree, scratch, bwrap, "git status --porcelain && git log --oneline")

        assert "M src/kept.txt" in said
        assert "first" in said

    async def test_committing_works(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        (worktree.root / "src" / "kept.txt").write_text("edited\n")

        said = await inside(worktree, scratch, bwrap, "git add -A && git commit -m 'from the agent'")

        assert "exit 0" in said
        assert await run("git", "log", "-1", "--format=%s", cwd=worktree.root) == "from the agent"

    async def test_stashing_works(self, worktree: Worktree, scratch: Path, bwrap: str) -> None:
        (worktree.root / "src" / "kept.txt").write_text("edited\n")

        said = await inside(worktree, scratch, bwrap, "git stash && git stash list")

        assert "stash@{0}" in said
        assert (worktree.root / "src" / "kept.txt").read_text() == "original\n"


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
