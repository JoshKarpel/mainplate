from __future__ import annotations

import asyncio
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
from conftest import run
from conftest import started
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ToolCallPart
from without_durability.interfaces import claimed
from without_durability.stepwise import Blocked
from without_durability.stepwise import resume

from mainplate import records
from mainplate.conversation import PLUGINS_KEY
from mainplate.conversation import REPOSITORY_PLUGINS_KEY
from mainplate.conversation import conversing
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
        The rule the shape cannot express, checked at `describe` rather than at render.

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
        on, off = running(enrolled, TENDED)
        assert [each.qualified for each in on] == ["bundled:guidance", "user:guidance"]
        assert off == ()

    def test_a_switch_a_session_recorded_wins_over_the_default(self) -> None:
        enrolled = (
            Enrolled(installed=Installed(tier=Tier.BUNDLED, name="handoff", path=Path("/a")), described=Described()),
        )
        on, off = running(enrolled, TENDED.switched("bundled:handoff", False))
        assert on == ()
        assert [each.qualified for each in off] == ["bundled:handoff"]

    def test_the_settings_step_draws_every_tier_including_the_empty_ones(self) -> None:
        """
        So the step is the same shape on every session and the flow can be learned and tested as one
        thing rather than as however many lists a repository happens to produce.
        """
        enrolled = (
            Enrolled(installed=Installed(tier=Tier.BUNDLED, name="handoff", path=Path("/a")), described=Described()),
        )
        assert [tier for tier, _ in grouped(enrolled)] == list(Tier)


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


