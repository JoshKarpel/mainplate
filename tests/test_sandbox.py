from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mainplate.sandbox import Sandbox
from mainplate.sandbox import Venue
from mainplate.sandbox import sandbox_command
from mainplate.snapshots import Workspace
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
async def workspace(tmp_path: Path) -> Workspace:
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
    return Workspace(root=planted)


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    """
    Where a session keeps what is not its repository's, beside the worktree rather than under it.

    Not created here: `ran` makes it, because bwrap will not bind a source that does not exist and
    a fixture that made it first would hide a tool that never did.
    """
    return tmp_path / "scratch" / "session"


async def inside(workspace: Workspace, scratch: Path, bwrap: str, command: str) -> str:
    """One command through the real tool, so these test what a session would actually get."""
    return await ran(workspace, scratch, bwrap, command, seconds=20)


class TestWhereTheCloneIs:
    async def test_the_common_directory_is_what_is_bound_not_the_worktrees_own(
        self, workspace: Workspace, scratch: Path, tmp_path: Path
    ) -> None:
        """
        The distinction that decides whether git works in there at all.

        A linked worktree's own git directory sits *inside* the bare clone and points back out at it
        for objects and refs, so binding that one reaches neither the objects nor the refs. Binding
        the common one reaches both, because the other is underneath it.
        """
        sandbox = await Sandbox.around(workspace, scratch)
        own = Path(await run("git", "rev-parse", "--absolute-git-dir", cwd=workspace.root))

        assert sandbox.clone == tmp_path / "clones" / "fixture.git"
        assert sandbox.clone in own.parents, "the worktree's own git directory is under the clone, not beside it"
        assert sandbox.worktree == workspace.root


class TestWhatTheVenueDecides:
    async def test_a_confined_command_gets_a_network_namespace_of_its_own(
        self, workspace: Workspace, scratch: Path
    ) -> None:
        sandbox = await Sandbox.around(workspace, scratch)

        assert "--unshare-net" in sandbox.argv(at=str(workspace.root), venue=Venue.CONFINED)

    async def test_a_connected_command_keeps_the_hosts_network(self, workspace: Workspace, scratch: Path) -> None:
        """The only difference between the two, so the rest of the policy cannot drift between them."""
        sandbox = await Sandbox.around(workspace, scratch)
        confined = sandbox.argv(at=str(workspace.root), venue=Venue.CONFINED)
        connected = sandbox.argv(at=str(workspace.root), venue=Venue.CONNECTED)

        assert "--unshare-net" not in connected
        assert [each for each in confined if each != "--unshare-net"] == list(connected)


class TestWhatACommandCanReach:
    async def test_it_can_write_in_the_worktree(self, workspace: Workspace, scratch: Path, bwrap: str) -> None:
        said = await inside(workspace, scratch, bwrap, "echo written > new.txt && cat new.txt")

        assert "written" in said
        assert "exit 0" in said
        assert (workspace.root / "new.txt").read_text() == "written\n", "the write reached the real worktree"

    async def test_the_home_directory_does_not_exist_in_there(
        self, workspace: Workspace, scratch: Path, bwrap: str
    ) -> None:
        """
        The containment that matters most, since the home directory is where the keys are.

        Asserted against the real `$HOME` of whoever is running the suite rather than a planted
        stand-in, because what a stand-in would prove is that a path nobody has does not exist.
        """
        home = Path.home()
        assert home.is_dir(), "the control: this path really is there outside the sandbox"

        said = await inside(workspace, scratch, bwrap, f"ls {home} 2>&1 || echo DENIED")

        assert "DENIED" in said

    async def test_there_is_no_network(self, workspace: Workspace, scratch: Path, bwrap: str) -> None:
        said = await inside(
            workspace, scratch, bwrap, "getent hosts example.com >/dev/null 2>&1 && echo REACHED || echo DENIED"
        )

        assert "DENIED" in said

    async def test_nothing_persists_between_two_calls(self, workspace: Workspace, scratch: Path, bwrap: str) -> None:
        """
        The property the per-call shape buys, and the one a long-lived executor would take away.

        It is asserted rather than assumed because it is what the tool's own description promises,
        and a model that believed otherwise would chain commands that quietly lose their state.
        """
        await inside(workspace, scratch, bwrap, "echo transient > /tmp/left-behind")
        said = await inside(workspace, scratch, bwrap, "cat /tmp/left-behind 2>&1 || echo GONE")

        assert "GONE" in said


