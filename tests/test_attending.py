# What the worker is doing about a session, and what a pass that fell over leaves behind.
#
# Two halves of one failure. A session whose pass raised recorded nothing, so the page drew the same
# three dots it draws for a reply being written and the two were indistinguishable for as long as the
# fault lasted. The reason is now in the checkpoint and the worker's standing is read beside it, and
# what these hold is that each says the right thing in every state - including the states where
# nothing is wrong, since a line that cried wolf through healthy turns would be worse than none.

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from conftest import DEFAULT_CHOICE
from conftest import WHEN
from conftest import passing
from conftest import started
from without_durability.interfaces import claimed
from without_durability.stepwise import Completed
from without_durability.stepwise import Run

from mainplate import records
from mainplate.app import reporting
from mainplate.conversation import Transcript
from mainplate.conversation import failed_key
from mainplate.conversation import failure_in
from mainplate.conversation import parse_failed
from mainplate.pages import waiting_for
from mainplate.service import Attended
from mainplate.service import Claimed
from mainplate.service import Conversation
from mainplate.service import Delayed
from mainplate.service import Idle
from mainplate.service import Queued
from mainplate.service import Service
from mainplate.service import attention_of
from mainplate.service import token_of
from mainplate.sessions import Session

NOW = 1_000_000.0
"""An arbitrary moment for the pure reading, in the store's own unit. Not zero, so a field left
unset would read as `now` rather than as something distinguishable from it."""


class TestReadingWhatTheWorkerIsDoing:
    """
    The four states a session can be in with the queue, out of the two rows that decide them.

    Pure, over a reading already taken, which is what lets every arm be written down without a store:
    the arms are what the page matches on, and what they cost is a sentence each.
    """

    def test_a_live_claim_is_a_pass_in_flight(self) -> None:
        """
        And it settles the answer whatever the queue says, because the row beside a live claim is the
        delivery that pass is answering for.
        """
        attended = Attended(recorded=9, held_until=NOW + 120, due_at=NOW + 600, asked_at=NOW)

        assert attention_of(attended) == Claimed()

    def test_a_claim_whose_lease_has_run_out_is_not_one(self) -> None:
        """
        The claim is released by setting it to now, so `held_until` in the past is the ordinary state
        of a session nothing holds rather than an exceptional one.
        """
        attended = Attended(recorded=9, held_until=NOW - 1, due_at=NOW - 1, asked_at=NOW)

        assert attention_of(attended) == Queued()

    def test_a_delivery_already_due_is_queued(self) -> None:
        assert attention_of(Attended(recorded=2, held_until=None, due_at=NOW, asked_at=NOW)) == Queued()

    def test_a_delivery_held_back_says_how_long_for(self) -> None:
        """
        Which is what the worker leaving a failed pass's delivery unanswered produces, and the one arm
        with a figure on it.
        """
        attended = Attended(recorded=2, held_until=None, due_at=NOW + 504, asked_at=NOW)

        assert attention_of(attended) == Delayed(until=timedelta(seconds=504))

    def test_no_row_at_all_is_a_session_nothing_is_coming_for(self) -> None:
        assert attention_of(Attended(recorded=2, held_until=None, due_at=None, asked_at=NOW)) == Idle()


class TestReadingItOutOfTheStore:
    """
    The same four, against `without-durability-sqlite`'s own rows rather than against a value written
    down here.

    Worth a real store because the query is the half that can drift: it names three tables this
    console does not own, and a rename upstream is a wrong answer rather than a failure to compile.
    """

    async def test_a_session_nobody_has_claimed_is_queued_for_its_first_pass(self, service: Service) -> None:
        """
        `say` appends the message and queues the session in one commit, so this is the state a
        session is in from the moment somebody types into it.
        """
        session = await started(service, "hello")

        assert await service.attention(session.id) == Queued()

    async def test_a_session_under_a_pass_reads_as_claimed(self, service: Service) -> None:
        """
        The claim a pass holds is exactly what this has to see, so it is taken the way a pass takes
        one rather than written into the table.
        """
        session = await started(service, "hello")
        holder = await claimed(service.checkpointer, session.id)
        try:
            assert await service.attention(session.id) == Claimed()
        finally:
            await service.checkpointer.release(holder)

        assert await service.attention(session.id) == Queued(), "and stops being claimed when released"

    async def test_a_session_nothing_has_ever_queued_is_idle(self, service: Service) -> None:
        """
        A workflow id with no rows anywhere, which is what a session looks like before anything is
        said in it and what a dropped one looks like after.
        """
        assert await service.attention("nothing-has-touched-this") == Idle()


