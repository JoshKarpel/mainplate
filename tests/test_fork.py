from __future__ import annotations

import pytest
from calling import calling
from conftest import DEFAULT_CHOICE
from conftest import WHEN
from conftest import Provider
from conftest import ran_at
from conftest import said_at
from conftest import started
from test_conversation import pass_at
from without_asgi import ASGIApp
from without_durability.interfaces import inbox_key
from without_durability.stepwise import Blocked

from mainplate.agent import Choice
from mainplate.conversation import CHOICE_KEY
from mainplate.conversation import Prose
from mainplate.conversation import before
from mainplate.conversation import choice_of
from mainplate.conversation import instructions_key
from mainplate.conversation import messages_key
from mainplate.conversation import opened_key
from mainplate.conversation import result_key
from mainplate.conversation import transcript
from mainplate.conversation import turn_of
from mainplate.pages import arrange
from mainplate.pages import inheriting
from mainplate.service import Service
from mainplate.sessions import Origin
from mainplate.sessions import Session


class TestReadingAKeyBack:
    @pytest.mark.parametrize(
        ("key", "turn"),
        [
            ("turn:0:prompt", 0),
            ("turn:7:messages", 7),
            ("turn:12:model:3", 12),
            ("turn:12:tree:3", 12),
            # Named by the call's own id rather than by a position, so the last segment is not a
            # number and this still has to read the *shape* rather than parse the whole key.
            ("turn:4:tool:toolu_017", 4),
            # A step kind nothing writes yet, which is the point of reading the shape rather than a
            # list of known kinds: a fork carries the whole of a turn without being taught each new
            # one. Tool keys were this case until they arrived, and needed no change here.
            ("turn:9:approval:0", 9),
            ("choice", None),
            ("turn:notanumber:prompt", None),
            ("nonsense", None),
        ],
    )
    def test_a_key_says_which_turn_it_belongs_to(self, key: str, turn: int | None) -> None:
        assert turn_of(key) == turn

    def test_a_prefix_carries_every_kind_of_key_for_the_turns_it_covers(self) -> None:
        recorded: dict[str, object] = {
            CHOICE_KEY: {"endpoint": "here", "model": "ripe/fast"},
            **said_at(0, "first"),
            messages_key(0): [],
            "turn:0:model:0": {"kind": "response"},
            "turn:0:tree:0": "a1b2c3",
            "turn:0:tool:toolu_017": "noted",
            **said_at(1, "second"),
            messages_key(1): [],
            **said_at(2, "third"),
        }
        carried = before(recorded, 2)

        assert set(carried) == {
            inbox_key(0),
            opened_key(0),
            messages_key(0),
            "turn:0:model:0",
            "turn:0:tree:0",
            "turn:0:tool:toolu_017",
            inbox_key(1),
            opened_key(1),
            messages_key(1),
        }
        assert CHOICE_KEY not in carried, "the branch answers the choice itself"

    def test_a_prefix_leaves_the_instructions_behind_so_a_branch_composes_its_own(self) -> None:
        """
        What the key's shape is for, and the reason it is not `turn:{n}:instructions`.

        `before` carries turn-prefixed keys across by shape, so a turn-shaped name would hand a
        branch its parent's system prompt - and a fork may *attach* a repository the parent never
        had, whose guidance and index would then be missing from words the branch is answered under.
        """
        recorded: dict[str, object] = {
            CHOICE_KEY: {"endpoint": "here", "model": "ripe/fast"},
            instructions_key(0): {"kind": "instructions", "said": "what the parent was answered under"},
            **said_at(0, "first"),
            messages_key(0): [],
            **said_at(1, "second"),
        }

        carried = before(recorded, 1)

        assert instructions_key(0) not in carried
        assert messages_key(0) in carried, "the control: the turns themselves do come across"

    def test_a_prefix_carries_a_command_and_its_result_and_stops_at_the_branch_point(self) -> None:
        """
        The second rule `before` needs, and the price the inbox charges: an entry says nothing about
        which turn it is in, so what decides is where it sits against the entry the branch opened on.
        A result travels with the command it answers, by the same shape rule.
        """
        recorded: dict[str, object] = {
            **said_at(0, "first"),
            **ran_at(1, "git status"),
            result_key(inbox_key(1)): {"kind": "result", "status": 0, "output": ""},
            messages_key(0): [],
            **said_at(1, "second", entry=2),
            **ran_at(3, "git diff"),
        }
        carried = before(recorded, 1)

        assert set(carried) == {inbox_key(0), opened_key(0), inbox_key(1), result_key(inbox_key(1)), messages_key(0)}


