from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any
from typing import get_args

import pytest
from conftest import DEFAULT_CHOICE
from conftest import FIXTURE
from conftest import INSTRUCTIONS
from conftest import Provider
from conftest import Scripted
from conftest import passing
from conftest import run
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ToolCallPart
from without_durability.stepwise import Blocked

from mainplate import records
from mainplate.agent import Choice
from mainplate.conversation import DECLARED_KEY
from mainplate.conversation import REPOSITORY_DECLARED_KEY
from mainplate.conversation import conversing
from mainplate.conversation import declared_in
from mainplate.conversation import registered_in
from mainplate.forge import Workspaces
from mainplate.plugins.asking import Declaring
from mainplate.plugins.asking import running
from mainplate.plugins.installed import BUNDLED_ROOT
from mainplate.plugins.installed import BadDeclaration
from mainplate.plugins.installed import Collides
from mainplate.plugins.installed import Enrolled
from mainplate.plugins.installed import Installed
from mainplate.plugins.installed import Tier
from mainplate.plugins.installed import bundled
from mainplate.plugins.installed import grouped
from mainplate.plugins.installed import installed_by
from mainplate.plugins.installed import repository_plugins
from mainplate.plugins.installed import without_collisions
from mainplate.plugins.protocol import EVENTS
from mainplate.plugins.protocol import Described
from mainplate.plugins.protocol import Event
from mainplate.plugins.protocol import Refused
from mainplate.plugins.protocol import parse_answer
from mainplate.plugins.protocol import parse_described
from mainplate.plugins.protocol import refusing
from mainplate.plugins.protocol import settings_of
from mainplate.plugins.protocol import state_of
from mainplate.plugins.protocol import toned
from mainplate.plugins.running import PluginFailed
from mainplate.plugins.running import Spawned
from mainplate.sandbox import sandbox_command
from mainplate.service import Service
from mainplate.sessions import Session
from mainplate.sessions import read_tending
from mainplate.tending import TENDED

# Where the fixture plugins live. A directory of real executables rather than strings written into
# tests, because what is under test includes that a plugin is a *file this console runs*: a fake
# that answered in-process would exercise everything but the one claim that matters.
FIXTURES = Path(__file__).parent / "plugins"


def spoken(**fields: object) -> dict[str, object]:
    """One event payload, with the envelope every event carries filled in."""
    return {"session": "a-session", "plugin": "user:probe", **fields}


