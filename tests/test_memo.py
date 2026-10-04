from __future__ import annotations

import json
import sys

import pytest
from calling import calling
from without_asgi import ASGIApp

from mainplate.calls import anchored_element
from mainplate.markup import as_message
from mainplate.memo import Memo
from mainplate.memo import Tally
from mainplate.memo import weighed


def tally_of(memo: Memo, name: str) -> Tally:
    """One function's figures out of a memo's account, by the name it was filed under."""
    return next(tally for tally in memo.account().tallies if tally.name.endswith(f".{name}"))


class TestWhatTheMemoAnswers:
    def test_a_second_call_with_the_same_arguments_is_answered_without_running_the_function(self) -> None:
        memo = Memo(budget=40_000)
        ran: list[str] = []

        @memo.memoized
        def shouted(text: str) -> str:
            ran.append(text)
            return text.upper()

        assert shouted("quiet") == "QUIET"
        assert shouted("quiet") == "QUIET"
        assert ran == ["quiet"]
        assert (tally_of(memo, "shouted").hits, tally_of(memo, "shouted").misses) == (1, 1)

    def test_different_arguments_are_different_entries(self) -> None:
        memo = Memo(budget=40_000)

        @memo.memoized
        def joined(left: str, right: str) -> str:
            return f"{left}-{right}"

        assert joined("ab", "cd") == "ab-cd"
        assert joined("cd", "ab") == "cd-ab"
        assert tally_of(memo, "joined").entries == 2

    def test_a_function_memoized_and_never_called_is_accounted_for_with_nothing_against_it(self) -> None:
        memo = Memo(budget=40_000)

        @memo.memoized
        def unused(text: str) -> str:
            return text

        assert tally_of(memo, "unused") == Tally(
            name=tally_of(memo, "unused").name, hits=0, misses=0, entries=0, weight=0, evicted=0
        )


class TestTheBudget:
    def test_what_is_held_is_counted_in_bytes_across_every_function(self) -> None:
        memo = Memo(budget=40_000)

        @memo.memoized
        def doubled(text: str) -> str:
            return text * 2

        @memo.memoized
        def tripled(text: str) -> str:
            return text * 3

        doubled("d" * 1_000)
        tripled("t" * 2_000)
        account = memo.account()
        assert account.held == sum(tally.weight for tally in account.tallies)
        assert tally_of(memo, "tripled").weight > tally_of(memo, "doubled").weight > 3_000

    def test_the_key_is_weighed_as_well_as_the_result(self) -> None:
        """A whole file's text is the key to its highlighting, and the memo holds it as long as the result."""
        memo = Memo(budget=40_000)

        @memo.memoized
        def first_line(text: str) -> str:
            return text.split("\n", 1)[0]

        first_line("head\n" + "body " * 2_000)
        assert tally_of(memo, "first_line").weight > 10_000

    def test_crossing_the_budget_lets_the_least_recently_used_go_and_charges_its_function(self) -> None:
        """
        Sized so one eviction is exactly enough: an entry weighs its key and its result, about 4 KB at
        2,000 characters and 8 KB at 4,000, so the third insert crosses 14 KB and fits once one 4 KB
        entry has gone, and only the least recently used one may be it.
        """
        memo = Memo(budget=14_000)

        @memo.memoized
        def kept(text: str) -> str:
            return text.upper()

        @memo.memoized
        def filling(text: str) -> str:
            return text.upper()

        kept("k" * 2_000)
        filling("f" * 2_000)
        kept("k" * 2_000)  # touched, so `filling`'s entry is now the least recently used
        filling("g" * 4_000)
        assert (tally_of(memo, "filling").evicted, tally_of(memo, "kept").evicted) == (1, 0)
        assert memo.account().held <= 14_000

    def test_a_result_heavier_than_the_whole_budget_is_returned_and_not_kept(self) -> None:
        memo = Memo(budget=3_000)

        @memo.memoized
        def repeated(text: str) -> str:
            return text * 10

        assert repeated("r" * 500) == "r" * 5_000
        assert (tally_of(memo, "repeated").entries, memo.account().held) == (0, 0)

    def test_configuring_starts_over_at_the_new_size(self) -> None:
        memo = Memo(budget=40_000)

        @memo.memoized
        def echoed(text: str) -> str:
            return text

        echoed("e" * 1_000)
        memo.configure(25_000)
        account = memo.account()
        assert (account.budget, account.held, tally_of(memo, "echoed").misses) == (25_000, 0, 0)


class TestWeighing:
    def test_text_is_weighed_as_python_holds_it(self) -> None:
        assert weighed("w" * 900) == sys.getsizeof("w" * 900)

    def test_a_tuple_of_lines_is_weighed_with_its_lines(self) -> None:
        lines = tuple(f"line {at}" * 30 for at in range(40))
        assert weighed(lines) == sys.getsizeof(lines) + sum(sys.getsizeof(line) for line in lines)


class TestTheDebugPage:
    async def test_it_names_every_memoized_function(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            page = await caller.get("/debug")
        assert page.status == 200
        for function in (as_message, anchored_element):
            assert f"{function.__module__}.{function.__qualname__}" in page.text

    async def test_the_json_carries_the_same_account(self, app: ASGIApp) -> None:
        async with calling(app) as caller:
            answered = await caller.get("/api/debug")
        assert answered.status == 200
        assert answered.headers["content-type"].startswith("application/json")
        memo = json.loads(answered.body)["memo"]
        named = {function["name"]: function for function in memo["functions"]}
        assert f"{anchored_element.__module__}.{anchored_element.__qualname__}" in named
        assert memo["held"] == sum(function["held"] for function in memo["functions"])
        assert set(next(iter(named.values()))) == {"name", "hits", "misses", "entries", "held", "evicted"}

    @pytest.mark.parametrize("path", ["/", "/debug"])
    async def test_it_is_reached_from_the_dashboard_and_reaches_its_json(self, app: ASGIApp, path: str) -> None:
        async with calling(app) as caller:
            page = await caller.get(path)
        assert {"/": 'href="/debug"', "/debug": 'href="/api/debug"'}[path] in page.text
