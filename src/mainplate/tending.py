# What this console does for a session unasked, and where a conversation stands against it.
#
# A module of its own for `thinking.py`'s reason, which is a cycle: the columns live on the session
# index and the decision is made inside a pass, so `sessions.py` and `conversation.py` both read
# this, and `sessions.py` already reads `conversation.py` for the key scheme. What is left here is a
# small shared vocabulary and the arithmetic over it, which belongs to neither of them.
#
# The split inside it is the usual one. `Tending` is a value on a row and knows nothing about models;
# `standing` is the pure function that places a conversation against one, and is the whole of what
# the pass and the page have to agree about.

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from typing import Literal

HANDS_OFF: Final = True
"""
Whether a session hands itself off when its reserve is crossed, where nobody has said otherwise.

**On, and this is the one setting of its kind where that is safe.** Every other harness defaults its
compaction on as a bet that the summary is good enough, because what the summary replaces is gone. A
handoff here replaces nothing: it is an append, the whole conversation stays in the transcript, it
still counts toward what the session cost, it still comes across on a fork, and forking above the
boundary carries the entire backlog into a session whose context holds all of it. So the worst a
wrong default costs is one turn nobody asked for, which is a fork away from being undone.
"""

RESERVE: Final = 40_000
"""
How much room a session keeps free for writing its own handoff, in tokens, where nobody has said.

**Tokens and never a percentage**, because what has to be true is that the handoff run has room to do
its work: the ask, a few tool calls, the returns they bring back, and the document. That is an
absolute quantity, and the same one on every model. A fifth of the window is 40k on a 200k model and
200k on a 1M one, which is the same setting needing to be re-tuned per model - and re-tuned by
somebody who would have to know this number anyway in order to pick the fraction.

Forty thousand is what a thorough handoff costs measured in what it *adds to the history* rather than
in what it is charged: a listing, four or five reads of real source files, and a document of a couple
of thousand tokens. It is deliberately generous, because being late costs a handoff that cannot be
written and being early costs one turn.
"""

LEAST_ROOM: Final = 8_000
"""
The smallest headroom a handoff run can be expected to finish in, in tokens.

The other end of the window `RESERVE` opens, and a constant rather than a setting because it is not a
preference: it is roughly what the ask, one look at the tree and a short document come to, so below
it a handoff is a request nobody should pay for. What it is *for* is the turn that crosses the
reserve and overshoots it, which one large tool return is enough to do.

Below this the console stops offering rather than reaching for something smaller. A cheaper
non-agentic summariser would be a second path that only ever runs when the first is already failing,
so nothing would exercise it and its bugs would surface during the one moment a conversation is least
able to absorb them; saying the reserve is gone and leaving the person to `forget` or to fork costs
nothing and cannot be wrong.
"""


THOUSAND: Final = 1_000
"""
What the reserve box is denominated in, because a reserve is only ever chosen in round thousands.

The stored value stays tokens, since tokens are what every other figure on this page is in and what
the arithmetic is done in; what changes is the *control*, where six digits is a number to count the
zeroes of rather than one to read. So the box holds `40` with a `K` beside it, the boundary multiplies
and the card divides, and the multiplier is named once here so the two halves cannot disagree.

A stored reserve that is not a round thousand renders truncated and is saved back rounded down, which
nothing in this console can produce: only a hand-edited row can hold one, and rounding a number
somebody wrote into a database by hand is the least of what they were asking for.
"""

HANDS_OFF_FIELD: Final = "hands_off"
RESERVE_FIELD: Final = "reserve"
"""
What the two controls are called on the form that sets them, and what the columns are called.

One word each, in the page, in the boundary that parses it, and in the table. They live here rather
than beside `GUIDING_FIELD` in `conversation.py` for `roots.py`'s reason: `pages.py` renders the
controls and `console.py` parses them, and the module that owns the vocabulary is the one both can
read without closing a ring.

**An unchecked checkbox posts no field at all**, so an absent `hands_off` on a *form* means off,
where an absent `hands_off` in the *column* means the default. Those are answers to two different
questions - what this form said, against what anybody has ever said - and nothing has to reconcile
them, because the boundary resolves a form to a whole `Tending` and `tend` writes both columns.
"""