class TestTheScratchDirectory:
    """
    The half of the filesystem that outlives a call, and the reason it is not the worktree.

    Two things have to be true together and neither implies the other: what is written there is
    still there next call, and none of it is anything git will ever mention. A scratch inside the
    worktree would pass the first and fail the second, which is the shape this rules out.
    """

    async def test_it_is_made_on_demand_rather_than_planted(
        self, workspace: Workspace, scratch: Path, bwrap: str
    ) -> None:
        assert not scratch.exists(), "the control: nothing has made it yet"

        await inside(workspace, scratch, bwrap, "true")

        assert scratch.is_dir()

    async def test_what_is_written_there_survives_the_next_call(
        self, workspace: Workspace, scratch: Path, bwrap: str
    ) -> None:
        await inside(workspace, scratch, bwrap, f"echo kept > {scratch}/notes.txt")
        said = await inside(workspace, scratch, bwrap, f"cat {scratch}/notes.txt")

        assert "kept" in said
        assert "exit 0" in said

    async def test_nothing_in_it_reaches_git(self, workspace: Workspace, scratch: Path, bwrap: str) -> None:
        """
        What a scratch directory under the worktree would get wrong.

        `list` passes `--others`, so an untracked directory inside the worktree is in every listing
        and every `status` until something excludes it, and the only place to write that exclusion
        is a git directory this command cannot write to.
        """
        await inside(workspace, scratch, bwrap, f"mkdir -p {scratch}/cache && echo x > {scratch}/cache/blob")

        said = await inside(workspace, scratch, bwrap, "git status --porcelain; git ls-files --others")

        assert "scratch" not in said
        assert "cache" not in said


class TestWhatGitCanDoInThere:
    """
    Both directions of the mount policy, which is the pair no single assertion covers.

    Bound too tightly and git is not there at all, which silently takes `list` with it. Bound too
    loosely and the agent can rewrite the history that the snapshots are chained onto. Each of these
    passes under exactly one of the three policies, so together they pin the one that is right.
    """

    async def test_reading_git_works(self, workspace: Workspace, scratch: Path, bwrap: str) -> None:
        said = await inside(workspace, scratch, bwrap, "git ls-files")

        assert "README.md" in said
        assert "src/app.py" in said
        assert "exit 0" in said

    async def test_status_and_log_work(self, workspace: Workspace, scratch: Path, bwrap: str) -> None:
        (workspace.root / "README.md").write_text("hi\nedited\n")

        said = await inside(workspace, scratch, bwrap, "git status --porcelain && git log --oneline")

        assert "M README.md" in said
        assert "first" in said

    async def test_committing_is_refused(self, workspace: Workspace, scratch: Path, bwrap: str) -> None:
        (workspace.root / "README.md").write_text("hi\nedited\n")

        said = await inside(workspace, scratch, bwrap, "git add -A && git commit -m 'from the agent'")

        assert "Read-only file system" in said
        assert "exit 0" not in said

    async def test_stashing_is_refused_and_the_work_survives(
        self, workspace: Workspace, scratch: Path, bwrap: str
    ) -> None:
        """
        `git stash` reads as safe and is the sharpest case against a command allowlist.

        Outside a sandbox it reverts every tracked edit, which here would be the turns since the
        last snapshot, and leaves untracked files in place so what is left is a mixture no snapshot
        describes. The assertion is on the worktree rather than on the message: what matters is that
        the work is still on disk.
        """
        (workspace.root / "README.md").write_text("hi\nedited\n")

        said = await inside(workspace, scratch, bwrap, "git stash")

        assert "Read-only file system" in said
        assert (workspace.root / "README.md").read_text() == "hi\nedited\n", "the edit is still there"


class TestWhatABashCallSaysBack:
    async def test_a_command_that_worked_says_so(self, workspace: Workspace, scratch: Path, bwrap: str) -> None:
        """
        Success is stated rather than left to be inferred from an empty answer.

        A model reading no output and no status cannot tell "it worked and printed nothing" from
        "it never ran", and runs the thing a second time.
        """
        said = await inside(workspace, scratch, bwrap, "true")

        assert said.endswith("exit 0")
        assert "$ true" in said

    async def test_a_command_that_failed_carries_its_code_and_its_output(
        self, workspace: Workspace, scratch: Path, bwrap: str
    ) -> None:
        said = await inside(workspace, scratch, bwrap, "echo trouble >&2; exit 3")

        assert "trouble" in said, "stderr is folded in rather than dropped"
        assert "exit 3" in said

    async def test_an_empty_command_is_something_to_ask_again(
        self, workspace: Workspace, scratch: Path, bwrap: str
    ) -> None:
        with pytest.raises(ValueError, match="a command to run is required"):
            await inside(workspace, scratch, bwrap, "   ")

    async def test_a_command_that_runs_too_long_is_stopped(
        self, workspace: Workspace, scratch: Path, bwrap: str
    ) -> None:
        with pytest.raises(ValueError, match="ran longer than"):
            await ran(workspace, scratch, bwrap, "sleep 30", seconds=1)

    def test_the_toolset_offers_exactly_one_tool_under_the_name_the_model_sees(
        self, workspace: Workspace, scratch: Path, bwrap: str
    ) -> None:
        """
        The name is what a model writes, so a rename is a silently broken call rather than an error.

        What a refusal *becomes* is deliberately not asserted here. It is the same three lines the
        file tools' `guarded` already carries a test for, and reaching it would mean building a
        `RunContext` to drive a library dataclass whose shape is not ours to depend on.
        """
        assert set(bash_tools(workspace, scratch, bwrap).tools) == {"bash"}


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
