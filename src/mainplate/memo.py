# The one place a pure function's results are kept, and the one budget of memory they share.
#
# A page is re-rendered whole whenever anything is recorded, so the console memoizes what is a pure
# function of a value that never changes: a message's Markdown, a file's highlighting, a tool's
# schema. Each of those used to be its own `lru_cache` with a count of entries, and a count says
# nothing about memory, since one held document weighs as much as fifty held messages. So they share
# one store, bounded in bytes, and a function memoized here is one more tenant of the same budget
# rather than one more number to choose.
#
# This is not the "second copy" `PHILOSOPHY.md` warns against. What is held is a value computed from
# a value that is already settled, so it can never disagree with what it was computed from, and
# dropping any of it at any moment changes nothing but how long the next call takes.

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Final
from typing import cast

from cachetools import LRUCache

DEFAULT_MEMO_BYTES: Final = 128 * 2**20
"""
What the memo may hold before it lets the least recently used result go.

Sized from a measurement and a judgement. Drawing the eight largest sessions on a real console, each
a few megabytes of checkpoint, left about 60 MiB held and nothing evicted; twice that keeps more
large sessions warm than a person moves between in a sitting, so a session gone cold is one nobody
has looked at for a while rather than the one opened a minute ago. The cost, stated: up to that much
memory held for as long as the process runs. `Settings.memo_bytes` is where it is set, and the debug
page is where to see whether it is the right size.
"""


def weighed(value: object) -> int:
    """
    About how many bytes holding `value` costs, which is what the budget is counted in.

    Exact for text, which is nearly everything the console memoizes, and walked through the tuples,
    lists, sets and mappings it comes in. Anything else is weighed shallow, by `sys.getsizeof`, which
    undercounts an object holding others; the few memoized here that are not text are small and few,
    and an undercount only lets the budget hold a little more than it says.
    """
    match value:
        case tuple() | list() | set() | frozenset():
            return sys.getsizeof(value) + sum(weighed(inner) for inner in value)
        case dict():
            return sys.getsizeof(value) + sum(weighed(key) + weighed(inner) for key, inner in value.items())
        case _:
            return sys.getsizeof(value)


@dataclass(frozen=True, slots=True)
class Entry:
    """
    One result as the memo holds it: the value, and what holding it costs, weighed once on the way in.

    The weight is the arguments' as well as the result's, because the arguments are the key and the
    key is held too: `highlighted` is keyed by a whole file's text, which outweighs the markup it
    returns, and the store's own `getsizeof` only ever sees the value.
    """

    value: object
    weight: int


@dataclass(slots=True)
class Counts:
    """One memoized function's running figures, changed only under the memo's lock."""

    hits: int = 0
    misses: int = 0
    entries: int = 0
    weight: int = 0
    evicted: int = 0


@dataclass(frozen=True, slots=True)
class Tally:
    """One memoized function's figures at the moment they were read, as the debug page draws them."""

    name: str
    hits: int
    misses: int
    entries: int
    weight: int
    evicted: int


@dataclass(frozen=True, slots=True)
class Account:
    """The whole memo at the moment it was read: what it may hold, what it holds, and who holds it."""

    budget: int
    held: int
    tallies: tuple[Tally, ...]


class Held(LRUCache[tuple[object, ...], Entry]):
    """
    cachetools' LRU, weighed by `Entry.weight`, keeping each function's entry and byte counts in step.

    The counts are kept here rather than by the decorator because eviction happens here: an insert
    that crosses the budget calls `popitem` until it fits, and only this class sees which function's
    result went. The first element of every key is the function's name, which is how an evicted key
    is charged back to the function that put it there.
    """

    def __init__(self, budget: int, counts: dict[str, Counts]) -> None:
        super().__init__(maxsize=budget, getsizeof=lambda entry: entry.weight)
        self.counts = counts

    def __setitem__(self, key: tuple[object, ...], entry: Entry) -> None:
        # Evicts first, through `popitem`, so the counts below are charged after room was made.
        super().__setitem__(key, entry)
        counts = self.counts[cast(str, key[0])]
        counts.entries += 1
        counts.weight += entry.weight

    def popitem(self) -> tuple[tuple[object, ...], Entry]:
        key, entry = super().popitem()
        counts = self.counts[cast(str, key[0])]
        counts.entries -= 1
        counts.weight -= entry.weight
        counts.evicted += 1
        return key, entry


class Memo:
    """
    The store every memoized function shares, and the decorator that makes a function one of them.

    **One store for the process, and its decorator names it at every use**: `@MEMO.memoized` on the
    function, so what memoizes a function is on the function, and the debug page reads the same
    store back. Process-wide because what it memoizes are module-level pure functions, which have no
    caller to be handed a store by.

    **The budget is set once at startup, before the console serves**, by `configure`, which starts
    the store over at the new size. The decorators run at import, before any setting has been read,
    so the store exists first with `DEFAULT_MEMO_BYTES` and is resized before anything has been
    computed into it; a resize later would only cost what it dropped.

    One lock around every look and every insert, because pages render on worker threads, and never
    around the computation: two threads missing on one key both compute it and the second insert is
    skipped, which costs a computation rather than a thread waiting on another's.
    """

    def __init__(self, budget: int = DEFAULT_MEMO_BYTES) -> None:
        self.lock = threading.Lock()
        self.counts: dict[str, Counts] = {}
        self.held = Held(budget, self.counts)

    def configure(self, budget: int) -> None:
        """Start over with `budget` bytes, dropping whatever was held and every figure but who is here."""
        with self.lock:
            for name in self.counts:
                self.counts[name] = Counts()
            self.held = Held(budget, self.counts)

    def memoized[**P, R](self, function: Callable[P, R]) -> Callable[P, R]:
        """
        `function`, with its results kept here, keyed by its name and its arguments.

        The arguments have to be hashable, as they do for `lru_cache`. A result heavier than the
        whole budget is returned and not kept, since keeping it would mean evicting everything else
        and then itself.
        """
        name = f"{function.__module__}.{function.__qualname__}"
        self.counts.setdefault(name, Counts())

        @wraps(function)
        def memoizing(*args: P.args, **kwargs: P.kwargs) -> R:
            # A plain tuple rather than cachetools' `hashkey`, whose stub wants every keyword argument
            # typed `Hashable`, which a `ParamSpec` cannot promise; hashing it is the check either way.
            key = (name, args, tuple(kwargs.items()))
            with self.lock:
                held = self.held
                entry = held.get(key)
                if entry is not None:
                    self.counts[name].hits += 1
                    return cast(R, entry.value)
                self.counts[name].misses += 1
            value = function(*args, **kwargs)
            weight = weighed(args) + weighed(kwargs) + weighed(value)
            with self.lock:
                # `held` rather than `self.held`: a `configure` while this computed started the store
                # over, and the result belongs to the store that missed, which is gone.
                if held is self.held and key not in held and weight <= held.maxsize:
                    held[key] = Entry(value=value, weight=weight)
            return value

        return memoizing

    def account(self) -> Account:
        """Every memoized function's figures and the store's, read together under the lock."""
        with self.lock:
            return Account(
                budget=int(self.held.maxsize),
                held=int(self.held.currsize),
                tallies=tuple(
                    Tally(
                        name=name,
                        hits=counts.hits,
                        misses=counts.misses,
                        entries=counts.entries,
                        weight=counts.weight,
                        evicted=counts.evicted,
                    )
                    for name, counts in sorted(self.counts.items())
                ),
            )


MEMO: Final = Memo()
"""The process's one memo; see `Memo` for why there is one and how it is sized."""
