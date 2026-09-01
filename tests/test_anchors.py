from __future__ import annotations

import pytest
from pydantic import ValidationError

from mainplate.anchors import MAX_DEPTH
from mainplate.anchors import UNADDRESSABLE
from mainplate.anchors import WIDTH
from mainplate.anchors import Anchored
from mainplate.anchors import EditRefused
from mainplate.anchors import Operation
from mainplate.anchors import Splice
from mainplate.anchors import Substitute
from mainplate.anchors import written

# A small file with all three interesting shapes in it: unique lines, a pair of byte-identical lines
# that context has to tell apart, and blank lines between the two functions.
SOURCE = (
    "def first(value):",
    "    if value:",
    "        return None",
    "    return 1",
    "",
    "",
    "def second(value):",
    "    if value:",
    "        return None",
    "    return 2",
)

DUPLICATED = "        return None"


def anchored() -> Anchored:
    return Anchored.over(SOURCE)


def code(at: int) -> str:
    """The anchor of one line of `SOURCE`, for a test that wants to name it."""
    found = anchored().codes[at]
    assert found is not None
    return found


def lines_of(operations: list[Operation]) -> tuple[str, ...]:
    return written(anchored(), operations).lines


class TestNamingALine:
    def test_every_line_with_something_on_it_is_named(self) -> None:
        named = [line for line, at in zip(SOURCE, anchored().codes, strict=True) if at is not None]
        assert named == [line for line in SOURCE if line.strip()]

    def test_a_blank_line_is_not_named(self) -> None:
        assert [anchored().codes[at] for at in (4, 5)] == [None, None]

    def test_no_two_lines_share_a_name(self) -> None:
        given = [at for at in anchored().codes if at is not None]
        assert len(set(given)) == len(given)

    def test_a_name_is_four_lowercase_letters(self) -> None:
        assert all(len(at) == WIDTH and at.isalpha() and at.islower() for at in anchored().codes if at is not None)

    def test_two_identical_lines_are_told_apart_by_what_precedes_them(self) -> None:
        """The whole reason context extension exists: 37% of lines in a real file need it."""
        assert SOURCE[2] == SOURCE[8] == DUPLICATED
        assert anchored().codes[2] != anchored().codes[8]

    def test_a_unique_line_is_named_by_its_own_content_alone(self) -> None:
        """
        What makes an anchor portable across reads: a line whose content is unique in its file gets
        the same name in any file where it is also unique, so nothing about the rest of the file
        leaks into it.
        """
        elsewhere = Anchored.over(("# something else entirely", "    return 1", "print('hi')"))
        assert elsewhere.codes[1] == code(3)

    def test_a_run_too_long_to_tell_apart_is_left_unnamed(self) -> None:
        """A refusal rather than an ambiguous name: address the unique lines around it instead."""
        wall = Anchored.over(tuple("    pass" for _ in range(MAX_DEPTH + 4)))
        assert None in wall.codes

    def test_an_unknown_name_says_the_line_has_moved(self) -> None:
        with pytest.raises(EditRefused, match="read the file again"):
            anchored().at("zzzz")


class TestShowingAFile:
    def test_each_named_line_is_shown_behind_its_name(self) -> None:
        assert anchored().rendered(0, 2) == f"{code(0)} def first(value):\n{code(1)}     if value:"

    def test_a_blank_line_is_shown_as_a_blank_line(self) -> None:
        assert anchored().rendered(4, 6) == "\n"

    def test_an_unnameable_line_is_marked_rather_than_left_bare(self) -> None:
        wall = Anchored.over(tuple("    pass" for _ in range(MAX_DEPTH + 4)))
        assert UNADDRESSABLE in wall.rendered()