@dataclass(frozen=True, slots=True)
class Tending:
    """
    What this console does for a session unasked, which is the one thing about a session that changes.

    Against `Choice`, which is recorded before the first message and fixed for the session's life:
    that is what a session *is*, and forking is how it changes. This is what is being done *to* a
    running session, so it has to be changeable while the session runs or it is not a setting at all.

    Two fields rather than one, and the pair is not the denormalized state it resembles. A reserve of
    zero would express "off" arithmetically - a window that opens only where the context has reached
    the whole of the model's - but it would throw the number away, so a person switching the console
    back on would be handed the default rather than what they had picked. Keeping the amount while the
    switch is off is what a switch is for.

    Its defaults are module constants rather than `Settings` fields, deliberately. A process-wide
    answer would be a second place a session's question is answered, exactly as a process-wide model
    would be, and nobody has asked to set these per console.
    """

    hands_off: bool = HANDS_OFF
    reserve: int = RESERVE


TENDED: Final = Tending()
"""
The shipped defaults as one value, which is what a session nobody has told anything runs on.

Named because three places need the same one and none of them can construct it in a default argument:
`Session.tending` wants a factory, the picker starts a new session on it, and `Service.start` compares
against it to decide whether a column is worth writing at all.
"""


def parse_tending(hands_off: int | None, reserve: int | None) -> Tending:
    """
    A session's own settings, with each absent column read as the constant it defaults to.

    Each independently, unlike `parse_origin`, which demands its pair. An origin's two columns are
    halves of one fact, so a row holding one is a row nothing here could have written; these are two
    settings, and a session that has been told one of them and not the other is an ordinary state.

    A stored reserve is taken as it stands and never clamped. What a nonsensical one produces is a
    window that never opens, which `standing` answers for itself, and quietly correcting a number
    somebody typed into the shape this console prefers is how a setting stops meaning what it says.
    """
    return Tending(
        hands_off=HANDS_OFF if hands_off is None else bool(hands_off),
        reserve=RESERVE if reserve is None else reserve,
    )


@dataclass(frozen=True, slots=True)
class Reserve:
    """
    A reserve as where it falls in one model's window, rather than as the amount it is.

    The same setting located, not a second name for it: `Tending.reserve` is how much room to keep and
    this is which stretch of context that keeps it in. Both readers want the located form - the pass
    to know whether a turn has crossed into it, the card to say how far off it is - so it is worked
    out once here rather than twice from the number.

    `opens` is the context at or above which a handoff is worth asking for, and `shuts` is the context
    above which there is no longer room to write one. Two bounds rather than a threshold, because a
    single turn can cross the first and overshoot the second, which one large tool return is enough to
    do.
    """

    opens: int
    shuts: int


def reserving(window: int | None, reserve: int) -> Reserve | None:
    """
    Where a reserve falls in a model's window, or nothing at all where the question cannot be put.

    Two cases and one answer. A model the reference database has never heard of has no window, so
    there is no fraction and no way to know a reserve was crossed. And a reserve at least as large as
    the window, or smaller than the room a handoff needs at all, describes no interval: the first
    would have every session due from its first turn, and the second would open a stretch that is
    already shut. Both are a console that cannot say where a conversation stands, and neither is a
    reason to guess at one.

    It is asked of the *reserve* rather than of a whole `Tending`, because whether the console will
    act on the answer is a different question from where the conversation is. A card says where a
    session stands whether or not it is switched on, and it can only do that if the switch is not in
    here.
    """
    if window is None:
        return None
    held = Reserve(opens=window - reserve, shuts=window - LEAST_ROOM)
    return None if held.opens <= 0 or held.opens > held.shuts else held


type Standing = Literal["room", "due", "gone"]
"""
Where a conversation sits against the room its own handoff would need.

Three answers because there are three, and the middle one is the whole reason this is not a
threshold: `room` is a conversation with talking left in it, `due` is one whose reserve is crossed
*and* which can still write the document, and `gone` is one that overshot between two turns and now
cannot. Only the middle one is something to act on, which is what keeps the console from spending a
request on a handoff that will not fit.
"""


def standing(context: int, window: int | None, reserve: int) -> Standing | None:
    """Where a conversation carrying `context` tokens sits against its own reserve, if anything says."""
    held = reserving(window, reserve)
    if held is None:
        return None
    if context < held.opens:
        return "room"
    if context <= held.shuts:
        return "due"
    return "gone"