class TestRunningOne:
    """A plugin is a single executable, spoken to over a pipe. That is the whole contract."""

    async def test_a_plugin_that_is_not_there_fails_naming_it(self) -> None:
        installed = Installed(tier=Tier.USER, name="absent", path=Path("/nowhere/at/all"))
        with pytest.raises(PluginFailed, match="user:absent"):
            await Spawned(environ={})(installed, spoken(event="describe"))

    async def test_a_plugin_that_exits_non_zero_fails_carrying_what_it_said(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken"
        broken.write_text("#!/bin/sh\necho 'it went wrong' >&2\nexit 3\n")
        broken.chmod(0o755)
        with pytest.raises(PluginFailed, match="it went wrong"):
            await asked(broken, spoken(event="describe"))

    async def test_a_plugin_that_prints_something_that_is_not_json_fails_naming_it(self, tmp_path: Path) -> None:
        noisy = tmp_path / "noisy"
        noisy.write_text("#!/bin/sh\ncat >/dev/null\necho 'not json at all'\n")
        noisy.chmod(0o755)
        with pytest.raises(PluginFailed, match="not JSON"):
            await asked(noisy, spoken(event="describe"))

    async def test_what_a_plugin_prints_on_stderr_is_kept_out_of_its_answer(self, tmp_path: Path) -> None:
        """
        A `print` left in while debugging is not a protocol error, which is why the two streams stay
        apart - unlike a command a person runs, where the interleaving is the answer.
        """
        chatty = tmp_path / "chatty"
        chatty.write_text("#!/bin/sh\ncat >/dev/null\necho 'still here' >&2\necho '{\"events\": []}'\n")
        chatty.chmod(0o755)
        assert await asked(chatty, spoken(event="describe")) == {"events": []}

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
        asking = asyncio.ensure_future(Spawned(environ={})(installed, spoken(event="describe")))
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
        assert await asked(echoing, spoken(event="describe", worktree="/somewhere")) == spoken(
            event="describe", worktree="/somewhere"
        )


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
        described = parse_described("bundled:handoff", await asked(handoff, spoken(event="describe")))
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
        described = parse_described("bundled:guidance", await asked(guidance, spoken(event="describe")))
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
            "bundled:guidance", await asked(guidance, spoken(event="describe", worktree=str(repository)))
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
            "bundled:guidance", await asked(guidance, spoken(event="describe", worktree=str(repository)))
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


class TestASessionsPlugins:
    """What a pass does about them: register once, run what is on, and record what they asked for."""

    @pytest.fixture
    def declaring(self) -> Declaring:
        return Declaring(console=bundled(), speaking=Spawned(environ={}))

    async def test_the_first_pass_registers_and_then_waits(self, service: Service, declaring: Declaring) -> None:
        """
        **The first pass of a session answers nothing**, which is the shape rather than an accident:
        it plants, asks every plugin what it is, records both, and comes back `Blocked` on an empty
        inbox. That is `planting` moving above `opening_turn` and nothing else.
        """
        session = await service.start(DEFAULT_CHOICE)
        provider = Provider()
        body = conversing(provider.endpoints(), INSTRUCTIONS, declaring=declaring)
        holder = await claimed(service.checkpointer, session.id)
        try:
            ended = await resume(holder, service.checkpointer, body)
        finally:
            await service.checkpointer.release(holder)

        assert isinstance(ended, Blocked), "it is waiting on a message rather than finished"
        assert provider.asked == 0, "and it asked no provider anything"
        recorded = await service.checkpointer.load(session.id)
        assert PLUGINS_KEY in recorded
        assert REPOSITORY_PLUGINS_KEY in recorded
        enrolled = registered_in(recorded)
        assert enrolled is not None
        assert {each.qualified for each in enrolled} == {"bundled:handoff", "bundled:guidance"}

    async def test_a_console_with_no_plugins_still_records_that_it_looked(self, service: Service) -> None:
        """
        Which is what makes an empty registration mean "this session is set up" rather than "nobody
        has looked": without the write there is no way to tell a console with none from a session
        whose worktree is still being planted.
        """
        session = await service.start(DEFAULT_CHOICE)
        body = conversing(Provider().endpoints(), INSTRUCTIONS)
        holder = await claimed(service.checkpointer, session.id)
        try:
            await resume(holder, service.checkpointer, body)
        finally:
            await service.checkpointer.release(holder)
        assert registered_in(await service.checkpointer.load(session.id)) == ()

    async def test_a_plugins_tool_is_in_the_prefix_and_its_answer_is_recorded(
        self, service: Service, declaring: Declaring
    ) -> None:
        """
        **Tools are the safest thing a plugin can contribute, not a forbidden one.**
        `wrap_tool_execute` wraps every call in a step, so a plugin-provided tool's answer is recorded
        and a resumed pass replays it without running the script again.
        """
        session = await started(service, "hello")
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
        holder = await claimed(service.checkpointer, session.id)
        try:
            await resume(holder, service.checkpointer, body)
        finally:
            await service.checkpointer.release(holder)

        recorded = await service.checkpointer.load(session.id)
        assert any(key.endswith(":tool:c1") for key in recorded), "the call is a recorded step"
        assert [each.said for each in delivered] == [written]
        assert delivered[0].plugin == "bundled:handoff", "attributed to whoever asked for it"
        assert delivered[0].forget is True, "and it starts the model's history again"

    async def test_a_plugin_that_is_off_contributes_nothing_at_all(
        self, service: Service, declaring: Declaring
    ) -> None:
        """No tool in the prefix, no card, no answer in the composer, and no events."""
        session = await started(service, "hello")
        await service.switch(session.id, {"bundled:handoff": False, "bundled:guidance": False})
        scripted = Scripted(script=(ModelResponse(parts=[TextPart("done")]),))
        body = conversing(scripted.endpoints(), INSTRUCTIONS, declaring=declaring, tendings=self.tending(service))
        holder = await claimed(service.checkpointer, session.id)
        try:
            await resume(holder, service.checkpointer, body)
        finally:
            await service.checkpointer.release(holder)
        # The turn answered, which is what says the agent was built at all, and it was built with no
        # plugin toolset: a `hand_off` in the prefix would have been offered to the stand-in.
        assert registered_in(await service.checkpointer.load(session.id)) is not None

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
        session = await started(planting, "hello", replace(DEFAULT_CHOICE, repository=FIXTURE))
        body = conversing(Provider().endpoints(), INSTRUCTIONS, workspaces, declaring=declaring_repository)
        holder = await claimed(planting.checkpointer, session.id)
        try:
            await resume(holder, planting.checkpointer, body)
        finally:
            await planting.checkpointer.release(holder)

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
        session = await started(planting, "hello", replace(DEFAULT_CHOICE, repository=FIXTURE, trusted=False))
        body = conversing(Provider().endpoints(), INSTRUCTIONS, workspaces, declaring=declaring_repository)
        holder = await claimed(planting.checkpointer, session.id)
        try:
            await resume(holder, planting.checkpointer, body)
        finally:
            await planting.checkpointer.release(holder)
        assert registered_in(await planting.checkpointer.load(session.id)) == ()

    async def test_a_console_with_no_sandbox_runs_none_of_them(
        self, service: Service, workspaces: Workspaces, declaring_repository: Declaring
    ) -> None:
        """
        A refusal rather than a fallback: a repository's plugin is safe to run because the process is
        confined, so a console that cannot confine one has nothing to offer in its place.
        """
        planting = replace(service, workspaces=workspaces)
        session = await started(planting, "hello", replace(DEFAULT_CHOICE, repository=FIXTURE))
        body = conversing(
            Provider().endpoints(),
            INSTRUCTIONS,
            workspaces,
            declaring=replace(declaring_repository, confining=False),
        )
        holder = await claimed(planting.checkpointer, session.id)
        try:
            await resume(holder, planting.checkpointer, body)
        finally:
            await planting.checkpointer.release(holder)
        assert registered_in(await planting.checkpointer.load(session.id)) == ()