class TestTheTokenAPageWatchesOn:
    """
    What decides whether the live connection reads again, and the one thing about it that is easy to
    get wrong.

    It has to move when the worker picks a session up and lets it go, because a pass that falls over
    records nothing and a token made of the count alone would hold still while the page sat under a
    spinner. It must *not* move on its own, because a token that differs from itself is a page that
    re-renders for ever.
    """

    async def test_it_moves_when_a_pass_takes_the_session_and_when_it_lets_go(self, service: Service) -> None:
        session = await started(service, "hello")
        before = await service.token(session.id)

        holder = await claimed(service.checkpointer, session.id)
        held = await service.token(session.id)
        await service.checkpointer.release(holder)
        after = await service.token(session.id)

        assert held != before, "a pass took it, and nothing was recorded to say so"
        assert after != held, "and let it go again"

    def test_it_holds_still_while_nothing_happens(self) -> None:
        """
        The regression this exists for: a token carrying how long *until* the next delivery, rather
        than when it is due, shrinks between two reads with nothing having happened, so the page
        re-renders for ever.

        Written against two readings that differ only in when they were taken, rather than against two
        reads of a real store a moment apart: the store's clock has millisecond resolution, so two
        consecutive reads land in the same millisecond often enough that the bug passes such a test.
        """
        earlier = Attended(recorded=4, held_until=NOW - 30, due_at=NOW + 600, asked_at=NOW)
        later = Attended(recorded=4, held_until=NOW - 30, due_at=NOW + 600, asked_at=NOW + 90)

        assert token_of(earlier) == token_of(later)
        assert attention_of(earlier) != attention_of(later), (
            "the control: the two readings really are different moments, and what the worker is doing "
            "does move between them"
        )

    def test_it_moves_on_every_field_the_store_actually_wrote(self) -> None:
        """Each of the three, one at a time, so a token that quietly dropped one would fail here."""
        settled = Attended(recorded=4, held_until=NOW - 30, due_at=NOW + 600, asked_at=NOW)

        assert token_of(replace(settled, recorded=5)) != token_of(settled)
        assert token_of(replace(settled, held_until=NOW + 300)) != token_of(settled)
        assert token_of(replace(settled, due_at=None)) != token_of(settled)

    async def test_it_moves_when_something_is_recorded(self, service: Service) -> None:
        """The half it always had, which the worker's standing is added to rather than replacing."""
        session = await started(service, "hello")
        before = await service.token(session.id)

        await service.checkpointer.supply(session.id, "made:up", {"kind": "tree", "tree": None})

        assert await service.token(session.id) != before


class TestWhatAFallenPassLeavesBehind:
    """
    `reporting` around a body that raises: the reason recorded, and the exception still raised.

    Both halves are the point. Without the record a session is stuck with nothing saying so; without
    the raise the worker treats the delivery as answered and a fault somebody could have fixed is one
    nothing ever looks at again.
    """

    @staticmethod
    async def broken(run: Run) -> None:
        raise RuntimeError("the plugin would not answer")

    async def test_the_reason_is_recorded_and_the_failure_still_raised(self, service: Service) -> None:
        session = await started(service, "hello")

        with pytest.raises(RuntimeError, match="would not answer"):
            await passing(service, session.id, reporting(self.broken))

        recorded = await service.checkpointer.load(session.id)
        fell = failure_in(recorded)
        assert fell is not None
        assert "would not answer" in fell.why
        assert "RuntimeError" in fell.why, "the type, which is half of what a reader searches for"

    async def test_a_pass_that_falls_over_at_the_same_point_records_nothing_further(self, service: Service) -> None:
        """
        What keeps a session broken for a week to one record rather than one per lease: the key is how
        far the session had got, so the same point claims the same name and the store keeps it.
        """
        session = await started(service, "hello")

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await passing(service, session.id, reporting(self.broken))

        recorded = await service.checkpointer.load(session.id)
        failures = [key for key in recorded if key.startswith("failed:")]
        assert failures == [failed_key(len(recorded) - 1)], "one record, named for where it stopped"

    async def test_a_pass_that_got_further_before_falling_over_records_a_second(self, service: Service) -> None:
        """And the newest is what the page reads, so the sentence names the point it is stuck at now."""
        session = await started(service, "hello")
        with pytest.raises(RuntimeError):
            await passing(service, session.id, reporting(self.broken))

        await service.say(session.id, "try again")
        with pytest.raises(RuntimeError):
            await passing(service, session.id, reporting(self.broken))

        recorded = await service.checkpointer.load(session.id)
        failures = [key for key in recorded if key.startswith("failed:")]
        assert len(failures) == 2
        assert parse_failed(recorded[failures[-1]]).at > parse_failed(recorded[failures[0]]).at

    async def test_a_body_that_does_not_raise_records_nothing(self, service: Service) -> None:
        """The control, and every ordinary pass: this adds a sentence and changes no control flow."""
        session = await started(service, "hello")

        async def fine(run: Run) -> None:
            return None

        ended = await passing(service, session.id, reporting(fine))

        recorded = await service.checkpointer.load(session.id)
        assert ended == Completed(None)
        assert failure_in(recorded) is None
        assert not [key for key in recorded if key.startswith("failed:")]

    async def test_the_record_is_the_shape_the_page_reads(self, service: Service) -> None:
        """
        The literal key rather than a round trip through the writer, which is what turns a drift
        between `failed_key` and what `reporting` builds into a failure here rather than a record
        nothing can find.
        """
        session = await started(service, "hello")
        with pytest.raises(RuntimeError):
            await passing(service, session.id, reporting(self.broken))

        recorded = await service.checkpointer.load(session.id)
        at = len(recorded) - 1
        assert failed_key(at) == f"failed:{at}", "the literal, so a drift in the builder fails here"
        assert failed_key(at) in recorded, "and is the name the writer actually claimed"
        fell = parse_failed(recorded[failed_key(at)])
        assert fell.at == at, "which is how far the session had got, carried so the page need not parse the key"
        assert fell.kind == "failed"