class TestWhatABranchInherits:
    async def test_it_carries_the_turns_before_the_branch_and_none_after(
        self, service: Service, provider: Provider
    ) -> None:
        body = provider.body()
        session = await started(service, "first", DEFAULT_CHOICE)
        await pass_at(service, body, session.id)
        await service.say(session.id, "second")
        await pass_at(service, body, session.id)

        forked = await service.fork(session.id, at=1, chosen=DEFAULT_CHOICE)

        assert forked is not None
        said = transcript(await service.checkpointer.load(forked.id))
        spoken = [block.text for panel in said.panels for block in panel.blocks if isinstance(block, Prose)]
        assert spoken == ["first", "answer 1"]
        assert said.turns == 1, "turn 1 is the slot the branch is waiting to be told"
        assert not said.awaiting

    async def test_the_parent_is_untouched_by_being_branched(self, service: Service, provider: Provider) -> None:
        body = provider.body()
        session = await started(service, "first", DEFAULT_CHOICE)
        await pass_at(service, body, session.id)
        was = await service.checkpointer.load(session.id)

        await service.fork(session.id, at=0, chosen=DEFAULT_CHOICE)

        assert await service.checkpointer.load(session.id) == was

    async def test_a_branch_may_answer_on_a_different_model(self, service: Service, provider: Provider) -> None:
        """The one moment a choice may differ, which is the whole reason forking exists here."""
        elsewhere = Choice(endpoint="gateway", model="wide/steady", thinking="xhigh")
        session = await started(service, "first", DEFAULT_CHOICE)

        forked = await service.fork(session.id, at=0, chosen=elsewhere)

        assert forked is not None
        assert choice_of(await service.checkpointer.load(forked.id)) == elsewhere
        assert choice_of(await service.checkpointer.load(session.id)) == DEFAULT_CHOICE

    async def test_forking_a_turn_asks_it_again_rather_than_making_you_retype_it(
        self, service: Service, provider: Provider
    ) -> None:
        """
        The point of the branch point being *before* the message rather than after it. Forking a
        turn to see it answered differently must not first make you retype the question, because
        by the time you have retyped it, it is a different question.
        """
        body = provider.body()
        session = await started(service, "what is a mainplate", DEFAULT_CHOICE)
        await pass_at(service, body, session.id)

        asked = transcript(await service.checkpointer.load(session.id)).asked_at(0)
        forked = await service.fork(session.id, at=0, chosen=DEFAULT_CHOICE, said=asked)

        assert forked is not None
        said = transcript(await service.checkpointer.load(forked.id))
        assert said.asked_at(0) == "what is a mainplate"
        assert said.awaiting, "the fork is queued to answer it, not sitting waiting to be told"

    async def test_the_message_may_be_edited_on_the_way_across(self, service: Service) -> None:
        """The other reason to fork a turn: not to re-run it, but to rephrase it."""
        session = await started(service, "what is a mainplate", DEFAULT_CHOICE)

        forked = await service.fork(session.id, at=0, chosen=DEFAULT_CHOICE, said="what is an escapement")

        assert forked is not None
        assert transcript(await service.checkpointer.load(forked.id)).asked_at(0) == "what is an escapement"

    async def test_forking_the_end_of_a_conversation_waits_rather_than_asking(
        self, service: Service, provider: Provider
    ) -> None:
        """Nothing to re-ask, so nothing is queued and no worker will take it."""
        body = provider.body()
        session = await started(service, "first", DEFAULT_CHOICE)
        await pass_at(service, body, session.id)

        forked = await service.fork(session.id, at=1, chosen=DEFAULT_CHOICE, said=None)

        assert forked is not None
        assert not transcript(await service.checkpointer.load(forked.id)).awaiting

    async def test_the_form_offers_the_turn_s_own_message_back(self, app: ASGIApp, service: Service) -> None:
        session = await started(service, "what is a mainplate", DEFAULT_CHOICE)

        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session.id}/forks/new?at=0")

        assert "what is a mainplate</textarea>" in answered.text

    async def test_a_branch_records_where_it_came_from(self, service: Service) -> None:
        session = await started(service, "first", DEFAULT_CHOICE)

        forked = await service.fork(session.id, at=3, chosen=DEFAULT_CHOICE)

        assert forked is not None
        assert forked.forked == Origin(session=session.id, turn=3)
        assert (await service.read(forked.id)) is not None

    async def test_a_branch_of_a_session_nobody_has_is_nothing_rather_than_an_empty_one(self, service: Service) -> None:
        assert await service.fork("no-such-session", at=0, chosen=DEFAULT_CHOICE) is None

    async def test_a_branch_is_answered_from_the_history_it_carried(self, service: Service, provider: Provider) -> None:
        """
        What the copy is *for*: the branch's first pass must see the turns it inherited, not start
        a fresh conversation that merely looks like one.
        """
        body = provider.body()
        session = await started(service, "first", DEFAULT_CHOICE)
        await pass_at(service, body, session.id)
        forked = await service.fork(session.id, at=1, chosen=DEFAULT_CHOICE)
        assert forked is not None

        await service.say(forked.id, "a different second")
        assert await pass_at(service, body, forked.id) == Blocked(listening=frozenset({opened_key(2)}))

        # Two messages of history plus the new prompt: the branch continued rather than restarted.
        assert provider.carried[-1] == 3


