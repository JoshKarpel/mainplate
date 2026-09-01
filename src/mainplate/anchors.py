# How a model names a line it wants to change, and what a batch of changes does to a file.
#
# The whole of this module is pure: lines in, lines out, and a refusal where a name does not resolve.
# Reading and writing files is `files.py`'s, which is the functional-core/imperative-shell split the
# rest of this repo keeps, and it is what lets the interesting half be tested with a list of strings.
#
# **A line is addressed by a hash of its content, never by its position.** A line number is the one
# kind of address that cannot fail: an edit above shifts every line below it and `47` still resolves,
# so a stale address silently edits the wrong place. A content hash either resolves to exactly one
# line or does not resolve at all, which turns that silent corruption into a loud refusal. It also
# means the model never retypes the text it is replacing, which is the expensive half of a
# search-and-replace edit and the half that arrives in *output* tokens.
#
# Four lowercase letters, and both halves of that were measured rather than guessed.
#
# **Four**, because 26^4 is 456,976 slots, which over a file's few hundred distinct lines expects
# about one collision per thousand. Three characters of base62 - what the published implementations
# of this idea use - collides 88% of the time over a thousand lines, which is why they need a
# probing scheme to break ties and then a persistent store to keep the probe order stable. Widening
# the hash by one character deletes that entire tower.
#
# **Lowercase**, because a tokenizer is not free and the vendors disagree about what is. Digits look
# ideal on OpenAI's tokenizer, which packs a run of digits three to a token; Qwen and StarCoder2
# spend one token per digit, so a six-digit anchor costs three times as much there. Four lowercase
# letters cost between 2.4 and 3.1 extra tokens per line across every tokenizer tested, which is the
# stable choice rather than the locally optimal one. A separator earns its keep too: a plain space
# costs a token less per line than the box-drawing character the published implementations use.
#
# **Blank lines get no anchor at all.** They are 17% of the lines in this repository and *none* of
# them is unique on its own content, so they were the single largest source of both anchor overhead
# and instability. Leaving them out lifts the share of lines that are unique on their own content
# from 62.5% to 75.6%, cuts the lines needing three or more lines of context by 41%, and takes 17%
# off the token cost, while losing nothing anybody wanted: "insert after the blank line" was always
# a worse instruction than "insert after the code line above it". They still count as *context* for
# the lines around them, and a span may still run through them.
#
# **A duplicate line and a hash collision are the same problem**, so one rule answers both: where two
# lines would share an anchor, extend each with the line before it and hash again, repeating until
# they differ. About 24% of anchorable lines need one line of context and 5% need two.
#
# The cost of that rule is the one thing to know before changing it: an extended anchor depends on
# its neighbours, so an edit just above one invalidates it. Measured over this repository, a
# single-line edit invalidates 0.59 anchors on average and 0.13 of those are more than five lines
# away. Both are answered by what the write reports rather than by making anchors survive changes
# they should not survive: the changed region comes back with fresh anchors, and any anchor that
# moved elsewhere in the file comes back as an explicit remapping.

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Annotated
from typing import Final
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

ALPHABET: Final = "abcdefghijklmnopqrstuvwxyz"

WIDTH: Final = 4

# How many preceding lines an anchor may take in before it gives up. Beyond this a line is inside a
# run of genuinely identical lines that no amount of context tells apart, and the honest answer is
# to have no anchor rather than an ambiguous one: the model addresses the region by the unique lines
# around it instead. Measured over this repository, 99.9% of anchorable lines resolve within five.
MAX_DEPTH: Final = 8

# What stands where an anchor would be on a line that has none but is not blank. Aligned with a real
# anchor so the column does not wander, and visibly not one so it is never mistaken for a name to
# use. Blank lines get nothing at all, since a blank line with a marker on it is no longer blank.
UNADDRESSABLE: Final = "-" * WIDTH

# How many lines either side of a change come back with it. Small deliberately: what makes a chained
# edit safe is the remapping, which is exhaustive, so this is for a reader's orientation rather than
# for correctness.
CONTEXT: Final = 3


