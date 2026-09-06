from __future__ import annotations

import pytest

from mainplate.console import NotAMessage
from mainplate.console import parse_form_tending
from mainplate.tending import HANDS_OFF
from mainplate.tending import LEAST_ROOM
from mainplate.tending import RESERVE
from mainplate.tending import THOUSAND
from mainplate.tending import Reserve
from mainplate.tending import Tending
from mainplate.tending import parse_tending
from mainplate.tending import reserving
from mainplate.tending import standing

WINDOW = 200_000
"""A window a long way from either default, so nothing here passes by coinciding with a constant."""

RESERVED = 50_000
"""A reserve that is neither the default nor the floor, for the same reason."""

IN_THOUSANDS = str(RESERVED // THOUSAND)
"""The same reserve as the box holds it, which is what a form posts."""


def posted(**fields: str) -> bytes:
    return "&".join(f"{name}={value}" for name, value in fields.items()).encode()


class TestWhereAReserveFallsInAWindow:
    def test_it_opens_a_reserve_below_the_ceiling_and_shuts_a_handoff_short_of_it(self) -> None:
        assert reserving(WINDOW, RESERVED) == Reserve(opens=150_000, shuts=WINDOW - LEAST_ROOM)

    def test_a_model_nothing_knows_the_window_of_places_nothing(self) -> None:
        assert reserving(None, RESERVED) is None

    def test_a_reserve_as_large_as_the_window_describes_no_interval(self) -> None:
        """
        Otherwise every session is due from its very first turn, on a model too small for the number.

        Read as an interval that opens at or below zero rather than as one to clamp: correcting the
        number would leave a person looking at a reserve the console is not using.
        """
        assert reserving(WINDOW, WINDOW) is None
        assert reserving(WINDOW, WINDOW + 1) is None

    def test_a_reserve_under_the_room_a_handoff_needs_describes_no_interval(self) -> None:
        """One that opens above where it shuts, which is a stretch nothing could ever be inside."""
        assert reserving(WINDOW, LEAST_ROOM - 1) is None
        assert reserving(WINDOW, LEAST_ROOM) == Reserve(opens=WINDOW - LEAST_ROOM, shuts=WINDOW - LEAST_ROOM)


class TestWhereAConversationStands:
    @pytest.mark.parametrize(
        ("context", "expected"),
        [
            (0, "room"),
            (149_999, "room"),
            (150_000, "due"),
            (WINDOW - LEAST_ROOM, "due"),
            (WINDOW - LEAST_ROOM + 1, "gone"),
            (WINDOW * 2, "gone"),
        ],
    )
    def test_the_three_answers_are_bounded_by_the_reserve_and_by_the_room_left(
        self, context: int, expected: str
    ) -> None:
        assert standing(context, WINDOW, RESERVED) == expected

    def test_nothing_placed_it_where_nothing_places_the_reserve(self) -> None:
        assert standing(120_000, None, RESERVED) is None


class TestReadingTheColumns:
    def test_an_absent_column_reads_as_the_constant_it_defaults_to(self) -> None:
        assert parse_tending(None, None) == Tending(hands_off=HANDS_OFF, reserve=RESERVE)

    def test_each_column_defaults_on_its_own_rather_than_as_a_pair(self) -> None:
        """
        Unlike an origin, which demands both: a session told one setting and not the other is
        ordinary, where a row naming half an origin is one nothing here could have written.
        """
        assert parse_tending(0, None) == Tending(hands_off=False, reserve=RESERVE)
        assert parse_tending(None, RESERVED) == Tending(hands_off=HANDS_OFF, reserve=RESERVED)

    def test_a_stored_reserve_is_taken_as_it_stands_rather_than_clamped(self) -> None:
        """What a nonsensical one produces is a window that never opens, which `reserving` says."""
        assert parse_tending(1, 1).reserve == 1
        assert reserving(WINDOW, 1) is None


class TestWhatTheFormPosts:
    def test_the_box_is_in_thousands_and_the_record_is_in_tokens(self) -> None:
        """
        The control is denominated differently from the value behind it, so the boundary multiplies.
        A reserve is only ever chosen in round thousands and six digits is a number to count the
        zeroes of.
        """
        assert parse_form_tending(posted(reserve="50")).reserve == 50_000

    def test_an_unchecked_switch_posts_no_field_and_reads_as_off(self) -> None:
        """
        The browser's own mechanism, and the one place this differs from the column: a form says what
        somebody just said, where a `NULL` column says nobody has ever said anything.
        """
        assert parse_form_tending(posted(reserve=IN_THOUSANDS)) == Tending(hands_off=False, reserve=RESERVED)

    def test_a_checked_switch_posts_whatever_the_browser_puts_in_it(self) -> None:
        assert parse_form_tending(posted(hands_off="on", reserve=IN_THOUSANDS)).hands_off

    def test_an_empty_box_takes_the_default_rather_than_recording_nothing(self) -> None:
        assert parse_form_tending(posted(hands_off="on")) == Tending(hands_off=True, reserve=RESERVE)

    def test_something_that_is_not_a_number_is_refused_rather_than_dropped(self) -> None:
        with pytest.raises(NotAMessage, match="is not a number of thousands of tokens"):
            parse_form_tending(posted(reserve="a lot"))

    def test_a_reserve_under_the_room_a_handoff_needs_is_refused_where_it_can_be_explained(self) -> None:
        """
        Refused here rather than stored and left to `reserving`, which would answer that the console
        cannot say where the session stands with nothing saying the number was why.
        """
        with pytest.raises(NotAMessage, match="leaves less room than a handoff needs"):
            parse_form_tending(posted(reserve=str(LEAST_ROOM // THOUSAND - 1)))