class TestSplicingASpan:
    @pytest.mark.parametrize(
        ("operation", "expected"),
        [
            pytest.param(
                Splice(op="splice", from_=code(0), to=code(1), text="def only():"),
                ("def only():", *SOURCE[2:]),
                id="from-to replaces both ends",
            ),
            pytest.param(
                Splice(op="splice", after=code(0), before=code(3), text="    pass"),
                (SOURCE[0], "    pass", *SOURCE[3:]),
                id="after-before keeps both ends",
            ),
            pytest.param(
                Splice(op="splice", from_=code(1), before=code(3), text="    pass"),
                (SOURCE[0], "    pass", *SOURCE[3:]),
                id="from-before mixes the two",
            ),
            pytest.param(
                Splice(op="splice", after=code(0), to=code(2), text="    pass"),
                (SOURCE[0], "    pass", *SOURCE[3:]),
                id="after-to mixes the other way",
            ),
        ],
    )
    def test_which_lines_the_span_covers_is_said_by_the_field_names(
        self, operation: Splice, expected: tuple[str, ...]
    ) -> None:
        assert lines_of([operation]) == expected

    def test_empty_text_deletes_the_span(self) -> None:
        assert lines_of([Splice(op="splice", from_=code(0), to=code(3), text="")]) == SOURCE[4:]

    def test_an_exclusive_end_reaches_blank_lines_no_anchor_could_name(self) -> None:
        """
        The case blank lines being unnamed would otherwise make clumsy: deleting a function *and*
        the blank lines after it, without naming a blank and without retyping the line below them.
        """
        assert lines_of([Splice(op="splice", from_=code(0), before=code(6), text="")]) == SOURCE[6:]

    def test_one_exclusive_bound_alone_inserts_there(self) -> None:
        assert lines_of([Splice(op="splice", after=code(3), text="    # noted")]) == (
            *SOURCE[:4],
            "    # noted",
            *SOURCE[4:],
        )

    def test_before_alone_inserts_above(self) -> None:
        assert lines_of([Splice(op="splice", before=code(0), text="# header")]) == ("# header", *SOURCE)

    def test_replacement_text_may_span_several_lines(self) -> None:
        assert lines_of([Splice(op="splice", from_=code(3), to=code(3), text="    x = 1\n    return x")]) == (
            *SOURCE[:3],
            "    x = 1",
            "    return x",
            *SOURCE[4:],
        )

    def test_a_span_that_ends_above_where_it_starts_is_refused(self) -> None:
        with pytest.raises(EditRefused, match="ends before it starts"):
            written(anchored(), [Splice(op="splice", from_=code(6), to=code(1), text="x")])


class TestRefusingAMalformedSplice:
    def test_both_bounds_on_one_side_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="same end of the span"):
            Splice(op="splice", from_="abcd", after="efgh", to="ijkl")

    def test_an_inclusive_start_with_no_end_names_the_field_that_would_work(self) -> None:
        with pytest.raises(ValidationError, match="to insert below a line use `after`"):
            Splice(op="splice", from_="abcd")

    def test_an_inclusive_end_with_no_start_names_the_field_that_would_work(self) -> None:
        with pytest.raises(ValidationError, match="to insert above a line use `before`"):
            Splice(op="splice", to="abcd")

    def test_naming_no_line_at_all_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="at least one of"):
            Splice(op="splice", text="orphaned")

    def test_from_is_spelled_from_on_the_wire(self) -> None:
        """The field a model actually writes, which is the keyword Python will not let us name."""
        assert Splice.model_validate({"op": "splice", "from": "abcd", "to": "efgh"}).from_ == "abcd"


class TestSubstitutingWithinALine:
    def test_only_the_named_text_changes(self) -> None:
        assert lines_of([Substitute(op="substitute", at=code(0), find="first", replace="renamed")]) == (
            "def renamed(value):",
            *SOURCE[1:],
        )

    def test_text_that_is_not_there_is_refused_with_the_line_quoted(self) -> None:
        with pytest.raises(EditRefused, match="does not contain"):
            written(anchored(), [Substitute(op="substitute", at=code(0), find="absent", replace="x")])

    def test_text_that_occurs_twice_is_refused_rather_than_guessed_at(self) -> None:
        doubled = Anchored.over(("total = total + 1",))
        naming = doubled.codes[0]
        assert naming is not None

        with pytest.raises(EditRefused, match="occurs 2 times"):
            written(doubled, [Substitute(op="substitute", at=naming, find="total", replace="n")])

    def test_a_newline_in_the_replacement_splits_the_line(self) -> None:
        assert lines_of([Substitute(op="substitute", at=code(3), find="return 1", replace="x = 1\n    return x")]) == (
            *SOURCE[:3],
            "    x = 1",
            "    return x",
            *SOURCE[4:],
        )