class TestWhatThePageSaysAboutIt:
    """
    Which of the four states is worth a sentence, and which of them the dots already say.

    **The quiet arms matter as much as the loud ones.** A line that spoke on every held-back delivery
    would talk through healthy turns, because the queue reserves a row a store round trip before the
    claim lands, and a console that cries wolf is one nobody reads. So what speaks is a recorded
    failure, plus the one state no race produces.
    """

    def waiting(self, **changed: object) -> Conversation:
        """One session with a turn outstanding, varied a field at a time."""
        return replace(
            Conversation(
                session=Session(id="s", created_at=WHEN, title="a session"),
                said=Transcript(panels=(), awaiting=True, turns=1),
                chosen=DEFAULT_CHOICE,
                answerable=True,
            ),
            **changed,  # type: ignore[arg-type]
        )

    @pytest.mark.parametrize("attention", [Claimed(), Queued(), Delayed(until=timedelta(minutes=8))])
    def test_a_turn_being_answered_says_nothing_and_leaves_the_dots(self, attention: object) -> None:
        """Every ordinary state, including the held-back delivery a healthy pass passes through."""
        assert waiting_for(self.waiting(attention=attention)) is None

    def test_a_session_nothing_is_scheduled_for_says_so_with_no_reason_recorded(self) -> None:
        """
        The one state no race produces: a message and the row that queues it are written in a single
        commit, and a pass asks for the next one from inside itself.
        """
        said = waiting_for(self.waiting(attention=Idle()))

        assert said is not None
        assert said.reason is None
        assert "nothing is scheduled" in said.said

    def test_a_failure_names_what_fell_over_and_what_happens_next(self) -> None:
        fell = records.Failed(why="PluginFailed('checks exited 1')", at=3)

        said = waiting_for(self.waiting(failed=fell, attention=Delayed(until=timedelta(minutes=8))))

        assert said is not None
        assert said.reason == fell.why, "verbatim, because it is the thing somebody pastes elsewhere"
        assert said.then is not None
        assert "Fork at this turn" in said.then, "the way out where retrying does not help"

    def test_a_failure_speaks_even_where_the_turn_looked_finished(self) -> None:
        """
        A pass can fall over after the turn's messages landed - on a plugin asked at the turn's end,
        or on the queue - so the sentence cannot be gated on something being outstanding.
        """
        settled = self.waiting(said=Transcript(panels=(), awaiting=False, turns=1))

        assert waiting_for(settled) is None, "the control: a settled conversation says nothing"
        assert waiting_for(replace(settled, failed=records.Failed(why="boom", at=1))) is not None

    def test_the_reason_is_carried_apart_from_the_prose_around_it(self) -> None:
        """
        Which is what lets the page set it in its own block: an exception's `repr` is neither a
        sentence nor centred text, and run together with the words either side it is a wall.
        """
        said = waiting_for(self.waiting(failed=records.Failed(why="Boom('x')", at=3), attention=Idle()))

        assert said is not None
        assert said.reason == "Boom('x')"
        assert "Boom" not in said.said, "and nowhere else"
        assert "Boom" not in (said.then or ""), "nor in the way out"