class EditRefused(ValueError):
    """
    An operation named something that does not resolve, or a batch that contradicts itself.

    One type for every way an edit can be turned down, because they all reach the model the same
    way and all mean the same thing to it: nothing was written, read the message, try again. What
    distinguishes them is the sentence, which is why these are constructed with a full one rather
    than with a code.
    """


def anchor(window: Sequence[str]) -> str:
    """The anchor for a line, given itself and however many lines before it it needed."""
    value = int.from_bytes(hashlib.blake2b("\n".join(window).encode(), digest_size=8).digest())
    letters = []
    for _ in range(WIDTH):
        value, rest = divmod(value, len(ALPHABET))
        letters.append(ALPHABET[rest])
    return "".join(letters)


def addressable(lines: Sequence[str]) -> tuple[int, ...]:
    """Which lines get an anchor at all, which is every line with something on it."""
    return tuple(at for at, line in enumerate(lines) if line.strip())


def window(lines: Sequence[str], at: int, depth: int) -> tuple[str, ...]:
    """A line and the `depth` lines before it, truncated at the start of the file."""
    return tuple(lines[max(0, at - depth) : at + 1])


def clashes(codes: Mapping[int, str]) -> frozenset[str]:
    return frozenset(code for code, many in Counter(codes.values()).items() if many > 1)


def anchors(lines: Sequence[str]) -> tuple[str | None, ...]:
    """
    An anchor per line, or nothing for a line that cannot have one.

    Nothing means one of two things, and they are deliberately not distinguished here: the line is
    blank, or it is inside a run of identical lines too long for `MAX_DEPTH` to break up. Both are
    lines that cannot be named, which is the only thing a caller can act on.

    The loop deepens *every* line sharing a contested anchor rather than picking one to leave alone,
    because leaving one alone would make an anchor depend on which of its twins was found first.
    Deepening a line at the top of the file is impossible, since its window is already the whole of
    what precedes it, so it keeps the anchor it has and its twin further down moves instead.
    """
    reach = dict.fromkeys(addressable(lines), 0)
    codes = {at: anchor(window(lines, at, depth)) for at, depth in reach.items()}
    for _ in range(MAX_DEPTH):
        contested = clashes(codes)
        if not contested:
            break
        deepened = False
        for at, depth in reach.items():
            if codes[at] in contested and depth < MAX_DEPTH and at - depth > 0:
                reach[at] = depth + 1
                deepened = True
        if not deepened:
            break
        codes = {at: anchor(window(lines, at, depth)) for at, depth in reach.items()}
    settled = {at: code for at, code in codes.items() if code not in clashes(codes)}
    return tuple(settled.get(at) for at in range(len(lines)))


@dataclass(frozen=True, slots=True)
class Anchored:
    """
    A file's lines with the name each one answers to.

    Built over the *whole* file even when only part of it is being shown, because an anchor is
    unique within a file and a table computed over a slice would hand out names that collide with
    lines outside it.
    """

    lines: tuple[str, ...]
    codes: tuple[str | None, ...]
    by_code: Mapping[str, int]

    @classmethod
    def over(cls, lines: Sequence[str]) -> Anchored:
        codes = anchors(lines)
        return cls(
            lines=tuple(lines),
            codes=codes,
            by_code={code: at for at, code in enumerate(codes) if code is not None},
        )

    def at(self, code: str) -> int:
        """
        Which line `code` names, or a refusal saying it names none.

        There is no ambiguous case to report: the table is unique by construction, so an anchor
        either resolves to exactly one line or is not in the file at all. What the message has to
        carry instead is *why* a name the model was given no longer works, since the usual cause is
        that the line changed under it.
        """
        found = self.by_code.get(code)
        if found is None:
            raise EditRefused(
                f"no line is anchored {code!r} in this file, so it has changed since you read it "
                f"or was never there; read the file again for current anchors"
            )
        return found

    def rendered(self, start: int = 0, stop: int | None = None) -> str:
        """The lines a read shows, each behind the name it answers to."""
        end = len(self.lines) if stop is None else stop
        shown = []
        for at in range(start, min(end, len(self.lines))):
            code, line = self.codes[at], self.lines[at]
            shown.append(line if not line.strip() else f"{code or UNADDRESSABLE} {line}")
        return "\n".join(shown)


