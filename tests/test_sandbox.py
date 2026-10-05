from __future__ import annotations

import asyncio
import shlex
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from conftest import PLANTED

from mainplate.sandbox import Filesystem
from mainplate.sandbox import InACheckout
from mainplate.sandbox import InAScratch
from mainplate.sandbox import Isolation
from mainplate.sandbox import OverEverything
from mainplate.sandbox import Sandbox
from mainplate.sandbox import Venue
from mainplate.sandbox import confined_by
from mainplate.sandbox import home_in
from mainplate.sandbox import starting_at
from mainplate.snapshots import Checkout
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
    Where a session keeps what is not its repository's, beside the checkout rather than under it.

    Not created here: `ran` makes it, because bwrap will not bind a source that does not exist and
    a fixture that made it first would hide a tool that never did.
    """
    return tmp_path / "scratch" / "session"


@pytest.fixture
async def local_server() -> AsyncIterator[str]:
    """
    A command that reads a line from a server on the runner's own loopback, with no network beyond it.

    A connected command running it is the control for the same client and the same address a
    confined one is refused at, which proves the namespace on a runner with no routes and no DNS,
    such as a session of this console with its own network off. A lookup of a public name would pass
    there whether or not the namespace existed.
    """
    tasks: set[asyncio.Task[None]] = set()

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Close each accepted connection after the line, so the client reads through to EOF."""
        try:
            writer.write(b"mainplate loopback control\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def accepted(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Hold each handler until teardown, so a passing assertion cannot leave a socket behind."""
        tasks.add(asyncio.create_task(respond(reader, writer)))

    server = await asyncio.start_server(accepted, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    script = (
        "import socket; "
        f"connection = socket.create_connection(('127.0.0.1', {port}), timeout=1); "
        "print(connection.makefile().read(), end=''); connection.close()"
    )
    try:
        yield f"python3 -c {shlex.quote(script)}"
    finally:
        server.close()
        await server.wait_closed()
        if tasks:
            await asyncio.gather(*tasks)


async def inside(checkout: Checkout, scratch: Path, bwrap: str, command: str) -> str:
    """One command through the real tool, so these test what a session would actually get."""
    return await ran(InACheckout(checkout=checkout, scratch=scratch), bwrap, Venue.CONFINED, command, seconds=20)


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

    async def test_nothing_of_the_machine_is_in_there(self, scratch: Path, bwrap: str, checkout: Checkout) -> None:
        """A checkout that exists on this machine is exactly the kind of thing a scratch-only session must not see."""
        said = await self.alone(scratch, bwrap, f"ls {checkout.root} >/dev/null 2>&1 && echo VISIBLE || echo DENIED")
        assert "DENIED" in said
        said = await self.alone(scratch, bwrap, 'echo "checkout=${MAINPLATE_CHECKOUT:-unset}"')
        assert "checkout=unset" in said

    async def test_a_scratch_only_sandbox_binds_the_scratch_and_nothing_else(self, scratch: Path) -> None:
        sandbox = confined_by(InAScratch(scratch=scratch))
        assert [(bind.path, bind.writable, bind.name) for bind in sandbox.places] == [(scratch, True, "scratch")]
        assert starting_at(InAScratch(scratch=scratch)) == scratch
        assert home_in(InAScratch(scratch=scratch)) == scratch


class TestWhereTheCheckoutIs:
    async def test_the_checkout_and_scratch_are_writable_and_the_store_is_read_only(
        self, checkout: Checkout, scratch: Path
    ) -> None:
        sandbox = confined_by(InACheckout(checkout=checkout, scratch=scratch))

        assert [(bind.path, bind.writable) for bind in sandbox.places] == [
            (checkout.root, True),
            (checkout.store.path, False),
            (scratch, True),
        ]

    async def test_git_this_console_runs_sees_the_checkout_as_a_command_does(
        self, checkout: Checkout, scratch: Path
    ) -> None:
        """
        A capture that bound the checkout or its store differently from the command that wrote them
        would be reading another repository than the one the session worked in, and saying nothing.
        """
        commands = confined_by(InACheckout(checkout=checkout, scratch=scratch)).places

        assert checkout.confined().places == commands[:2]

    async def test_git_writes_work_and_stay_in_the_checkout(
        self, checkout: Checkout, scratch: Path, bwrap: str
    ) -> None:
        """
        What owning its `.git` buys a session: commit, branch and rebase, as git does them anywhere.

        Read back through the store for the other half: nothing a session's git did reached it.
        """
        before = await checkout.store.demand("for-each-ref", "--format=%(refname) %(objectname)")

        said = await inside(
            checkout,
            scratch,
            bwrap,
            "echo edited >> src/kept.txt && git commit -qam second && git branch topic && git log -1 --format=%s",
        )

        assert "second" in said
        assert await run("git", "branch", "--show-current", cwd=checkout.root) == branch_named(PLANTED)
        assert "topic" in await run("git", "branch", "--format=%(refname:short)", cwd=checkout.root)
        assert await checkout.store.demand("for-each-ref", "--format=%(refname) %(objectname)") == before

    async def test_the_store_cannot_be_written_from_in_there(
        self, checkout: Checkout, scratch: Path, bwrap: str
    ) -> None:
        said = await inside(
            checkout, scratch, bwrap, f"touch {checkout.store.path}/planted 2>&1 && echo WROTE || echo DENIED"
        )

        assert "DENIED" in said
        assert not (checkout.store.path / "planted").exists()

    async def test_fetching_brings_the_stores_refreshed_branches_without_a_network(
        self, checkout: Checkout, scratch: Path, bwrap: str
    ) -> None:
        said = await inside(checkout, scratch, bwrap, "git fetch -q origin && git branch -r")

        assert "origin/main" in said


class TestWhatTheVenueDecides:
    async def test_a_confined_command_gets_a_network_namespace_of_its_own(
        self, checkout: Checkout, scratch: Path
    ) -> None:
        sandbox = confined_by(InACheckout(checkout=checkout, scratch=scratch))

        assert "--unshare-net" in sandbox.argv(at=str(checkout.root), venue=Venue.CONFINED)

    async def test_a_connected_command_keeps_the_hosts_network(self, checkout: Checkout, scratch: Path) -> None:
        """The only difference between the two, so the rest of the policy cannot drift between them."""
        sandbox = confined_by(InACheckout(checkout=checkout, scratch=scratch))
        confined = sandbox.argv(at=str(checkout.root), venue=Venue.CONFINED)
        connected = sandbox.argv(at=str(checkout.root), venue=Venue.CONNECTED)

        assert "--unshare-net" not in connected
        assert [each for each in confined if each != "--unshare-net"] == list(connected)


class TestWhatACommandCanReach:
    async def test_it_can_write_in_the_checkout(self, checkout: Checkout, scratch: Path, bwrap: str) -> None:
        said = await inside(checkout, scratch, bwrap, "echo written > new.txt && cat new.txt")

        assert "written" in said
        assert "exit 0" in said
        assert (checkout.root / "new.txt").read_text() == "written\n", "the write reached the real checkout"

    async def test_the_home_directory_does_not_exist_in_there(
        self, checkout: Checkout, scratch: Path, bwrap: str
    ) -> None:
        """
        The containment that matters most, since the home directory is where the keys are.

        Asserted against the real `$HOME` of whoever is running the suite rather than a planted
        stand-in, because what a stand-in would prove is that a path nobody has does not exist.
        """
        home = Path.home()
        assert home.is_dir(), "the control: this path really is there outside the sandbox"

        said = await inside(checkout, scratch, bwrap, f"ls {home} 2>&1 || echo DENIED")

        assert "DENIED" in said

    @pytest.mark.network
    async def test_there_is_no_network(self, checkout: Checkout, scratch: Path, bwrap: str, local_server: str) -> None:
        """A server on the runner's loopback, reached from the host's namespace and refused from a new one."""
        place = InACheckout(checkout=checkout, scratch=scratch)
        connected = await ran(place, bwrap, Venue.CONNECTED, local_server, seconds=20)
        assert "mainplate loopback control\n" in connected, "the control: the server is there to reach"

        confined = await ran(place, bwrap, Venue.CONFINED, local_server, seconds=20)

        assert "ConnectionRefusedError" in confined

    async def test_nothing_persists_between_two_calls(self, checkout: Checkout, scratch: Path, bwrap: str) -> None:
        """
        The property the per-call shape buys, and the one a long-lived executor would take away.

        It is asserted rather than assumed because it is what the tool's own description promises,
        and a model that believed otherwise would chain commands that quietly lose their state.
        """
        await inside(checkout, scratch, bwrap, "echo transient > /tmp/left-behind")
        said = await inside(checkout, scratch, bwrap, "cat /tmp/left-behind 2>&1 || echo GONE")

        assert "GONE" in said


class TestTheScratchDirectory:
    """
    The half of the filesystem that outlives a call, and the reason it is not the checkout.

    Two things have to be true together and neither implies the other: what is written there is
    still there next call, and none of it is anything git will ever mention. A scratch inside the
    checkout would pass the first and fail the second, which is the shape this rules out.
    """

    async def test_it_is_made_on_demand_rather_than_planted(
        self, checkout: Checkout, scratch: Path, bwrap: str
    ) -> None:
        assert not scratch.exists(), "the control: nothing has made it yet"

        await inside(checkout, scratch, bwrap, "true")

        assert scratch.is_dir()

    async def test_what_is_written_there_survives_the_next_call(
        self, checkout: Checkout, scratch: Path, bwrap: str
    ) -> None:
        await inside(checkout, scratch, bwrap, f"echo kept > {scratch}/notes.txt")
        said = await inside(checkout, scratch, bwrap, f"cat {scratch}/notes.txt")

        assert "kept" in said
        assert "exit 0" in said

    async def test_it_is_the_home_directory_of_every_command(
        self, checkout: Checkout, scratch: Path, bwrap: str
    ) -> None:
        """
        The scratch rather than the tmpfs, because that is where a toolchain the repository's setup
        installed keeps what it fetched, and a `$HOME` anywhere else is a shell that cannot find its
        own tools. Asked by writing through it, for the reason the name below is.
        """
        await inside(checkout, scratch, bwrap, 'echo home > "$HOME/dotfile"')

        assert (scratch / "dotfile").read_text() == "home\n"

    async def test_what_the_setup_recorded_is_set_for_every_command(
        self, checkout: Checkout, scratch: Path, bwrap: str
    ) -> None:
        """
        And set whole, after everything the sandbox sets itself, so a `PATH` a setup recorded is the
        `PATH` rather than a fragment of one.
        """
        said = await ran(
            InACheckout(checkout=checkout, scratch=scratch),
            bwrap,
            Venue.CONFINED,
            'echo "$GREETING" && echo "$PATH"',
            seconds=20,
            environment={"GREETING": "hi there", "PATH": f"{scratch}/bin:/usr/bin:/bin"},
        )

        assert "hi there" in said
        assert f"{scratch}/bin:/usr/bin:/bin" in said

    async def test_it_is_reachable_by_name_rather_than_by_its_path(
        self, checkout: Checkout, scratch: Path, bwrap: str
    ) -> None:
        """
        The same absolute path under a name, so nothing has to reproduce a session id from memory.

        Asked by *writing through* the variable rather than by echoing it, because an environment
        variable that holds the right characters and points somewhere unreachable would pass the
        echo and fail the only thing it is for.
        """
        await inside(checkout, scratch, bwrap, 'echo named > "$MAINPLATE_SCRATCH/by-name.txt"')

        assert (scratch / "by-name.txt").read_text() == "named\n"

    async def test_the_checkout_is_named_too_so_a_command_can_come_back_to_it(
        self, checkout: Checkout, scratch: Path, bwrap: str
    ) -> None:
        # A command starts in the checkout, so this matters only once it has gone somewhere else,
        # which is exactly when a path is otherwise retyped.
        #
        # It leaves for `/` rather than for the scratch, and that is the difference between a test
        # and a test that passes anyway: `cd ""` leaves a shell exactly where it was, so a command
        # that had never left the checkout printed the checkout with the variable unset and proved
        # nothing. From `/` an unset variable prints `/`.
        said = await inside(checkout, scratch, bwrap, 'cd / && cd "$MAINPLATE_CHECKOUT" && pwd')

        assert str(checkout.root) in said

    async def test_the_store_is_not_named_because_nothing_should_be_writing_paths_into_it(
        self, checkout: Checkout, scratch: Path, bwrap: str
    ) -> None:
        # It is bound so that borrowed objects resolve, not so that anybody addresses it. A name
        # would invite a write to the one place the read-only bind exists to refuse.
        said = await inside(checkout, scratch, bwrap, "env | grep -c MAINPLATE_ || true")

        assert "2" in said, "the checkout and the scratch, and nothing else"

    async def test_nothing_in_it_reaches_git(self, checkout: Checkout, scratch: Path, bwrap: str) -> None:
        """
        What a scratch directory under the checkout would get wrong.

        `list` passes `--others`, so an untracked directory inside the checkout is in every listing
        and every `status` until something excludes it, and excluding it would mean an entry in the
        session's own `.git/info/exclude` that anything the session runs could take out again.
        """
        await inside(checkout, scratch, bwrap, f"mkdir -p {scratch}/cache && echo x > {scratch}/cache/blob")

        said = await inside(checkout, scratch, bwrap, "git status --porcelain; git ls-files --others")

        assert "scratch" not in said
        assert "cache" not in said


class TestWhatGitCanDoInThere:
    """Ordinary local git works in there, because this checkout's `.git` belongs to this session."""

    async def test_reading_git_works(self, checkout: Checkout, scratch: Path, bwrap: str) -> None:
        said = await inside(checkout, scratch, bwrap, "git ls-files")

        assert ".gitignore" in said
        assert "src/kept.txt" in said
        assert "exit 0" in said

    async def test_status_and_log_work(self, checkout: Checkout, scratch: Path, bwrap: str) -> None:
        (checkout.root / "src" / "kept.txt").write_text("edited\n")

        said = await inside(checkout, scratch, bwrap, "git status --porcelain && git log --oneline")

        assert "M src/kept.txt" in said
        assert "first" in said

    async def test_committing_works(self, checkout: Checkout, scratch: Path, bwrap: str) -> None:
        (checkout.root / "src" / "kept.txt").write_text("edited\n")

        said = await inside(checkout, scratch, bwrap, "git add -A && git commit -m 'from the agent'")

        assert "exit 0" in said
        assert await run("git", "log", "-1", "--format=%s", cwd=checkout.root) == "from the agent"

    async def test_stashing_works(self, checkout: Checkout, scratch: Path, bwrap: str) -> None:
        (checkout.root / "src" / "kept.txt").write_text("edited\n")

        said = await inside(checkout, scratch, bwrap, "git stash && git stash list")

        assert "stash@{0}" in said
        assert (checkout.root / "src" / "kept.txt").read_text() == "original\n"


class TestWhatABashCallSaysBack:
    async def test_a_command_that_worked_says_so(self, checkout: Checkout, scratch: Path, bwrap: str) -> None:
        """
        Success is stated rather than left to be inferred from an empty answer.

        A model reading no output and no status cannot tell "it worked and printed nothing" from
        "it never ran", and runs the thing a second time.
        """
        said = await inside(checkout, scratch, bwrap, "true")

        assert said.endswith("exit 0")
        assert "$ true" in said

    async def test_a_command_that_failed_carries_its_code_and_its_output(
        self, checkout: Checkout, scratch: Path, bwrap: str
    ) -> None:
        said = await inside(checkout, scratch, bwrap, "echo trouble >&2; exit 3")

        assert "trouble" in said, "stderr is folded in rather than dropped"
        assert "exit 3" in said

    async def test_an_empty_command_is_something_to_ask_again(
        self, checkout: Checkout, scratch: Path, bwrap: str
    ) -> None:
        with pytest.raises(ValueError, match="a command to run is required"):
            await inside(checkout, scratch, bwrap, "   ")

    async def test_a_command_that_runs_too_long_is_stopped(self, checkout: Checkout, scratch: Path, bwrap: str) -> None:
        with pytest.raises(ValueError, match="ran longer than"):
            await ran(InACheckout(checkout=checkout, scratch=scratch), bwrap, Venue.CONFINED, "sleep 30", seconds=1)

    def test_the_toolset_offers_exactly_one_tool_under_the_name_the_model_sees(
        self, checkout: Checkout, scratch: Path, bwrap: str
    ) -> None:
        """
        The name is what a model writes, so a rename is a silently broken call rather than an error.

        What a refusal *becomes* is deliberately not asserted here. It is the same three lines the
        file tools' `guarded` already carries a test for, and reaching it would mean building a
        `RunContext` to drive a library dataclass whose shape is not ours to depend on.
        """
        confinement = InACheckout(checkout=checkout, scratch=scratch)

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
            pytest.param(Filesystem.CHECKOUT, id="a form that already named the checkout"),
        ],
    )
    def test_a_repository_forces_the_checkout_whatever_was_asked(self, asked: Filesystem) -> None:
        settled = Isolation(filesystem=asked, network=True).settled("exe-github:blog")

        assert settled.filesystem is Filesystem.CHECKOUT
        assert settled.network, "and the network answer is untouched, because the axes are independent"

    def test_without_a_repository_the_checkout_becomes_no_files(self) -> None:
        """
        The direction that stops a form widening a session by naming something it cannot have.

        `NOTHING` rather than a refusal, because the honest reading of "a checkout" with no
        repository is the state a session with no repository already had.
        """
        settled = Isolation(filesystem=Filesystem.CHECKOUT).settled(None)

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

    async def test_a_command_sees_outside_any_checkout(self, bwrap: str) -> None:
        """
        What choosing it means, asserted rather than described.

        Against the real home directory, because a planted stand-in would only prove that a path
        nobody has is visible. `TestWhatACommandCanReach` asserts the same path is *denied* under a
        checkout, so the pair is what says the setting does anything.
        """
        home = Path.home()

        said = await ran(OverEverything(), bwrap, Venue.CONFINED, f"ls {home} >/dev/null && echo VISIBLE", seconds=20)

        assert "VISIBLE" in said

    @pytest.mark.network
    async def test_the_network_is_still_off_unless_the_session_asked(self, bwrap: str, local_server: str) -> None:
        """
        The reason `EVERYTHING` is still a sandbox rather than no sandbox.

        Dropping it for this arm would take the network switch with it, so the whole machine would
        silently imply the whole internet and one of the two axes would stop being expressible.
        """
        connected = await ran(OverEverything(), bwrap, Venue.CONNECTED, local_server, seconds=20)
        assert "mainplate loopback control\n" in connected, "the control: the server is there to reach"

        confined = await ran(OverEverything(), bwrap, Venue.CONFINED, local_server, seconds=20)

        assert "ConnectionRefusedError" in confined