async def still_running(script: Path) -> bool:
    """
    Whether any process is still running this script, asked of the process table.

    The honest reading of "nothing was left behind", and asked of the system rather than of an
    object this console holds: what leaks is a child process, and a handle that says it was killed
    would be the claim under test standing in for the evidence.

    `pgrep -f` matches the whole command line and excludes itself, and the path is unique to one
    test's `tmp_path`, so nothing else can match. A short wait first, because killing is a signal
    rather than a reaping and the process table catches up a moment later.
    """
    for _ in range(50):
        found = await asyncio.create_subprocess_exec(
            "pgrep", "-f", str(script), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        if await found.wait() != 0:
            return False
        await asyncio.sleep(0.02)
    return True


async def asked(plugin: Path, payload: Mapping[str, object]) -> Any:
    """
    One plugin run for real, over a pipe, as the console runs one.

    Unconfined, because these are the console's own tier and confinement is what
    `TestARepositorysOwnPlugin` is about.
    """
    installed = Installed(tier=Tier.USER, name=plugin.name, path=plugin)
    return await Spawned(environ={})(installed, payload)


class TestTheVocabulary:
    """
    What a plugin may say and what it may be told, held to the shapes this console owns.

    The boundary is the point: a plugin's answer crossed a trust boundary, so a word this console has
    no answer for is a mistake to name rather than one to drop.
    """

    def test_the_written_out_events_are_the_ones_the_type_has(self) -> None:
        """
        One fact in two places, and this is what turns a drift into a failure.

        `EVENTS` is a value because some readers enumerate rather than match, and deriving it from
        the type would be a runtime reading of a static thing. So the pair is asserted instead, which
        is the bargain `RETENTION` and `CACHE_FOR` already take.
        """
        assert set(EVENTS) == set(get_args(Event.__value__))

    def test_a_word_this_console_has_no_answer_for_is_refused_rather_than_dropped(self) -> None:
        """A plugin that wrote `tool` where the word is `tools` is told, rather than quietly ignored."""
        with pytest.raises(Refused, match="user:probe"):
            parse_described("user:probe", {"tool": [{"name": "hand_off"}]})

    def test_a_row_naming_two_controls_is_refused_where_it_is_parsed(self) -> None:
        """
        The rule the shape cannot express, checked at `setup` rather than at render.

        Found here it is a session that says which plugin and why; found at render time it is a page
        that will not draw.
        """
        with pytest.raises(Refused):
            parse_described(
                "user:probe",
                {
                    "card": {
                        "heading": "probe",
                        "rows": [{"switch": {"name": "a", "label": "a"}, "number": {"name": "b", "label": "b"}}],
                    }
                },
            )

    @pytest.mark.parametrize(
        ("named", "drawn"),
        [("plain", "plain"), ("quiet", "quiet"), ("strong", "strong"), ("chartreuse", "plain"), (None, "plain")],
    )
    def test_a_tone_nothing_answers_to_draws_the_plain_one(self, named: str | None, drawn: str) -> None:
        """
        The opposite of what an unknown record `kind` does, and deliberately so.

        A kind nothing answers to is a checkpoint this console cannot read; a tone nothing answers to
        is a panel in the wrong ink, and a session that will not render is far worse than that.
        """
        assert toned(named) == drawn

    def test_the_two_keywords_cross_the_wire_as_the_words_a_plugin_writes(self) -> None:
        """`return` and `set` cannot be field names, so the alias is what a plugin actually writes."""
        answered = parse_answer("user:probe", {"return": "recorded", "set": {"seen": 3}})
        assert answered.returned == "recorded"
        assert answered.setting == {"seen": 3}

    @pytest.mark.parametrize(
        ("event", "asking"),
        [("after_turn", {"return": "no"}), ("compose", {"retry": "no"}), ("tool", {"inject": ["no"]})],
    )
    def test_an_effect_the_event_has_no_room_for_is_refused(self, event: Event, asking: dict[str, object]) -> None:
        """
        The table in the design note, enforced in one place rather than wherever a field is read.

        A plugin asking for an effect that goes nowhere would otherwise be quietly doing nothing,
        which is the failure mode a closed vocabulary exists to make impossible.
        """
        with pytest.raises(Refused, match="user:probe"):
            refusing("user:probe", event, parse_answer("user:probe", asking))


class TestSettingsAndState:
    """
    One column holding two kinds of thing, split by whether the card declared the name.

    Settings are already a place, so state is a setting with nothing in front of it: one write, one
    lifetime, and no second mechanism for a plugin author to learn.
    """

    CARD = Described.model_validate(
        {
            "card": {
                "heading": "probe",
                "rows": [
                    {"switch": {"name": "on", "label": "on", "default": True}},
                    {"number": {"name": "keep", "label": "keep", "default": 40}},
                ],
            }
        }
    )

    def test_a_control_nobody_has_set_reads_as_its_own_declared_default(self) -> None:
        assert settings_of(self.CARD, {}) == {"on": True, "keep": 40}

    def test_a_stored_value_of_the_wrong_shape_reads_as_the_default(self) -> None:
        """
        What a hand-edited row deserves, and a better answer than a session that will not run.

        This is the parse the `STRICT` table stopped doing: the column is unchecked text, so the
        invariant moved here, against the schema the card already declares.
        """
        assert settings_of(self.CARD, {"on": "yes", "keep": "lots"}) == {"on": True, "keep": 40}

    def test_a_number_is_not_satisfied_by_a_boolean(self) -> None:
        """`True` is an `int` in Python and is not a number of tokens anywhere else."""
        assert settings_of(self.CARD, {"keep": True})["keep"] == 40

    def test_what_the_card_does_not_declare_comes_back_as_state(self) -> None:
        held = {"on": False, "keep": 12, "last": {"tree": "abc"}}
        assert settings_of(self.CARD, held) == {"on": False, "keep": 12}
        assert state_of(self.CARD, held) == {"last": {"tree": "abc"}}

    def test_a_plugin_with_no_card_gets_pure_state(self) -> None:
        """Which is what makes `set` one mechanism rather than two: no card, no settings, all state."""
        nothing = Described()
        assert settings_of(nothing, {"seen": 3}) == {}
        assert state_of(nothing, {"seen": 3}) == {"seen": 3}


class TestWhereAPluginComesFrom:
    """Three places one may be declared, and what a tier is allowed to claim."""

    def test_the_key_is_the_whole_of_the_name(self) -> None:
        """
        Two people's plugins are installable side by side, which a declared name would prevent.

        Both may call themselves `guidance`; a rule that the declared name must match its key would
        mean editing one of their files, which their next release undoes.
        """
        installed = installed_by(Tier.USER, {"alice-guidance": Path("/a"), "bob-guidance": Path("/b")})
        assert [each.qualified for each in installed] == ["user:alice-guidance", "user:bob-guidance"]

    def test_the_same_name_in_two_tiers_is_two_plugins(self) -> None:
        """A bundled `guidance` and an operator's are three settings blobs away from each other."""
        mine = Installed(tier=Tier.USER, name="guidance", path=Path("/a"))
        ours = Installed(tier=Tier.BUNDLED, name="guidance", path=Path("/b"))
        assert mine.qualified != ours.qualified

    def test_only_a_repositorys_plugin_is_confined(self) -> None:
        """
        The isolation a session picked is about what the *model* may reach, and a plugin is not the
        model.
        """
        assert Installed(tier=Tier.REPOSITORY, name="review", path=Path("/a")).confined
        assert not Installed(tier=Tier.USER, name="review", path=Path("/a")).confined
        assert not Installed(tier=Tier.BUNDLED, name="review", path=Path("/a")).confined

    def test_a_repositorys_contributions_are_prefixed_and_no_other_tiers_are(self) -> None:
        """
        Not tidiness: unprefixed, a repository could collide deliberately, and a collision that
        refused would let a repository stop your session starting.
        """
        theirs = Installed(tier=Tier.REPOSITORY, name="review", path=Path("/a"))
        mine = Installed(tier=Tier.USER, name="review", path=Path("/a"))
        assert theirs.tool_named("lint") == "repo_review_lint"
        assert theirs.leader_named("lint") == "review:lint"
        assert mine.tool_named("lint") == "lint"
        assert mine.leader_named("lint") == "lint"

    def test_every_plugin_comes_on_and_nothing_shadows_anything(self) -> None:
        """
        Installing one is the decision, and this console does not second-guess it by turning
        something else off out of view. A tier switch sets the switches under it; there is no other
        answer.
        """
        enrolled = tuple(
            Enrolled(installed=each, described=Described())
            for each in (
                Installed(tier=Tier.BUNDLED, name="guidance", path=Path("/a")),
                Installed(tier=Tier.USER, name="guidance", path=Path("/b")),
            )
        )
        assert [each.qualified for each in running(enrolled, TENDED)] == ["bundled:guidance", "user:guidance"]

    def test_a_switch_a_session_recorded_wins_over_the_default(self) -> None:
        enrolled = (
            Enrolled(installed=Installed(tier=Tier.BUNDLED, name="handoff", path=Path("/a")), described=Described()),
        )
        assert running(enrolled, TENDED.switched("bundled:handoff", False)) == ()

    def test_the_settings_step_draws_every_tier_including_the_empty_ones(self) -> None:
        """
        So the step is the same shape on every session and the flow can be learned and tested as one
        thing rather than as however many lists a repository happens to produce.
        """
        assert [tier for tier, _ in grouped((Installed(tier=Tier.BUNDLED, name="handoff", path=Path("/a")),))] == list(
            Tier
        )


class TestWhenTwoPluginsWantOneName:
    """Namespacing settles identity; it cannot settle the two spaces a contribution lands in."""

    def enrolled(self, tier: Tier, name: str, tool: str) -> Enrolled:
        return Enrolled(
            installed=Installed(tier=tier, name=name, path=Path("/a")),
            described=Described.model_validate({"tools": [{"name": tool, "description": "d"}]}),
        )

    def test_two_of_the_operators_own_wanting_one_tool_are_refused_naming_both(self) -> None:
        """
        Safe to refuse precisely because both are yours: the refusal holds up the first message
        rather than the settings step, so the screen still draws with the switch that fixes it.
        """
        with pytest.raises(Collides, match="hand_off"):
            without_collisions(
                (self.enrolled(Tier.USER, "mine", "hand_off"), self.enrolled(Tier.BUNDLED, "handoff", "hand_off"))
            )

    def test_a_repository_cannot_collide_because_its_names_are_prefixed(self) -> None:
        held = without_collisions(
            (self.enrolled(Tier.BUNDLED, "handoff", "hand_off"), self.enrolled(Tier.REPOSITORY, "handoff", "hand_off"))
        )
        assert len(held) == 2

    def test_a_repository_that_still_collides_is_dropped_rather_than_stopping_the_session(self) -> None:
        """
        The last line of the guarantee, and the reason it is a drop: the alternative is a repository
        deciding whether your session starts.
        """
        held = without_collisions(
            (
                self.enrolled(Tier.USER, "mine", "repo_review_lint"),
                self.enrolled(Tier.REPOSITORY, "review", "lint"),
            )
        )
        assert [each.qualified for each in held] == ["user:mine"]


class TestWhatARepositoryMayDeclare:
    def test_a_file_declaring_nothing_but_plugins_is_all_there_is_to_declare(self, tmp_path: Path) -> None:
        """
        Not "does not today": there is no field for an endpoint, a credential or an isolation level,
        so the escalation is unrepresentable rather than checked.
        """
        (tmp_path / ".mainplate").mkdir()
        (tmp_path / ".mainplate" / "mainplate.yaml").write_text("endpoint: {url: 'http://elsewhere'}\n")
        with pytest.raises(BadDeclaration):
            repository_plugins(tmp_path)

    def test_a_path_climbing_out_of_the_repository_is_refused(self, tmp_path: Path) -> None:
        """A declaration naming `../../bin/sh` is a repository asking to run something else entirely."""
        (tmp_path / ".mainplate").mkdir()
        (tmp_path / ".mainplate" / "mainplate.yaml").write_text("plugins: {escape: ../../../bin/sh}\n")
        with pytest.raises(BadDeclaration, match="outside the tree"):
            repository_plugins(tmp_path)

    def test_a_repository_carrying_no_such_file_declares_nothing(self, tmp_path: Path) -> None:
        """Which is most repositories, and what makes trusting one inert without anything being read."""
        assert repository_plugins(tmp_path) == ()

    @pytest.mark.parametrize("named", ["../elsewhere", "a/b", ".hidden", "", "a b"])
    def test_a_key_that_is_not_a_name_is_refused_where_the_file_is_read(self, tmp_path: Path, named: str) -> None:
        """
        A name is not only a label: it is half a tool name, half a leader, the key of a settings blob,
        and the directory a plugin's own scratch is made at. So a key naming a path is a repository
        asking for a directory outside the one this console made for it.
        """
        (tmp_path / ".mainplate").mkdir()
        (tmp_path / ".mainplate" / "mainplate.yaml").write_text(f"plugins: {{{named!r}: thing}}\n")
        with pytest.raises(BadDeclaration, match="not a plugin name"):
            repository_plugins(tmp_path)


class TestRunningOne:
    """A plugin is a single executable, spoken to over a pipe. That is the whole contract."""

    async def test_a_plugin_that_is_not_there_fails_naming_it(self) -> None:
        installed = Installed(tier=Tier.USER, name="absent", path=Path("/nowhere/at/all"))
        with pytest.raises(PluginFailed, match="user:absent"):
            await Spawned(environ={})(installed, spoken(event="setup"))

    async def test_a_plugin_that_exits_non_zero_fails_carrying_what_it_said(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken"
        broken.write_text("#!/bin/sh\necho 'it went wrong' >&2\nexit 3\n")
        broken.chmod(0o755)
        with pytest.raises(PluginFailed, match="it went wrong"):
            await asked(broken, spoken(event="setup"))

    async def test_a_plugin_that_prints_something_that_is_not_json_fails_naming_it(self, tmp_path: Path) -> None:
        noisy = tmp_path / "noisy"
        noisy.write_text("#!/bin/sh\ncat >/dev/null\necho 'not json at all'\n")
        noisy.chmod(0o755)
        with pytest.raises(PluginFailed, match="not JSON"):
            await asked(noisy, spoken(event="setup"))

    async def test_what_a_plugin_prints_on_stderr_is_kept_out_of_its_answer(self, tmp_path: Path) -> None:
        """
        A `print` left in while debugging is not a protocol error, which is why the two streams stay
        apart - unlike a command a person runs, where the interleaving is the answer.
        """
        chatty = tmp_path / "chatty"
        chatty.write_text("#!/bin/sh\ncat >/dev/null\necho 'still here' >&2\necho '{\"events\": []}'\n")
        chatty.chmod(0o755)
        assert await asked(chatty, spoken(event="setup")) == {"events": []}

    async def test_a_cancelled_call_leaves_no_process_behind(self, tmp_path: Path) -> None:
        """
        A pass is cancelled when the worker is, which is every shutdown.

        A plugin part-way through answering then leaves a `communicate` that will never resume, and
        killing the process is not enough: what stays open is the pipes it was reading. Draining them
        is what the timeout path does and is exactly what cannot be done here, since an `await` in a
        cancelled task is cancelled again the moment it is reached.

        Left alone the leak surfaces as a `ResourceWarning` raised into whatever happens to be
        running when the collector fires, which under `filterwarnings = ["error"]` is a failure
        attributed to an unrelated test - so it is worth catching here, where it says what it is.
        """
        slow = tmp_path / "slow"
        slow.write_text("#!/bin/sh\ncat >/dev/null\nsleep 30\n")
        slow.chmod(0o755)
        installed = Installed(tier=Tier.USER, name="slow", path=slow)
        asking = asyncio.ensure_future(Spawned(environ={})(installed, spoken(event="setup")))
        # Long enough for the process to be spawned and the payload written, which is the state the
        # leak needs: a shorter wait would cancel before there was anything to leave behind.
        await asyncio.sleep(0.3)
        asking.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asking

        assert not await still_running(slow), "the plugin outlived the call that was cancelled"

    async def test_the_payload_reaches_the_plugin_on_stdin(self, tmp_path: Path) -> None:
        """The `echo`-and-a-pipe claim, asserted: a plugin is testable by hand and this is the hand."""
        echoing = tmp_path / "echoing"
        echoing.write_text("#!/bin/sh\ncat\n")
        echoing.chmod(0o755)
        assert await asked(echoing, spoken(event="setup", worktree="/somewhere")) == spoken(
            event="setup", worktree="/somewhere"
        )


class TestWhereARepositorysPluginRuns:
    """
    The namespace one is given, which differs from a command's in the three ways a plugin needs.

    Asserted against the `bwrap` arguments rather than by running anything, because what is under
    test is what this console *asks* for: `test_sandbox.py` is where what a mount namespace actually
    does is proved, and these would be the same assertions made slowly.
    """

    @pytest.fixture
    def spawned(self, tmp_path: Path) -> Spawned:
        return Spawned(bwrap="/usr/bin/bwrap", scratch=tmp_path / "plugins", environ={})

    def installed(self) -> Installed:
        return Installed(tier=Tier.REPOSITORY, name="checks", path=Path("/tree/.mainplate/checks"))

    async def invocation(self, spawned: Spawned, event: str, worktree: Path) -> tuple[str, ...]:
        return (await spawned.invocation(self.installed(), spoken(event=event, worktree=str(worktree)))).argv

    async def test_setting_up_reaches_the_network_and_nothing_else_does(self, spawned: Spawned, worktree: Any) -> None:
        """
        **Before the conversation, connected; during it, never.** A plugin that needs a program has to
        fetch one, and `setup` runs before the first message: the worktree holds the commit the
        repository supplied, and nothing the model wrote exists yet.
        """
        assert "--unshare-net" not in await self.invocation(spawned, "setup", worktree.root)
        for event in ("tool", "before_request", "after_turn", "compose", "action"):
            assert "--unshare-net" in await self.invocation(spawned, event, worktree.root), event

    async def test_home_is_the_plugins_own_scratch_and_not_the_tmpfs_a_command_gets(
        self, spawned: Spawned, worktree: Any, tmp_path: Path
    ) -> None:
        """
        Which is the whole of what makes a plugin with dependencies possible: everything that fetches
        keeps what it fetched under `$HOME`, so on a tmpfs a `uv run --script` shebang would resolve
        an interpreter at setup and find none at the next event, with the network shut.
        """
        argv = await self.invocation(spawned, "after_turn", worktree.root)
        at = argv.index("HOME")
        assert argv[at + 1] == str(tmp_path / "plugins" / "a-session" / "repository" / "checks")

    async def test_the_scratch_is_this_plugins_alone_and_never_the_one_the_model_writes_to(
        self, spawned: Spawned
    ) -> None:
        """
        A plugin that kept an executable in the session's own scratch would be running, unattended and
        at every turn boundary, whatever the model last put at that path. So the two are separate
        directories, and the tier and the name are what tell one plugin's from another's.
        """
        mine = spawned.scratch_for(self.installed(), "a-session")
        theirs = spawned.scratch_for(Installed(tier=Tier.USER, name="checks", path=Path("/x")), "a-session")
        elsewhere = spawned.scratch_for(self.installed(), "another-session")
        assert len({mine, theirs, elsewhere}) == 3
        assert "scratch" not in mine.parts, "the session's own scratch is somewhere else entirely"

    async def test_it_is_named_in_the_payload_as_well_as_in_the_environment(
        self, spawned: Spawned, worktree: Any, tmp_path: Path
    ) -> None:
        """
        Both, because the two readers are different: the payload is what a plugin parses, and the
        environment is what a line of shell reaches without parsing anything. The one that binds the
        directory is the one that says where it is, so there is no second place computing the path.
        """
        sending = await spawned.invocation(self.installed(), spoken(event="setup", worktree=str(worktree.root)))
        assert sending.payload["scratch"] == str(tmp_path / "plugins" / "a-session" / "repository" / "checks")

    async def test_a_plugin_outside_a_worktree_is_handed_no_scratch_at_all(self, spawned: Spawned) -> None:
        """It has the operator's own environment and a `$HOME`, and needs nothing from this console."""
        installed = Installed(tier=Tier.USER, name="notify", path=Path("/opt/notify"))
        sending = await spawned.invocation(installed, spoken(event="setup"))
        assert "scratch" not in sending.payload


class TestTheBundledHandoff:
    """
    The plugin the protocol was read off, run as a process exactly as any other is.

    Between it and `guidance` the pair exercises every event, every effect and every contribution,
    which is what makes them a test rather than two examples.
    """

    @pytest.fixture
    def handoff(self) -> Path:
        return BUNDLED_ROOT / "handoff"

    async def test_it_ships_and_describes_itself(self, handoff: Path) -> None:
        described = parse_described("bundled:handoff", await asked(handoff, spoken(event="setup")))
        assert set(described.events) == {"tool", "after_turn", "compose"}
        assert [each.name for each in described.tools] == ["hand_off"]
        assert [each.leader for each in described.answers] == ["handoff"]
        assert described.answers[0].demands is False, "the one answer whose box may be empty"
        assert described.card is not None
        assert described.settings == {"hands_off": True, "reserve": 40}

    async def test_a_document_is_delivered_with_a_boundary_and_a_value_goes_back_to_the_model(
        self, handoff: Path
    ) -> None:
        """
        Three effects out of one call, which is what made this the shape the protocol was read off.

        `forget` is why it is worth a mechanism at all: a handoff that did not clear the context
        would be a summary of the conversation appended to the conversation.
        """
        written = "x" * 400
        answered = parse_answer(
            "bundled:handoff", await asked(handoff, spoken(event="tool", tool="hand_off", args={"document": written}))
        )
        assert answered.retry is None
        assert [(each.said, each.forget, each.tone) for each in answered.deliver] == [(written, True, "strong")]
        assert answered.returned is not None
        assert "starts again" in str(answered.returned)

    async def test_an_acknowledgement_is_a_correctable_refusal_rather_than_a_document(self, handoff: Path) -> None:
        """A `retry` doing exactly what retries are for: the model is told and writes a real one."""
        answered = parse_answer(
            "bundled:handoff",
            await asked(handoff, spoken(event="tool", tool="hand_off", args={"document": "Done, I wrote it."})),
        )
        assert answered.retry is not None
        assert "acknowledgement" in answered.retry
        assert answered.deliver == ()

    async def test_a_turn_that_crossed_the_reserve_asks_for_one(self, handoff: Path) -> None:
        answered = parse_answer(
            "bundled:handoff",
            await asked(
                handoff,
                spoken(
                    event="after_turn",
                    turn=4,
                    opened_on={"kind": "prompt"},
                    context=170_000,
                    window=200_000,
                    settings={"hands_off": True, "reserve": 40},
                ),
            ),
        )
        assert len(answered.deliver) == 1
        assert answered.deliver[0].forget is False, "the ask carries no boundary; the document does"

    @pytest.mark.parametrize(
        ("context", "why"),
        [(10_000, "room left"), (199_000, "overshot the room a handoff needs"), (0, "nothing said yet")],
    )
    async def test_a_turn_that_did_not_cross_it_asks_for_nothing(self, handoff: Path, context: int, why: str) -> None:
        answered = parse_answer(
            "bundled:handoff",
            await asked(
                handoff,
                spoken(
                    event="after_turn",
                    turn=4,
                    opened_on={"kind": "prompt"},
                    context=context,
                    window=200_000,
                    settings={"hands_off": True, "reserve": 40},
                ),
            ),
        )
        assert answered.deliver == (), why

    async def test_a_turn_it_opened_itself_never_triggers_another(self, handoff: Path) -> None:
        """
        The whole of what stops it recursing. The reserve stays crossed for as long as the context is
        large, so the ask turn - whose own context is the conversation it is summarising - would
        cross it again the instant it ended, and so would the one after that.
        """
        answered = parse_answer(
            "bundled:handoff",
            await asked(
                handoff,
                spoken(
                    event="after_turn",
                    turn=4,
                    opened_on={"kind": "note", "plugin": "user:probe"},
                    context=170_000,
                    window=200_000,
                    settings={"hands_off": True, "reserve": 40},
                ),
            ),
        )
        assert answered.deliver == ()

    async def test_another_plugins_note_does_not_stop_it(self, handoff: Path) -> None:
        """
        The comparison is against this plugin's own name rather than against the record's kind, which
        is why the payload carries one: a turn opened by somebody else's note is an ordinary turn.
        """
        answered = parse_answer(
            "bundled:handoff",
            await asked(
                handoff,
                spoken(
                    event="after_turn",
                    turn=4,
                    opened_on={"kind": "note", "plugin": "user:someone-else"},
                    context=170_000,
                    window=200_000,
                    settings={"hands_off": True, "reserve": 40},
                ),
            ),
        )
        assert len(answered.deliver) == 1

    async def test_the_switch_being_off_asks_for_nothing(self, handoff: Path) -> None:
        answered = parse_answer(
            "bundled:handoff",
            await asked(
                handoff,
                spoken(
                    event="after_turn",
                    turn=4,
                    opened_on={"kind": "prompt"},
                    context=170_000,
                    window=200_000,
                    settings={"hands_off": False, "reserve": 40},
                ),
            ),
        )
        assert answered.deliver == ()

    async def test_a_model_nobody_wrote_a_window_down_for_asks_for_nothing(self, handoff: Path) -> None:
        """There is no fraction and no way to know a reserve was crossed, so nothing is guessed at."""
        answered = parse_answer(
            "bundled:handoff",
            await asked(
                handoff,
                spoken(
                    event="after_turn",
                    turn=4,
                    opened_on={"kind": "prompt"},
                    context=170_000,
                    settings={"hands_off": True, "reserve": 40},
                ),
            ),
        )
        assert answered.deliver == ()

    async def test_a_note_typed_beside_the_leader_is_appended_rather_than_replacing_the_ask(
        self, handoff: Path
    ) -> None:
        """
        The base is what a handoff *is* and has to be there whether or not anybody adds to it, where
        "dwell on the parser" on its own is an instruction to summarise a summary.
        """
        answered = parse_answer(
            "bundled:handoff",
            await asked(handoff, spoken(event="compose", leader="handoff", said="dwell on the parser")),
        )
        assert len(answered.deliver) == 1
        said = answered.deliver[0].said
        assert said.startswith("Hand this conversation off.")
        assert said.endswith("dwell on the parser")


class TestTheBundledGuidance:
    """The other half of what the protocol has to carry: `instructions` and an `inject`."""

    @pytest.fixture
    def guidance(self) -> Path:
        return BUNDLED_ROOT / "guidance"

    @pytest.fixture
    def repository(self, tmp_path: Path) -> Path:
        root = tmp_path / "repo"
        (root / "apps" / "web").mkdir(parents=True)
        (root / "AGENTS.md").write_text("This project is a console.\n")
        (root / "apps" / "web" / "AGENTS.md").write_text(
            "---\ndescription: How the web app is laid out\n---\n\nUse the design tokens.\n"
        )
        return root

    async def test_a_session_with_no_repository_contributes_nothing_and_wants_no_events(self, guidance: Path) -> None:
        """
        Which is what stops the mechanism being wasteful: asking it per request would be a process
        per request to be told nothing.
        """
        described = parse_described("bundled:guidance", await asked(guidance, spoken(event="setup")))
        assert described.events == ()
        assert described.instructions is None

    async def test_the_repositorys_own_guidance_and_an_index_of_the_rest_are_what_it_contributes(
        self, guidance: Path, repository: Path
    ) -> None:
        """
        **The index is identity and the file is the content.** That `apps/web` has conventions is one
        line and what they are is a page, which is what makes it affordable on every request.
        """
        described = parse_described(
            "bundled:guidance", await asked(guidance, spoken(event="setup", worktree=str(repository)))
        )
        assert described.events == ("before_request",)
        assert described.instructions is not None
        assert "This project is a console." in described.instructions
        assert "`apps/web/AGENTS.md`: How the web app is laid out" in described.instructions
        assert "Use the design tokens." not in described.instructions, "the index names it rather than quoting it"

    async def test_frontmatter_is_kept_out_of_what_the_model_is_told(self, guidance: Path, repository: Path) -> None:
        """
        Addressed to whatever loads the file rather than to the model, so passing it on spends a
        context window on a `paths:` list nobody reads.
        """
        (repository / "AGENTS.md").write_text("---\ndescription: the root\n---\n\nThis project is a console.\n")
        described = parse_described(
            "bundled:guidance", await asked(guidance, spoken(event="setup", worktree=str(repository)))
        )
        assert described.instructions is not None
        assert "description:" not in described.instructions

    async def test_reaching_into_a_directory_hands_over_what_it_carries(self, guidance: Path, repository: Path) -> None:
        """
        Delivered on the request *after* the calls that reached in, which is the same round trip the
        tool results arrive on: there is no earlier moment.
        """
        messages = [
            {
                "kind": "response",
                "parts": [
                    {
                        "part_kind": "tool-call",
                        "tool_name": "read",
                        "args": {"path": "apps/web/main.py"},
                        "tool_call_id": "c1",
                    }
                ],
            }
        ]
        answered = parse_answer(
            "bundled:guidance",
            await asked(guidance, spoken(event="before_request", worktree=str(repository), messages=messages)),
        )
        assert len(answered.inject) == 1
        assert "Use the design tokens." in answered.inject[0]

    async def test_what_the_model_has_already_been_handed_is_not_handed_again(
        self, guidance: Path, repository: Path
    ) -> None:
        """
        **The history is the ledger**, which is what makes a set kept anywhere else wrong rather than
        merely redundant: one on the session would survive a `forget`, and one on the pass would
        deliver again on every pass.
        """
        messages = [
            {
                "kind": "response",
                "parts": [
                    {
                        "part_kind": "tool-call",
                        "tool_name": "read",
                        "args": {"path": "apps/web/main.py"},
                        "tool_call_id": "c1",
                    }
                ],
            },
            {
                "kind": "request",
                "parts": [
                    {
                        "part_kind": "system-prompt",
                        "content": "`apps/web/AGENTS.md`, guidance for this part of the repository:\n\nUse the design tokens.",
                    }
                ],
            },
        ]
        answered = parse_answer(
            "bundled:guidance",
            await asked(guidance, spoken(event="before_request", worktree=str(repository), messages=messages)),
        )
        assert answered.inject == ()

    async def test_a_path_that_climbs_out_of_the_repository_hands_over_nothing(
        self, guidance: Path, repository: Path
    ) -> None:
        """
        It cannot arrive from a tool, but this reads what a model wrote and a value that cannot
        happen is not one to crash on.
        """
        messages = [
            {
                "kind": "response",
                "parts": [
                    {
                        "part_kind": "tool-call",
                        "tool_name": "read",
                        "args": {"path": "../../etc/passwd"},
                        "tool_call_id": "c1",
                    }
                ],
            }
        ]
        answered = parse_answer(
            "bundled:guidance",
            await asked(guidance, spoken(event="before_request", worktree=str(repository), messages=messages)),
        )
        assert answered.inject == ()


class TestWhatTheBundledSetIs:
    def test_every_bundled_plugin_is_an_executable_that_describes_itself(self) -> None:
        """
        Discovered rather than listed, so adding one is a file and nothing else - which is the same
        claim the other tiers make about somebody else's.
        """
        found = bundled()
        assert {each.name for each in found} == {"handoff", "guidance"}
        for each in found:
            assert each.tier is Tier.BUNDLED
            assert each.path.stat().st_mode & 0o111, f"{each.name} is not executable"


async def set_up(
    service: Service, declaring: Declaring, chosen: Choice = DEFAULT_CHOICE, workspaces: Workspaces | None = None
) -> Session:
    """
    A session past its settings step, which is the three moments the console actually has.

    A pass, to plant the worktree and read what each tier *declares*; the press, which is the only
    thing that lets a plugin be run at all; and a second pass, which is where running one now
    happens. Written here rather than in `conftest.py` because it is what this suite is about:
    everywhere else a session with plugins in it is incidental, and here the order is the claim.
    """
    session = await service.start(chosen)
    body = conversing(Provider().endpoints(), INSTRUCTIONS, workspaces, declaring=declaring)
    await passing(service, session.id, body)
    console = replace(service, declaring=declaring)
    await console.settle(session.id, {})
    await passing(service, session.id, body)
    return session


class TestASessionsPlugins:
    """What the two moments do: read what is declared, load what is on, and record what they asked for."""

    @pytest.fixture
    def declaring(self) -> Declaring:
        return Declaring(console=bundled(), speaking=Spawned(environ={}))

    async def test_the_first_pass_declares_without_running_anything(
        self, service: Service, declaring: Declaring
    ) -> None:
        """
        **The first pass of a session answers nothing and executes nothing**, which is the trust
        boundary rather than an accident: it plants, reads what each tier declares out of files, and
        comes back `Blocked` on an empty inbox. What each plugin *is* is not known yet, because
        asking is running and nobody has said to.
        """
        session = await service.start(DEFAULT_CHOICE)
        provider = Provider()
        ended = await passing(service, session.id, conversing(provider.endpoints(), INSTRUCTIONS, declaring=declaring))

        assert isinstance(ended, Blocked), "it is waiting on a message rather than finished"
        assert provider.asked == 0, "and it asked no provider anything"
        recorded = await service.checkpointer.load(session.id)
        assert DECLARED_KEY in recorded
        assert REPOSITORY_DECLARED_KEY in recorded
        assert registered_in(recorded) is None, "nothing has been run, so nothing has been registered"
        declared = declared_in(recorded)
        assert declared is not None
        assert {each.qualified for each in declared} == {"bundled:handoff", "bundled:guidance"}

    async def test_the_press_is_what_runs_them(self, service: Service, declaring: Declaring) -> None:
        """
        Which is the whole of the settings step: a plugin is a program, so what executes one is
        somebody having looked at the list and said so.
        """
        session = await set_up(service, declaring)
        enrolled = registered_in(await service.checkpointer.load(session.id))
        assert enrolled is not None
        assert {each.qualified for each in enrolled} == {"bundled:handoff", "bundled:guidance"}

    async def test_a_plugin_left_off_is_never_run_at_all(self, service: Service, declaring: Declaring) -> None:
        """
        Not merely contributing nothing: the switch decides which programs are executed, so a plugin
        somebody turned off is absent from the registration because it was never asked anything.
        """
        session = await service.start(DEFAULT_CHOICE)
        # With `tendings`, because the switch is a column and the *pass* is what reads it now: what
        # decides which plugins are set up is what the press wrote, and a pass given no way to read
        # that column would set every declared plugin up regardless.
        body = conversing(Provider().endpoints(), INSTRUCTIONS, declaring=declaring, tendings=self.tending(service))
        await passing(service, session.id, body)
        await replace(service, declaring=declaring).settle(session.id, {"bundled:handoff": False})
        await passing(service, session.id, body)
        enrolled = registered_in(await service.checkpointer.load(session.id))
        assert enrolled is not None
        assert [each.qualified for each in enrolled] == ["bundled:guidance"]

    async def test_a_console_with_no_plugins_still_records_that_it_looked(self, service: Service) -> None:
        """
        Which is what makes an empty declaration mean "this session has looked" rather than "nobody
        has looked": without the write there is no way to tell a console with none from a session
        whose worktree is still being planted.
        """
        session = await service.start(DEFAULT_CHOICE)
        await passing(service, session.id, conversing(Provider().endpoints(), INSTRUCTIONS))
        assert declared_in(await service.checkpointer.load(session.id)) == ()

    async def test_a_plugins_tool_is_in_the_prefix_and_its_answer_is_recorded(
        self, service: Service, declaring: Declaring
    ) -> None:
        """
        **Tools are the safest thing a plugin can contribute, not a forbidden one.**
        `wrap_tool_execute` wraps every call in a step, so a plugin-provided tool's answer is recorded
        and a resumed pass replays it without running the script again.
        """
        session = await set_up(service, declaring)
        await service.say(session.id, "hello")
        written = "x" * 400
        # Where the notes a plugin asked for go, collected rather than delivered: what this asserts
        # is that the tool's `deliver` reached the console, and an inbox would be a second thing to
        # read it back out of.
        delivered: list[records.Note] = []

        async def collecting(into: str, note: records.Note) -> None:
            delivered.append(note)

        scripted = Scripted(
            script=(
                ModelResponse(
                    parts=[ToolCallPart(tool_name="hand_off", args={"document": written}, tool_call_id="c1")]
                ),
                ModelResponse(parts=[TextPart("done")]),
            )
        )
        body = conversing(scripted.endpoints(), INSTRUCTIONS, declaring=declaring, delivering=collecting)
        await passing(service, session.id, body)

        recorded = await service.checkpointer.load(session.id)
        assert any(key.endswith(":tool:c1") for key in recorded), "the call is a recorded step"
        assert [each.said for each in delivered] == [written]
        assert delivered[0].plugin == "bundled:handoff", "attributed to whoever asked for it"
        assert delivered[0].forget is True, "and it starts the model's history again"

    async def test_a_session_that_loaded_none_of_them_answers_all_the_same(
        self, service: Service, declaring: Declaring
    ) -> None:
        """No tool in the prefix, no card, no answer in the composer, and no events."""
        session = await service.start(DEFAULT_CHOICE)
        tendings = self.tending(service)
        declaring_body = conversing(Provider().endpoints(), INSTRUCTIONS, declaring=declaring, tendings=tendings)
        await passing(service, session.id, declaring_body)
        off = {"bundled:handoff": False, "bundled:guidance": False}
        await replace(service, declaring=declaring).settle(session.id, off)
        await passing(service, session.id, declaring_body)
        await service.say(session.id, "hello")
        scripted = Scripted(script=(ModelResponse(parts=[TextPart("done")]),))
        body = conversing(scripted.endpoints(), INSTRUCTIONS, declaring=declaring, tendings=tendings)
        await passing(service, session.id, body)
        # The turn answered, which is what says the agent was built at all, and it was built with no
        # plugin toolset: a `hand_off` in the prefix would have been offered to the stand-in.
        assert registered_in(await service.checkpointer.load(session.id)) == ()

    def tending(self, service: Service) -> Any:
        async def read(session: str) -> Any:
            return await read_tending(service.database, session)

        return read


class TestARepositorysOwnPlugin:
    """
    The tier that is somebody else's code, run behind the namespace `bash` already uses.

    The fixture is a **bash** plugin, deliberately: it is the one in this repository that is none of
    Python, none of ours and none of the console's own tiers, so it proves the three claims that are
    otherwise only asserted in prose - any language, reaches nothing it was not handed, and a
    repository's own script can contribute to what a session is told.
    """

    @pytest.fixture
    def bwrap(self) -> str:
        """
        Where the sandbox is, and a loud failure if it is not anywhere.

        Not skipped when it is missing, for the reason `test_sandbox.py` is not skipped: a check
        nobody runs is a check that catches nothing, and what is under test here is what a mount
        namespace actually does.
        """
        return sandbox_command()

    @pytest.fixture
    async def declaring_repository(self, worktree: Any, tmp_path: Path, bwrap: str) -> Declaring:
        """The fixture repository, carrying a declaration and the script it names, both committed."""
        root = worktree.root
        (root / ".mainplate").mkdir()
        (root / ".mainplate" / "mainplate.yaml").write_text("plugins:\n  git-status: .mainplate/git-status\n")
        script = root / ".mainplate" / "git-status"
        script.write_bytes((FIXTURES / "git-status").read_bytes())
        script.chmod(0o755)
        await run("git", "add", "-A", cwd=root)
        await run("git", "commit", "-qm", "carry a plugin", cwd=root)
        return Declaring(
            console=(),
            speaking=Spawned(bwrap=bwrap, scratch=tmp_path / "scratch", environ={}),
            confining=True,
        )

    async def test_a_trusted_repositorys_plugin_runs_and_contributes_what_it_says(
        self, service: Service, workspaces: Workspaces, declaring_repository: Declaring
    ) -> None:
        """
        The whole path in one: the worktree is planted, the declaration is read out of it, the script
        runs behind the sandbox, and what it says lands in the session's instructions.
        """
        planting = replace(service, workspaces=workspaces)
        session = await set_up(
            planting, declaring_repository, replace(DEFAULT_CHOICE, repository=FIXTURE), workspaces=workspaces
        )

        enrolled = registered_in(await planting.checkpointer.load(session.id))
        assert enrolled is not None
        assert [each.qualified for each in enrolled] == ["repository:git-status"]
        said = enrolled[0].described.instructions
        assert said is not None
        assert "Current git status:" in said

    async def test_a_session_that_does_not_trust_it_reads_nothing_at_all(
        self, service: Service, workspaces: Workspaces, declaring_repository: Declaring
    ) -> None:
        """
        And the recorded set is empty for that session's life. Trusting afterwards reaches sessions
        started after it and none before, which is the answer `Choice` gives to every other question.
        """
        planting = replace(service, workspaces=workspaces)
        session = await set_up(
            planting,
            declaring_repository,
            replace(DEFAULT_CHOICE, repository=FIXTURE, trusted=False),
            workspaces=workspaces,
        )
        assert declared_in(await planting.checkpointer.load(session.id)) == (), "nothing was even read"
        assert registered_in(await planting.checkpointer.load(session.id)) == ()

    async def test_a_console_with_no_sandbox_runs_none_of_them(
        self, service: Service, workspaces: Workspaces, declaring_repository: Declaring
    ) -> None:
        """
        A refusal rather than a fallback: a repository's plugin is safe to run because the process is
        confined, so a console that cannot confine one has nothing to offer in its place.
        """
        planting = replace(service, workspaces=workspaces)
        session = await set_up(
            planting,
            replace(declaring_repository, confining=False),
            replace(DEFAULT_CHOICE, repository=FIXTURE),
            workspaces=workspaces,
        )
        assert declared_in(await planting.checkpointer.load(session.id)) == ()
        assert registered_in(await planting.checkpointer.load(session.id)) == ()


class TestThisRepositorysOwnPlugin:
    """
    `.mainplate/pre-commit`, which is this repository asking its own sessions to run its own hooks.

    **Run over a pipe like any other, and never installed by these tests.** What its `setup` does is
    fetch: an interpreter, `pre-commit`, and a hook environment per entry in the config. That is
    minutes on a cold cache and a dependency on an index, so what is asserted here is everything
    *around* the fetch, with a stub standing in for the hooks themselves.

    The fetch is not left unproven, it is proven elsewhere and by hand: `docs/design/plugins.md`
    records what one run costs, and the claim that a confined plugin can install at `setup` and use
    it at a turn boundary is `TestWhereARepositorysPluginRuns`' to make, against the arguments this
    console passes rather than against PyPI.
    """

    @pytest.fixture
    def plugin(self) -> Path:
        return Path(__file__).parent.parent / ".mainplate" / "pre-commit"

    @pytest.fixture
    async def repository(self, tmp_path: Path) -> Path:
        """A worktree with a change in it and a `pre-commit` that is a stub, not an install."""
        root = tmp_path / "repo"
        root.mkdir()
        await run("git", "init", "-q", "--initial-branch=main", ".", cwd=root)
        await run("git", "config", "user.email", "fixture@example.com", cwd=root)
        await run("git", "config", "user.name", "fixture", cwd=root)
        (root / ".pre-commit-config.yaml").write_text("repos: []\n")
        await run("git", "add", "-A", cwd=root)
        await run("git", "commit", "-qm", "first", cwd=root)
        return root

    def payload(self, plugin: Path, repository: Path, **fields: object) -> dict[str, object]:
        return {
            "session": "a-session",
            "plugin": "repository:pre-commit",
            "worktree": str(repository),
            "scratch": str(repository.parent / "scratch"),
            **fields,
        }

    def stubbed(self, repository: Path, *exits: int) -> None:
        """
        A `pre_commit` module on the path that exits as told, once per run, and says which run it was.

        The two runs are the shape being tested: the first is what fails, the second is what says
        whether a hook fixed it. A stub is what lets both be driven without an index anywhere near it.
        """
        module = repository.parent / "stub" / "pre_commit"
        module.mkdir(parents=True, exist_ok=True)
        (module / "__init__.py").write_text("")
        codes = ", ".join(str(each) for each in exits)
        (module / "__main__.py").write_text(
            "import sys\n"
            "from pathlib import Path\n"
            f"codes = [{codes}]\n"
            "counted = Path(__file__).parent / 'runs'\n"
            "at = int(counted.read_text()) if counted.exists() else 0\n"
            "counted.write_text(str(at + 1))\n"
            "print(f'run {at}: ' + ' '.join(sys.argv[1:]))\n"
            "raise SystemExit(codes[at] if at < len(codes) else codes[-1])\n"
        )

    async def asked(self, plugin: Path, repository: Path, payload: Mapping[str, object]) -> Any:
        """
        The plugin, run for real, with its dependency answered by the stub rather than by an install.

        `PYTHONPATH` is what puts the stub in front of anything else, and `uv run --script` keeps the
        environment it was given, so the shebang resolves an interpreter and imports this.
        """
        installed = Installed(tier=Tier.USER, name="pre-commit", path=plugin)
        environ = {
            "PATH": os.environ["PATH"],
            "HOME": os.environ["HOME"],
            "PYTHONPATH": str(repository.parent / "stub"),
        }
        return await Spawned(environ=environ)(installed, payload)

    async def test_a_repository_with_no_config_gets_a_plugin_that_contributes_nothing(
        self, plugin: Path, repository: Path
    ) -> None:
        """
        Rather than a session that refuses to start. A fork of this repository, or a branch part way
        through adding `pre-commit`, is an ordinary thing to open a session on.
        """
        (repository / ".pre-commit-config.yaml").unlink()
        assert await self.asked(plugin, repository, self.payload(plugin, repository, event="setup")) == {}

    async def test_it_contributes_a_tool_an_answer_and_a_card_of_both_controls(
        self, plugin: Path, repository: Path
    ) -> None:
        """
        And the tool is the one that could not be left out: this plugin holds the only `pre-commit` a
        session can reach, since the model's own `bash` has no network to install one and no way into
        this plugin's scratch.
        """
        self.stubbed(repository, 0)
        said = await self.asked(plugin, repository, self.payload(plugin, repository, event="setup"))
        described = parse_described("repository:pre-commit", said)

        assert set(described.events) == {"tool", "after_turn", "compose", "action"}
        assert [each.name for each in described.tools] == ["run"]
        assert [each.leader for each in described.answers] == ["run"]
        assert described.settings == {"checks": True, "most": 3}

    async def test_hooks_that_will_not_install_fail_the_setup_rather_than_contributing_nothing(
        self, plugin: Path, repository: Path
    ) -> None:
        """
        An empty answer would be this plugin declaring itself to have nothing to offer, which is a
        session running without the checks this repository asked for and saying so nowhere. What the
        console does with a non-zero exit is put the sentence on the settings step, beside the switch
        that turns this off.
        """
        self.stubbed(repository, 2)
        with pytest.raises(PluginFailed, match="exited 1"):
            await self.asked(plugin, repository, self.payload(plugin, repository, event="setup"))

    async def test_a_turn_that_changed_nothing_runs_no_hooks_at_all(self, plugin: Path, repository: Path) -> None:
        """A clean tree has nothing to pass hooks over, and running them to say so is the slow way."""
        self.stubbed(repository, 1, 1)
        said = await self.asked(
            plugin,
            repository,
            self.payload(
                plugin,
                repository,
                event="after_turn",
                turn=1,
                opened_on={"kind": "prompt"},
                settings={"checks": True, "most": 3},
                state={},
            ),
        )
        assert said == {}, "which is also the stub never having been reached"

    async def test_a_failing_hook_is_delivered_with_what_the_second_run_still_says(
        self, plugin: Path, repository: Path
    ) -> None:
        """
        Twice where the first fails, which is the autofix loop the shell hook had: most of what
        `pre-commit` reports is a hook that has already fixed the file, and the second run is what
        separates that from what a person has to decide about.
        """
        self.stubbed(repository, 1, 1)
        (repository / "written.py").write_text("x = 1\n")
        said = parse_answer(
            "repository:pre-commit",
            await self.asked(
                plugin,
                repository,
                self.payload(
                    plugin,
                    repository,
                    event="after_turn",
                    turn=1,
                    opened_on={"kind": "prompt"},
                    settings={"checks": True, "most": 3},
                    state={},
                ),
            ),
        )
        assert len(said.deliver) == 1
        assert "run 1: run --files written.py" in said.deliver[0].said, "the second run's output, not the first's"
        assert said.deliver[0].tone == "quiet"
        assert said.setting == {"chasing": 1}

    async def test_a_run_that_only_fixed_things_says_nothing_at_a_turn_boundary(
        self, plugin: Path, repository: Path
    ) -> None:
        """
        Delivering opens a turn, so announcing work that is already done costs a model request to say
        "carry on" - and a model that goes on to edit a file the hooks rewrote finds out anyway,
        because `edit` is anchored on what was read.
        """
        self.stubbed(repository, 1, 0)
        (repository / "written.py").write_text("x = 1\n")
        said = parse_answer(
            "repository:pre-commit",
            await self.asked(
                plugin,
                repository,
                self.payload(
                    plugin,
                    repository,
                    event="after_turn",
                    turn=1,
                    opened_on={"kind": "prompt"},
                    settings={"checks": True, "most": 3},
                    state={},
                ),
            ),
        )
        assert said.deliver == ()

    async def test_somebody_asking_is_answered_whether_or_not_anything_is_failing(
        self, plugin: Path, repository: Path
    ) -> None:
        """A control somebody pressed with no visible effect is a control that looks broken."""
        self.stubbed(repository, 1, 0)
        (repository / "written.py").write_text("x = 1\n")
        said = parse_answer(
            "repository:pre-commit",
            await self.asked(
                plugin,
                repository,
                self.payload(
                    plugin, repository, event="compose", leader="run", said="", settings={"checks": True, "most": 3}
                ),
            ),
        )
        assert len(said.deliver) == 1
        assert "fixed some of the files" in said.deliver[0].said
        assert said.setting == {"chasing": 0}, "and asking is somebody taking an interest, which resets the bound"

    async def test_the_switch_being_off_runs_nothing(self, plugin: Path, repository: Path) -> None:
        self.stubbed(repository, 1, 1)
        (repository / "written.py").write_text("x = 1\n")
        said = await self.asked(
            plugin,
            repository,
            self.payload(
                plugin,
                repository,
                event="after_turn",
                turn=1,
                opened_on={"kind": "prompt"},
                settings={"checks": False, "most": 3},
                state={},
            ),
        )
        assert said == {}

    async def test_it_stops_chasing_one_failure_after_the_bound_and_starts_again_when_somebody_speaks(
        self, plugin: Path, repository: Path
    ) -> None:
        """
        **The number that is here because a console is not a terminal.** The hook this came from
        re-fired on every stop for free, since the thing it interrupted was a person; here each
        delivery opens a turn, so a hook the model cannot satisfy would bill for itself until somebody
        noticed.
        """
        self.stubbed(repository, 1, 1)
        (repository / "written.py").write_text("x = 1\n")

        async def ending(opened_on: Mapping[str, object], chasing: int) -> Any:
            return await self.asked(
                plugin,
                repository,
                self.payload(
                    plugin,
                    repository,
                    event="after_turn",
                    turn=4,
                    opened_on=opened_on,
                    settings={"checks": True, "most": 2},
                    state={"chasing": chasing},
                ),
            )

        mine = {"kind": "note", "plugin": "repository:pre-commit"}
        assert await ending(mine, 2) == {}, "the bound is reached, so it goes quiet rather than billing again"
        assert await ending({"kind": "prompt"}, 2) != {}, "and a person saying anything starts it over"

    async def test_turning_the_switch_back_on_starts_the_chase_again(self, plugin: Path, repository: Path) -> None:
        """A switch flicked off through a long refactor and back on behaves like a fresh session."""
        said = await self.asked(
            plugin, repository, self.payload(plugin, repository, event="action", control="checks", value=True)
        )
        assert said == {"set": {"chasing": 0}}

    async def test_this_repository_declares_it_at_the_path_it_is_actually_at(self) -> None:
        """
        The one thing a rename breaks silently: the declaration and the file are two places, and a
        session on this repository would fail its own setup rather than say so here.
        """
        here = Path(__file__).parent.parent
        declared = repository_plugins(here)
        assert [each.name for each in declared] == ["pre-commit"]
        assert declared[0].path.is_file()
        assert os.access(declared[0].path, os.X_OK), "and a plugin that is not executable is one nothing can run"