class Splice(BaseModel):
    """
    Replace a span of whole lines, where either end may fall inside or outside the span.

    Which end is which is said by the *field name* rather than by a flag, so each side reads as
    what it means and there is no boolean to get backwards:

    - `from` starts the span at that line, replacing it
    - `after` starts the span on the line following it, keeping it
    - `to` ends the span at that line, replacing it
    - `before` ends the span on the line preceding it, keeping it

    Giving only one side is an **insertion** at that point, since inserting is replacing the empty
    span there. `{"after": "abcd", "text": "..."}` puts text below that line and `{"before": ...}`
    puts it above. Only the exclusive forms can do this: an inclusive bound with no partner does
    not describe a span at all, and is refused rather than guessed at.

    The exclusive forms are also what reaches a blank line, which has no anchor of its own. Deleting
    a function and the blank lines after it is `{"from": <its first line>, "before": <the next code
    line>}`, which needs neither to name a blank nor to retype the line it stops short of.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    op: Literal["splice"]
    from_: str | None = Field(default=None, alias="from", description="Anchor of the first line to replace.")
    after: str | None = Field(default=None, description="Anchor of the line just above the span, which is kept.")
    to: str | None = Field(default=None, description="Anchor of the last line to replace.")
    before: str | None = Field(default=None, description="Anchor of the line just below the span, which is kept.")
    text: str = Field(
        default="",
        description=(
            "What the span becomes. Each line of this text becomes one line of the file, so an "
            'empty string deletes the span outright, while "\\n" leaves one blank line behind '
            "rather than nothing."
        ),
    )

    @model_validator(mode="after")
    def one_bound_per_side(self) -> Splice:
        if self.from_ is not None and self.after is not None:
            raise ValueError("a splice takes `from` or `after`, not both: they are the same end of the span")
        if self.to is not None and self.before is not None:
            raise ValueError("a splice takes `to` or `before`, not both: they are the same end of the span")
        if self.from_ is None and self.after is None and self.to is None and self.before is None:
            raise ValueError("a splice needs at least one of `from`, `after`, `to` or `before`")
        return self

    @model_validator(mode="after")
    def a_lone_bound_is_exclusive(self) -> Splice:
        """
        One-sided means insert, and only `after`/`before` can say where.

        `{"from": "abcd"}` is refused rather than read as an insertion, because `from` names a line
        to *replace* and a replacement with no end is not a span. The message names the field that
        does say it, since that is the whole of what the caller got wrong.
        """
        if self.from_ is not None and self.to is None and self.before is None:
            raise ValueError("`from` starts a span and needs `to` or `before`; to insert below a line use `after`")
        if self.to is not None and self.from_ is None and self.after is None:
            raise ValueError("`to` ends a span and needs `from` or `after`; to insert above a line use `before`")
        return self


class Substitute(BaseModel):
    """
    Replace text *within* one anchored line, without retyping the rest of it.

    For the case a whole-line splice is clumsy at: changing a word in a long line of prose, a name
    in a signature, one argument in a call. `find` must occur exactly once in that line, so there is
    never a question of which occurrence was meant; widen it until it does.

    `replace` may contain newlines, which splits the line into several.
    """

    model_config = ConfigDict(frozen=True)

    op: Literal["substitute"]
    at: str = Field(description="Anchor of the line to change.")
    find: str = Field(description="The exact text to replace, which must occur once in that line.")
    replace: str = Field(description="What to put in its place.")


type Operation = Annotated[Splice | Substitute, Field(discriminator="op")]


@dataclass(frozen=True, slots=True)
class Span:
    """
    One operation resolved against the file it was named for: which lines go, and what replaces them.

    Half-open, so an insertion is the empty span at a point and needs no separate shape. Resolved
    against the file as it was *before* any of a batch was applied, which is what lets a batch be
    written against one reading of the file.
    """

    start: int
    stop: int
    lines: tuple[str, ...]

    @property
    def last(self) -> int:
        """
        The last position this span occupies, which for an insertion is the point it sits at.

        An inclusive end rather than the half-open one, because it is used to decide whether two
        spans collide and an empty span occupies a point rather than nothing at all: an insertion
        at the start of a replaced range and the replacement itself are two operations with no
        defined order between them, which is a batch to refuse rather than to resolve.
        """
        return max(self.start, self.stop - 1)

    def collides(self, other: Span) -> bool:
        return max(self.start, other.start) <= min(self.last, other.last)


def as_lines(text: str) -> tuple[str, ...]:
    r"""
    Replacement text as the lines it becomes, where empty means no lines at all.

    `"".splitlines()` is already empty, and a trailing newline is dropped rather than becoming a
    blank line, so `"a\n"` and `"a"` both mean one line. That matches what a caller writing JSON
    means by either and keeps a deletion from leaving an empty line behind.
    """
    return tuple(text.splitlines())


def opened_at(anchored: Anchored, splice: Splice) -> int | None:
    if splice.from_ is not None:
        return anchored.at(splice.from_)
    if splice.after is not None:
        return anchored.at(splice.after) + 1
    return None


def closed_at(anchored: Anchored, splice: Splice) -> int | None:
    if splice.to is not None:
        return anchored.at(splice.to) + 1
    if splice.before is not None:
        return anchored.at(splice.before)
    return None


def spliced_span(anchored: Anchored, splice: Splice) -> Span:
    opens, closes = opened_at(anchored, splice), closed_at(anchored, splice)
    start = opens if opens is not None else closes
    stop = closes if closes is not None else opens
    if start is None or stop is None:
        # `Splice`'s own validator already refuses this, and it is repeated here because the type
        # cannot carry the guarantee: "at least one of four optional fields" is not something the
        # annotations say, so this function would otherwise be reasoning from a promise made
        # somewhere else. Total on its own input rather than trusting that a validator ran.
        raise EditRefused("a splice needs at least one of `from`, `after`, `to` or `before`")
    if stop < start:
        raise EditRefused(
            "that span ends before it starts: the line ending it is above the line starting it. "
            "Anchors name lines, so the two have to be given in the order they appear in the file"
        )
    return Span(start=start, stop=stop, lines=as_lines(splice.text))


def substituted_span(anchored: Anchored, substitute: Substitute) -> Span:
    at = anchored.at(substitute.at)
    line = anchored.lines[at]
    found = line.count(substitute.find)
    if found == 0:
        raise EditRefused(f"line {substitute.at!r} does not contain {substitute.find!r}; it reads {line.strip()!r}")
    if found > 1:
        raise EditRefused(
            f"{substitute.find!r} occurs {found} times in line {substitute.at!r}, so which one was meant "
            f"is a guess; widen `find` until it occurs once"
        )
    return Span(start=at, stop=at + 1, lines=as_lines(line.replace(substitute.find, substitute.replace)))


def resolved(anchored: Anchored, operation: Operation) -> Span:
    """One operation against the file as it stands, or a refusal naming what went wrong."""
    if isinstance(operation, Substitute):
        return substituted_span(anchored, operation)
    return spliced_span(anchored, operation)


def spliced(lines: Sequence[str], spans: Sequence[Span]) -> tuple[str, ...]:
    """
    Every span applied at once, which is what makes a batch mean one reading of the file.

    Bottom-up, so an earlier span's indices are still the ones it was resolved against when it is
    applied: done top-down, every span after the first would be off by whatever the first changed.
    """
    written = tuple(lines)
    for span in sorted(spans, key=lambda each: each.start, reverse=True):
        written = written[: span.start] + span.lines + written[span.stop :]
    return written


def colliding(spans: Sequence[Span]) -> tuple[int, int] | None:
    """
    The first two operations that cannot both be applied, by their position in the batch.

    A check worth making because it is *decidable* here, which is the thing addressing by content
    buys that search-and-replace cannot: every span is resolved against one state of the file
    before anything is written, so overlap is a fact rather than a guess about what a previous
    edit did to a later one's match.
    """
    for first in range(len(spans)):
        for second in range(first + 1, len(spans)):
            if spans[first].collides(spans[second]):
                return first, second
    return None


def merged(ranges: Sequence[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Overlapping or touching ranges joined, so a reader is shown one region rather than three."""
    joined: list[tuple[int, int]] = []
    for start, stop in sorted(ranges):
        if joined and start <= joined[-1][1]:
            joined[-1] = (joined[-1][0], max(joined[-1][1], stop))
        else:
            joined.append((start, stop))
    return tuple(joined)