class TestArrangingTheTree:
    def branch(self, name: str, parent: str | None = None, turn: int = 0) -> Session:
        return Session(
            id=name,
            created_at=WHEN,
            title=name,
            forked=None if parent is None else Origin(session=parent, turn=turn),
        )

    def test_a_session_with_no_origin_sits_at_the_root(self) -> None:
        alone = self.branch("alone")
        assert arrange([alone]) == ((alone, 0),)

    def test_a_branch_sits_directly_under_what_it_branched_from(self) -> None:
        parent, child, unrelated = self.branch("parent"), self.branch("child", "parent"), self.branch("unrelated")

        assert arrange([parent, unrelated, child]) == ((parent, 0), (child, 1), (unrelated, 0))

    def test_a_branch_of_a_branch_steps_in_again(self) -> None:
        first, second, third = self.branch("first"), self.branch("second", "first"), self.branch("third", "second")

        assert arrange([first, second, third]) == ((first, 0), (second, 1), (third, 2))

    def test_depth_stops_growing_so_a_long_line_still_fits_the_sidebar(self) -> None:
        line = [self.branch("0")] + [self.branch(str(n), str(n - 1)) for n in range(1, 8)]

        assert [depth for _, depth in arrange(line)] == [0, 1, 2, 3, 3, 3, 3, 3]

    def test_a_branch_whose_parent_is_gone_is_drawn_rather_than_hidden(self) -> None:
        """Losing the parent must not lose a conversation somebody can still read."""
        orphan = self.branch("orphan", "long-deleted")

        assert arrange([orphan]) == ((orphan, 0),)


class TestSayingWhatCarriesOver:
    @pytest.mark.parametrize(
        ("at", "asking", "reads"),
        [
            (0, True, "Carries nothing, then asks turn 0 again."),
            (1, True, "Carries turn 0, then asks turn 1 again."),
            (4, True, "Carries turns 0 to 3, then asks turn 4 again."),
            # Forking the end of a conversation, where there is no turn to re-ask.
            (2, False, "Carries turns 0 to 1, then waits for turn 2."),
        ],
    )
    def test_the_sentence_reads_at_both_ends_of_the_range(self, at: int, asking: bool, reads: str) -> None:
        assert inheriting(at, asking) == reads


class TestBranchingThroughTheConsole:
    async def test_the_form_offers_the_session_s_own_choice_rather_than_the_default(
        self, app: ASGIApp, service: Service
    ) -> None:
        """Continuing on the same model is the common branch, so it is the one needing no change."""
        elsewhere = Choice(endpoint="gateway", model="wide/steady")
        session = await started(service, "first", elsewhere)

        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session.id}/forks/new?at=0")

        assert answered.status == 200
        assert 'value="gateway" checked' in answered.text

    async def test_branching_creates_a_session_and_sends_the_browser_to_it(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await started(service, "first", DEFAULT_CHOICE)

        async with calling(app) as caller:
            answered = await caller.post(
                f"/sessions/{session.id}/forks",
                {"at": "0", "endpoint": "gateway", "model": "wide/steady", "thinking": "low"},
            )

        assert answered.status == 303
        branched = answered.location.rsplit("/", 1)[-1]
        assert branched != session.id
        assert choice_of(await service.checkpointer.load(branched)) == Choice(
            endpoint="gateway", model="wide/steady", thinking="low"
        )

    async def test_a_pair_nothing_offers_is_refused_exactly_as_starting_one_is(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await started(service, "first", DEFAULT_CHOICE)

        async with calling(app) as caller:
            answered = await caller.post(
                f"/sessions/{session.id}/forks",
                {"at": "0", "endpoint": "here", "model": "nope", "thinking": "default"},
            )

        assert answered.status == 422

    async def test_a_turn_the_session_never_reached_is_refused(self, app: ASGIApp, service: Service) -> None:
        session = await started(service, "first", DEFAULT_CHOICE)

        async with calling(app) as caller:
            answered = await caller.get(f"/sessions/{session.id}/forks/new?at=99")

        assert answered.status == 404