class TestApplyingABatch:
    def test_operations_are_resolved_against_the_file_before_any_of_them_ran(self) -> None:
        """
        What makes a batch worth having: two operations written against one reading of the file
        cannot shift each other, where two sequential search-and-replaces would.
        """
        assert lines_of(
            [
                Substitute(op="substitute", at=code(0), find="first", replace="renamed"),
                Splice(op="splice", after=code(9), text="# end"),
            ]
        ) == ("def renamed(value):", *SOURCE[1:], "# end")

    def test_operations_that_change_the_same_lines_are_refused_entire(self) -> None:
        with pytest.raises(EditRefused, match="operations 1 and 2"):
            written(
                anchored(),
                [
                    Splice(op="splice", from_=code(0), to=code(1), text="x"),
                    Substitute(op="substitute", at=code(1), find="value", replace="v"),
                ],
            )

    def test_an_insertion_at_the_start_of_a_replaced_span_is_refused_as_ambiguous(self) -> None:
        """Two operations at one point have no order between them, so the batch is turned down."""
        with pytest.raises(EditRefused, match="same lines"):
            written(
                anchored(),
                [
                    Splice(op="splice", from_=code(1), to=code(2), text="    pass"),
                    Splice(op="splice", before=code(1), text="    # above"),
                ],
            )

    def test_an_insertion_just_past_a_replaced_span_is_allowed(self) -> None:
        assert lines_of(
            [
                Splice(op="splice", from_=code(1), to=code(2), text="    pass"),
                Splice(op="splice", before=code(3), text="    # between"),
            ]
        ) == (SOURCE[0], "    pass", "    # between", *SOURCE[3:])

    def test_an_empty_batch_is_refused(self) -> None:
        with pytest.raises(EditRefused, match="at least one operation"):
            written(anchored(), [])


class TestWhatAnEditReports:
    def test_the_changed_region_comes_back_with_its_new_anchors(self) -> None:
        done = written(anchored(), [Substitute(op="substitute", at=code(3), find="1", replace="42")])
        start, stop = done.regions[0]
        assert "    return 42" in done.after.rendered(start, stop)

    def test_an_anchor_that_moved_elsewhere_in_the_file_is_named(self) -> None:
        """
        The measured cost of context extension, reported rather than left to be tripped over.

        Deleting lines 1 and 2 removes one of *each* pair of identical lines in this file, so both
        survivors down in `second` stop needing context and are renamed, eight lines from the edit
        and nowhere near what the reply shows.
        """
        surviving = {code(7): SOURCE[7], code(8): DUPLICATED}

        done = written(anchored(), [Splice(op="splice", from_=code(1), to=code(2), text="    pass")])

        assert {moved.was: moved.line for moved in done.remapped} == surviving
        assert all(moved.now != moved.was for moved in done.remapped)

    def test_an_anchor_that_did_not_move_is_not_named(self) -> None:
        done = written(anchored(), [Substitute(op="substitute", at=code(9), find="2", replace="3")])
        assert done.remapped == ()

    def test_a_change_inside_a_shown_region_is_not_also_reported_as_a_remapping(self) -> None:
        """It is already there with its new anchor, so naming it again would be noise."""
        done = written(anchored(), [Splice(op="splice", after=code(0), text="    # noted")])
        shown = {line for start, stop in done.regions for line in done.after.lines[start:stop]}
        assert not any(moved.line in shown for moved in done.remapped)