def shifted(spans: Sequence[Span]) -> tuple[tuple[int, int], ...]:
    """
    Where each span's replacement landed once every earlier one had been applied.

    The spans are resolved against the file before the batch, so their own indices say nothing
    about where to find the result. Every span before one shifts it by whatever it added or
    removed, and they cannot overlap, so the shift is a running total rather than a search.
    """
    landed: list[tuple[int, int]] = []
    drift = 0
    for span in sorted(spans, key=lambda each: each.start):
        start = span.start + drift
        landed.append((start, start + len(span.lines)))
        drift += len(span.lines) - (span.stop - span.start)
    return tuple(landed)


@dataclass(frozen=True, slots=True)
class Moved:
    """One line that kept its content and changed its name, because what precedes it changed."""

    was: str
    now: str
    line: str


def moved(before: Anchored, after: Anchored, shown: Sequence[tuple[int, int]]) -> tuple[Moved, ...]:
    """
    Every anchor that changed on a line nobody edited and the reply does not already show.

    This is what keeps a chain of edits going without a re-read. An anchor takes in the lines before
    it when its own content is not unique, so an edit can change the name of an untouched line
    somewhere else in the file: rare, measured at 0.13 lines per single-line edit, and silent
    unless it is said out loud. The two tables are both in hand at this point, so saying it costs a
    comparison rather than any bookkeeping kept between calls.

    Lines already inside a region the reply is showing are left out, since their new anchors are
    right there.
    """
    changed: list[Moved] = []
    matcher = SequenceMatcher(a=before.lines, b=after.lines, autojunk=False)
    for start, landed, length in matcher.get_matching_blocks():
        for step in range(length):
            was, now = before.codes[start + step], after.codes[landed + step]
            if was is None or now is None or was == now:
                continue
            if any(low <= landed + step < high for low, high in shown):
                continue
            changed.append(Moved(was=was, now=now, line=after.lines[landed + step]))
    return tuple(changed)