class TestOnARunnerWithNoNetwork:
    """
    Every `network` claim again, with the suite itself in a namespace that has no network.

    The suite runs here on whatever runner it is given, and a claim about the network that holds on
    a laptop can still fail where there is none, which is a session of this console developing this
    console with its own network off: counting routes and looking up a public name both did. So the
    second runner is made rather than waited for. A child `pytest` rather than an arm of a fixture,
    because the runner's namespace is the process's: moving a thread into another needs
    `CAP_SYS_ADMIN`, and entering a user namespace is refused to a process with threads.

    The cost, stated: a second interpreter collecting the whole suite to pick the claims out, which
    is seconds. The marker rather than a list of names, so a new claim about the network is picked up
    by being marked, and the other runner is the ordinary run, which already happens.
    """

    @pytest.mark.timeout(120)
    async def test_every_network_claim_holds(self, bwrap: str) -> None:
        offline = (bwrap, "--dev-bind", "/", "/", "--unshare-net", "--")
        root = Path(__file__).parents[1]
        routes = await run(*offline, "sh", "-c", "tail -n +2 /proc/net/route | wc -l", cwd=root)
        assert routes == "0", "the control: the runner really has no network"

        # `no:cacheprovider` so the child's results never become the `--lf` of whoever ran the suite.
        said = await run(
            *offline, sys.executable, "-m", "pytest", "-m", "network", "-n0", "-p", "no:cacheprovider", cwd=root
        )

        assert "passed" in said