@dataclass(frozen=True, slots=True)
class Written:
    """
    What a batch did, as everything the model needs to keep going without reading the file again.

    `regions` are where the changes landed in the new file, already merged and padded with a little
    context; `remapped` is every anchor that moved outside them.
    """

    after: Anchored
    regions: tuple[tuple[int, int], ...]
    remapped: tuple[Moved, ...]

    @property
    def lines(self) -> tuple[str, ...]:
        return self.after.lines


def written(before: Anchored, operations: Sequence[Operation]) -> Written:
    """
    A whole batch resolved, checked against itself, and applied.

    Nothing is written until every operation has resolved and no two of them collide, which is what
    "atomic" means here: a batch that would half-apply is refused entire, so the file is never in a
    state no one asked for.
    """
    if not operations:
        raise EditRefused("an edit needs at least one operation")
    spans = [resolved(before, operation) for operation in operations]
    clash = colliding(spans)
    if clash is not None:
        first, second = clash
        raise EditRefused(
            f"operations {first + 1} and {second + 1} both change the same lines, so which one wins "
            f"would depend on the order they were applied in; nothing was written. Split them across "
            f"two calls, or combine them into one operation"
        )
    after = Anchored.over(spliced(before.lines, spans))
    regions = merged(
        [(max(0, start - CONTEXT), min(len(after.lines), stop + CONTEXT)) for start, stop in shifted(spans)]
    )
    return Written(after=after, regions=regions, remapped=moved(before, after, regions))
