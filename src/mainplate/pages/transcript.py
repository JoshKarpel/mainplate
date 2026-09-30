# The conversation as panels and rules, and what a session is waiting on.
#
# One live region, and it is the transcript. A turn being answered is the only thing on this console
# that changes without somebody doing anything, and it changes several times while it runs, so the
# region carries no `hx-` attribute of its own: the page holds one connection, outside everything
# that swaps, and the server sends this region down it whenever the session records anything.

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from itertools import groupby
from typing import Final
from typing import assert_never

from without_html import Child
from without_html import Element
from without_html import a
from without_html import code
from without_html import details
from without_html import div
from without_html import p
from without_html import pre
from without_html import span
from without_html import summary

from mainplate.calls import block_diff_element
from mainplate.calls import call_body
from mainplate.calls import changes_by_file
from mainplate.calls import recorded_kept
from mainplate.calls import subject_of
from mainplate.commands import UNFINISHED
from mainplate.conversation import Block
from mainplate.conversation import Command
from mainplate.conversation import Guidance
from mainplate.conversation import Kind
from mainplate.conversation import Panel
from mainplate.conversation import Prose
from mainplate.conversation import Reasoning
from mainplate.conversation import Spent
from mainplate.conversation import Steering
from mainplate.conversation import ToolUse
from mainplate.conversation import Transcript
from mainplate.markup import as_document
from mainplate.markup import as_message
from mainplate.markup import linked_text
from mainplate.pages.document import Links
from mainplate.pages.document import opens
from mainplate.pages.document import rule_id
from mainplate.pages.document import working
from mainplate.pages.figures import along
from mainplate.pages.figures import charged
from mainplate.pages.figures import consumed
from mainplate.pages.figures import portion
from mainplate.pages.figures import tokens
from mainplate.pages.moments import Reader
from mainplate.pages.moments import dated
from mainplate.pages.moments import elapsed
from mainplate.pages.moments import stamped
from mainplate.pages.moments import timed
from mainplate.pages.moments import when_element
from mainplate.service import Conversation
from mainplate.sessions import Claimed
from mainplate.sessions import Delayed
from mainplate.sessions import Idle
from mainplate.sessions import Queued

TRANSCRIPT_ID: Final = "transcript"


# What each kind of panel is called where a person reads it: the role label on the panel, and the
# chip in the key that governs it. One mapping, so the legend and the thing it is a legend for
# cannot come to disagree about what a kind is called.
NAMES: Final[tuple[tuple[Kind, str], ...]] = (
    ("system-prompt", "system prompt"),
    ("guidance", "guidance"),
    ("prompt", "prompt"),
    ("note", "note"),
    ("steer", "steer"),
    ("command", "command"),
    ("thinking", "thinking"),
    ("assistant", "assistant"),
    ("tool", "tool"),
)


# The one kind whose label does not say everything about it. `you (ran)` used to carry the fact that
# no model was ever told about a command; `command` does not, so it is said here instead, which is
# where the cost estimate already says the thing a figure cannot. Only this one, because it is the
# only kind whose name leaves something out. Named for the attribute rather than for what it holds,
# since `aside` is taken and means a side conversation.
TITLES: Final[dict[Kind, str]] = {
    "command": "You ran this yourself, in the session's checkout. No model was told about it.",
    # The other kind whose label leaves something out: `guidance` says what it holds and not why it
    # is here, which is that a tool reached into a part of the repository carrying its own.
    "guidance": (
        "The console handed this to the model when it reached into a part of the repository "
        "that carries its own guidance."
    ),
    # The third, and the one where leaving it out would be worst: every other message in a
    # conversation was typed by somebody, so a reader has no reason to suspect this one was not.
    # **It is the fallback rather than the answer**: a note carries its own `title` where the plugin
    # that asked for it named one, since a pre-commit failure and a handoff document are different
    # things to meet halfway down a transcript. This is what a plugin that named none gets.
    "note": "A plugin asked for this. Nobody in the conversation typed it.",
}


# Which side of the exchange a kind is on: what reached the model, and what the model produced.
# The dock's flanking arrows step one side each, and the palette runs on this same axis, so it is
# stated once here rather than in both places.
SIDES: Final[dict[Kind, str]] = {
    # The person's side, by the same rule as `command`: the axis is who produced the text, and what
    # is in a system prompt was written by the operator and by whoever wrote the repository's own
    # guidance. The console composed it; it did not write it.
    "system-prompt": "person",
    # The same rule one mechanism along: whoever wrote the repository's `AGENTS.md` wrote this, and
    # the console handed it over. What separates it from the standing prompt is where it sits in the
    # request, which is not something a hue can say.
    "guidance": "person",
    "prompt": "person",
    # The person's side because the axis is who produced the *text*, and what a note holds was
    # produced by this console's own machinery with the model as the party about to be told. Its
    # `title` says a plugin asked for it, exactly as `command`'s says no model was told. What a
    # plugin gets to vary is the `tone`, which is weight *within* this side rather than a hue
    # competing with it.
    "note": "person",
    "steer": "person",
    # The person's side because the axis is who produced the text, which is the same rule `steer`
    # follows. It is the one kind on that side the model never saw, and the panel's own `title` says
    # so rather than the palette: a hue is for who, not for who was told.
    "command": "person",
    "assistant": "model",
    "thinking": "model",
    "tool": "model",
}


# Which panels the server draws open, and every panel folds, so this is the whole of the default.
#
# **The reader's is the last word, and the server only says where they start.** What a person opens
# or shuts survives every swap - `mainplate.js` keeps the decision, not a set of the ones they
# unfolded - so this is a first offer rather than a rule about what may be read. A kind added
# without an entry here is a `KeyError` at render, which is the same bargain `SIDES` takes and for
# the same reason: a default nobody chose is worse than a page that will not draw.
#
# Reference is shut and conversation is open, which is the one line through every kind here. A system
# prompt and a delivered guidance file are documents somebody committed, so they are drawn as the
# line they open with; everything else is what was said, and a conversation whose replies had
# to be opened one at a time would not be a transcript. A note falls on the conversation side of
# that despite often reading like a document: it is a message, it is the one nobody wrote, and so it
# is the one a reader cannot recall for themselves. A tool panel is *open* with each call inside it shut,
# which is today's rendering exactly: the calls are listed, and what each was handed is a press away.
OPENS: Final[dict[Kind, bool]] = {
    "system-prompt": False,
    "guidance": False,
    "prompt": True,
    # Open, because it is a message and not reference material: what it says is why the turn under it
    # goes the way it does, and it is the one message a reader did not write and so cannot recall.
    "note": True,
    "steer": True,
    "command": True,
    "thinking": True,
    "assistant": True,
    "tool": True,
}


def figures_element(
    spent: Spent,
    whose: str,
    reader: Reader,
    when: datetime | None = None,
    opens: bool = False,
    window: int | None = None,
    running: Decimal | None = None,
) -> tuple[Element, ...]:
    """
    What something cost and when it landed, or nothing at all where it has not answered yet.

    The counts are always drawn and the money only where something priced it, because they fail
    independently: the tokens are on the response itself and are known for every turn on every wire,
    where the price needs a record in a database this console may not have been asked to read. A
    turn showing counts and no money is one whose model nobody published a price for, which is the
    same blank its card shows and is why the two are separate elements rather than one sentence.

    `whose` names what the figures are of, because a turn rule stands at a request boundary too and
    carries the **turn's** total beside that request's own marker. Identical where a turn took one
    round trip and visibly different where it took several, so the title is what settles which is
    being read rather than the reader inferring it from the size of the number.

    **The moment leads, and the duration follows it**, which is the pair read together: the answer
    landed at 09:32 and the provider was waited on for 3.4s of that. Both are about the round trips
    to the provider rather than the turn from end to end, since the calls a turn made in between are
    timed on their own panels.

    The moment is when the provider's answer came back, which is `ModelResponse.timestamp` and the
    only moment a request records. What it is *not* is when the request went out: that is this figure
    less the one beside it, and deriving it here would put a moment on the page that nothing wrote
    down. `opens` says this is a turn rule, where the moment is the turn's **first** answer rather
    than the whole turn's: it is the one figure a turn rule takes from a request instead of from the
    turn, and it is that way round so that the moments read down the page in the order they happened.
    A turn's last answer on the rule that opens it would run backwards against the requests below.

    **The input figure is the context and not the sum**, which is `Spent.context`'s whole argument
    said on the page: what a reader wants off a rule is how full the window is, and a turn's
    requests each carry the conversation again. The fraction beside it is the same fact as a
    percentage, and the gauge on the rule itself is the same fact again as a picture - one number
    said three ways because the question it answers is the one a long conversation ends on.

    **Symbols and not words.** A rule is a single line that must not wrap, and it now carries seven
    figures where it carried three. `\N{UPWARDS ARROW}` and `\N{DOWNWARDS ARROW}` are a count of
    tokens going up to the model and coming back, `\N{WHITE SQUARE CONTAINING BLACK SMALL SQUARE}` is how much of
    the first came out of the provider's cache instead, and `\N{GREEK CAPITAL LETTER DELTA}` against
    `\N{N-ARY SUMMATION}` is what this one exchange added against the running total, which is that
    pair's own notation and reads as a pair rather than as two prices to tell apart by size. Five
    cells rather than the twenty or so the words would take, and the words are in the titles where
    there is room to say which is which.

    `running` is everything up to and including whatever this rule speaks for, which is the figure a
    person scrolling actually wants: what one turn cost is only readable against what the
    conversation has cost so far. It is left out where it *is* what the rule already says, since the
    first priced turn of a session would otherwise print one number twice.

    **The separator goes between the figures rather than in front of each of them.** Every one of
    these is drawn only where there is something to say, so a dot carried by a figure is a dot that
    appears or disappears with it: baked in, the time had none and the count after it had one, and a
    turn nothing timed then opened with a dot standing for nothing. Interleaving it here is the one
    place that knows what is actually being drawn.

    It is carried *inside* each figure all the same, which is what a narrow window asks for: the
    stylesheet drops three of these on a phone, and a dot standing between them as an element of its
    own would be left behind by the figure it belonged to.
    """
    if not spent.asked and not spent.answered:
        return ()
    fraction = consumed(spent.context, window)
    figures: list[tuple[str, str, tuple[Child, ...]]] = []
    if spent.took is not None:
        figures.append(("took", f"{whose} spent {elapsed(spent.took)} waiting on the model", (elapsed(spent.took),)))
    if spent.context:
        figures.append(
            (
                "context",
                f"{whose}: {spent.context:,} tokens of context",
                (
                    f"\N{UPWARDS ARROW}{tokens(spent.context)}",
                    # Inside the count rather than beside it, and in brackets, because it is a fact
                    # *about* that count and not a figure of its own: what is cached is part of the
                    # context, the way the wire's own numbers nest. Its own element all the same, so
                    # a phone can drop the bracket and keep the count.
                    *(
                        (
                            span(
                                cls="rule__cached",
                                attrs={
                                    "title": f"{spent.cached:,} of those tokens were read from the provider's cache"
                                },
                                children=f" (\N{WHITE SQUARE CONTAINING BLACK SMALL SQUARE}{tokens(spent.cached)})",
                            ),
                        )
                        if spent.cached
                        else ()
                    ),
                ),
            )
        )
    if fraction is not None and window is not None:
        figures.append(
            (
                "full",
                f"{portion(fraction)} of this model's context window, which the reference gives as {window:,} tokens",
                (portion(fraction),),
            )
        )
    figures.append(
        ("answered", f"{whose} wrote {spent.answered:,} tokens", (f"\N{DOWNWARDS ARROW}{tokens(spent.answered)}",))
    )
    if spent.cost is not None:
        figures.append(
            (
                "cost",
                f"{whose}, estimated from published rates and not billed: ${spent.cost:f}",
                (f"\N{GREEK CAPITAL LETTER DELTA}{charged(spent.cost)}",),
            )
        )
    if running is not None and running != spent.cost:
        figures.append(
            (
                "running",
                f"${running:f} up to and including {whose.lower()}, estimated from published rates and not billed",
                (f"\N{N-ARY SUMMATION}{charged(running)}",),
            )
        )
    return (
        *(
            (
                when_element(
                    when,
                    cls="rule__when",
                    title=f"{whose} was {'first ' if opens else ''}answered at {stamped(when, reader)}",
                    said=timed(when, reader),
                ),
            )
            if when is not None
            else ()
        ),
        *(
            span(
                cls=f"rule__{named}",
                attrs={"title": title},
                children=list(said) if at == 0 and when is None else ["\N{MIDDLE DOT} ", *said],
            )
            for at, (named, title, said) in enumerate(figures)
        ),
    )


def written(text: str, *, document: bool = False) -> Element:
    """
    Prose, as the Markdown its author almost certainly meant it to be.

    The renderer is what makes putting this in a child position safe, and it is the only reason
    this is not simply escaped text: it renders the Markdown and then throws away everything the
    result is not allowed to contain, so a model that echoed a prompt back cannot put a script or
    a `javascript:` link on this page. See `markup.py` for why both halves of that are needed.

    `document` says the text is a file rather than something typed into a box, which decides whether
    its own newlines are line breaks. See `markup.py` for why that is the one difference.
    """
    return div(cls="text", children=as_document(text) if document else as_message(text))


def tool_block(links: Links, used: ToolUse, anchor: str, at: int) -> Element:
    """
    One call, folded, with what it was handed and what it gave back.

    A real `<details>` because that is what works with no script at all and what the dock's fold
    controls act on. Every call is folded: what a read brought back or a command said is context a
    reader reaches for rather than prose they read through, and what the calls that write did is
    the batch's diff below the panel, which covers every file they touched at once. The cost,
    stated: a session with no checkout has no snapshots and so no batch's diff, and there an `edit`
    or a `create` is shut with nothing standing in for it.

    **Shut whether or not the call has come back.** A call still out is drawn working, with the dots
    in its summary, and folded exactly as it will be once it returns, because the script keeps every
    toggle as the reader's decision: a fold whose default moved as its result landed would be
    recorded as a decision nobody made. What the summary says is what makes shut affordable, since
    the subject of the call - the path, the command - is on the line a reader scans without a press.

    The id is the panel's own plus this block's place in it, which is stable in both halves: a
    panel's blocks only ever grow at the end, so a call keeps its place once made whether or not it
    has come back, and everything a turn draws after a response - a later response, a command -
    lands after the panel rather than in front of it, so the panel's own `at` does not move either.
    A command's fold cannot be named this way for exactly that reason; see `command_block`. The
    script needs it to put a reader's unfolded calls back after a swap, since morphing removes an
    attribute the new markup does not carry.

    What is under the summary is `call_body`'s, drawn per tool; see `calls.py`.

    **A call that kept an artifact version links to it from the summary**, beside the path, so the
    page it made is one press away without opening the call. Pinned to that version, since what the
    call made is the thing a reader is asking about, whatever the artifact has become since.
    """
    subject = subject_of(used.tool, used.arguments)
    kept = recorded_kept(used)
    return details(
        cls="tool",
        attrs={"id": f"{anchor}-tool-{at}", **opens(False)},
        children=[
            summary(
                children=[
                    span(cls="tool__name", children=used.tool),
                    *(
                        ()
                        if subject is None
                        else (
                            span(cls="tool__subject", attrs={"title": subject.said}, children=subject.said),
                            *(
                                (span(cls="tool__extent", children=subject.extent),)
                                if subject.extent is not None
                                else ()
                            ),
                        )
                    ),
                    *(
                        ()
                        if kept is None
                        else (
                            a(
                                cls="tool__kept",
                                attrs={"href": links.to_artifact(kept.artifact, kept.version)},
                                children=f"artifact v{kept.version}",
                            ),
                        )
                    ),
                    # Beside the outcome rather than in the body, because how long a call ran is what
                    # a reader scanning a folded turn wants and the body is what they open when they
                    # want the rest. Absent while a call is still out: a figure there would have to
                    # count up, and what says a call is running is the working mark already beside it.
                    *(
                        (
                            span(
                                cls="tool__took",
                                attrs={"title": f"This call took {elapsed(used.took)}"},
                                children=elapsed(used.took),
                            ),
                        )
                        if used.took is not None
                        else ()
                    ),
                    working()
                    if used.returned is None
                    else span(
                        cls="tool__outcome",
                        attrs={"data-outcome": used.returned.outcome},
                        children=used.returned.outcome,
                    ),
                ]
            ),
            call_body(used),
        ],
    )


def status_element(status: int) -> Element:
    """
    What a command exited with, as the number and what it means.

    The **number**, not "failed", because an exit status is a program's own vocabulary and flattening
    it loses what it said: `git diff --quiet` exits 1 to mean *there are changes*, and `grep` exits 1
    to mean *no match*, neither of which is a failure. Zero is shown as the number too, in the same
    words as the rest, so a reader scanning a turn reads one column of statuses and the colour alone
    is what picks out the ones that are not zero; a word of its own for zero was a second vocabulary
    for the value the colour already sets apart.

    `UNFINISHED` is the console admitting it never learned, which is a third thing rather than a bad
    exit: the process that would have read the status was stopped first.
    """
    if status == UNFINISHED:
        return span(
            cls="ran__status",
            attrs={"data-status": "unfinished", "title": "This never reported a status"},
            children="unfinished",
        )
    return span(
        cls="ran__status",
        attrs={"data-status": "ok" if status == 0 else "other", "title": f"This exited with status {status}"},
        children=f"exit {status}",
    )


def command_block(ran: Command) -> Element:
    """
    One command the person ran, with what it said open under it.

    **Open, where a tool call is folded, and the difference is who asked.** A call is the model
    reaching for context, so its output is something a reader opens when they want to check the
    work; a command is something the person typed themselves, and what it said is the whole of why
    they typed it. So it is still a `<details>` - it folds, the dock's fold controls act on it, and
    a reader who has read one can shut it - but it does not have to be opened to be read.

    Which means the fold is now a `<details>` the server renders *open* and the reader may shut,
    where a call is one it renders shut and the reader may open. `mainplate.js` therefore keeps
    what the reader decided rather than only what they unfolded, or a morph mid-turn would reopen
    a command they had just put away.

    **The id is the command's own inbox entry, and deliberately not the panel's anchor** as a call's
    is. A panel's position moves while a turn is answered - a response landing above pushes it down -
    so a fold identified by it is a decision the script loses on the next response. The entry is the
    store's own name for the thing, minted once and never reused, so this is the record's name for it
    rather than a second numbering.

    The command itself is shown verbatim and never as Markdown. It is a shell line, so the
    backticks, asterisks and underscores in it are characters rather than emphasis, and rendering it
    would change what a reader is told they ran.

    No `data-markdown`, for the same reason `tool_block` carries none: what is on the page is already
    the source, so an attribute repeating it would be the second copy that one is not.

    **A command with no output says so, rather than drawing an empty box.** Plenty of them have none
    - `git diff --quiet` is the fixture's own example, and every command whose whole answer is its
    exit status - and a blank pane under one reads as output that failed to arrive. It is a stated
    absence for the same reason `no reference record` is: a reader's next question is what happened,
    and an empty rectangle makes them ask it.

    **And it is drawn folded**, where a command with output is drawn open: its line, its time and
    its status are the whole of what there is to read, and all three are on the summary, so an open
    fold would spend a row on the sentence saying there is nothing under it. A command still running
    is drawn open, as every command is until it finishes, so one that finishes with output does not
    have to be opened to be read.

    **A push is drawn from the branch it records, never from its text.** Its text is `push`, which is
    also what a person running `push` in the sandbox recorded, so a page reading the text would draw
    the two alike. What a push shows is the branch, under a mark of its own in place of the `$`:
    nobody typed it into a shell, and the branch is what a reader needs to know went where.
    """
    said = None if ran.result is None else ran.result.output
    return details(
        cls="ran" if ran.pushed is None else "ran ran--push",
        attrs={"id": f"ran-{ran.entry}", **opens(said is None or bool(said))},
        children=[
            summary(
                children=[
                    *(
                        (code(cls="ran__line", children=ran.text),)
                        if ran.pushed is None
                        else (
                            span(
                                cls="ran__pushed",
                                attrs={"title": "This console pushed the session's branch to its repository"},
                                children="push",
                            ),
                            code(cls="ran__line", children=ran.pushed),
                        )
                    ),
                    *(
                        (
                            span(
                                cls="ran__online",
                                attrs={"title": "This ran with the network on, so it could reach the repository"},
                                children="online",
                            ),
                        )
                        if ran.online
                        else ()
                    ),
                    *(
                        (
                            span(
                                cls="ran__took",
                                attrs={"title": f"This took {elapsed(ran.result.took)}"},
                                children=elapsed(ran.result.took),
                            ),
                        )
                        if ran.result is not None and ran.result.took is not None
                        else ()
                    ),
                    working() if ran.result is None else status_element(ran.result.status),
                ]
            ),
            *(
                ()
                if said is None
                else (
                    div(
                        cls="ran__body",
                        children=pre(children=code(children=linked_text(said)))
                        if said
                        else span(cls="ran__silent", children="no output"),
                    ),
                )
            ),
        ],
    )


# How much of a panel's prose is carried into the line its row stands for it with. Not a decision
# about how much is *shown*: what a shut panel shows is whatever fits, clipped with an ellipsis by
# the browser at whatever width the panel happens to have, which is the one measurement no server can
# make. This is only the bound on what is carried, and it exists because a line holding the whole of
# a long block would put every word of it on the page twice, on a region that is re-rendered whenever
# the turn in flight records anything.
#
# The number is what the clipping needs to stay honest: clipped short of the bound the ellipsis says
# there is more, and clipped *at* the bound with no ellipsis it would say there is not. So it has to
# exceed what the widest panel can show, which is a bounded question because the transcript is capped
# at `--measure`. `TestTheLineAShutPanelStandsFor` measures the worst case there is - the narrowest
# character this console's prose face draws, repeated - and fails if it fits.
OPENING: Final = 320


def opening_of(text: str) -> str:
    """
    The front of a block of prose, as the one line a shut fold stands for.

    Whitespace collapsed rather than left as written, because the summary is one line either way: a
    browser collapses it in the markup, so a paragraph break carried here would spend the bound on
    characters that draw as one space. Collapsing first makes `OPENING` a count of what a reader
    could actually see.

    Markdown markers are left in it. A model that opened its reasoning with a heading wrote that
    heading, and rendering it here would need a second rendering path for a line that has nowhere to
    put a block element; the fold under it is one press away for anybody who wants it set properly.
    """
    return " ".join(text.split())[:OPENING]


def written_block(kind: str, text: str, *, document: bool = False) -> Element:
    """
    One block of rendered Markdown, carrying the Markdown it was rendered from.

    The attribute is what a copy button hands over, and it has to be here because the rendering is
    lossy in exactly the way a person copying cares about: the fences, the emphasis, the list markers
    and the table are all gone from the text of the page, and no reading of the rendered markup gets
    them back. Nothing else reads it - it is not a second copy of anything, since it is the same
    value this element was built from, put into the same render.

    Only the kinds that *are* Markdown. A tool's arguments and its return are shown verbatim already,
    so what is on the page is the source, and an attribute repeating it would be the second copy this
    one is not.

    `document` goes straight to `written`, and says the text is a file rather than something typed
    into a box: guidance is one, and everything else here was written in the conversation.
    """
    return div(cls=("block", kind), attrs={"data-markdown": text}, children=written(text, document=document))


def block_element(links: Links, block: Block, panel: Panel, at: int) -> Element:
    """
    One block, told where it is by the panel holding it.

    The panel rather than its anchor, because the two kinds that fold are named from different
    halves of it: a call is addressed by the panel it is in, which never moves once made, and a
    command by its own inbox entry, because the panel a command is in does move. See
    `command_block`.

    **Only two kinds fold in here, and the rest are drawn plain.** A stretch of reasoning, the
    standing system prompt and a delivered guidance file each used to carry a `<details>` of its own
    whose summary was the front of its own body - so a panel spent one row saying what it was and a
    second row saying it again, and the second row was a lone marker once the fold was open. The
    panel is that fold now, and its opening line is on the panel's own row. See `panel_element`.

    A call and a command keep theirs, because neither summary is a prefix of anything: a tool's name
    with what it returned and how long it ran, and a command's line with the status a program chose,
    are facts about the block rather than the block restated. A panel also holds a *batch* of either,
    so a reader wanting one read out of three needs a fold per call and not only a fold per panel.
    """
    match block:
        case Prose(text=text):
            return written_block("block--text", text)
        case Steering(text=text):
            return written_block("block--text", text)
        case Guidance(text=text):
            # Drawn as the document the standing system prompt is drawn as, because it is the same
            # kind of thing: an `AGENTS.md` with a line of the console's own in front of it. What
            # separates the two is where each sits in the request, which is the panel's business.
            return written_block("block--document", text, document=True)
        case Command():
            return div(cls=("block", "block--ran"), children=command_block(block))
        case Reasoning(text=text):
            return written_block("block--thinking", text)
        case ToolUse():
            return div(cls=("block", "block--tool"), children=tool_block(links, block, panel.anchor, at))
        case _ as unreachable:
            assert_never(unreachable)


def request_label(turn: int, at: int) -> Element:
    """
    The `r{turn}.{at}` marker on a rule, naming the model request the rule stands in front of.

    Named the whole way, for `Panel.address`'s reason one level along: a rule inside a turn draws no
    `#N`, so a bare `r1` said which request without saying of what. The `r` is what keeps it from
    being read as a panel, which numbers a different axis - `#3.1` is turn 3's second *panel* and
    `r3.1` is its second *request*, and one response becomes as many panels as it has kinds of part.
    """
    return span(
        cls="rule__request",
        attrs={"title": f"The {ordinal(at)} model request of turn {turn}"},
        children=f"r{turn}.{at}",
    )


def ordinal(at: int) -> str:
    """`0` as `first`, for a title that reads as a sentence rather than as an index."""
    names = ("first", "second", "third", "fourth", "fifth")
    return names[at] if at < len(names) else f"{at + 1}th"


def rule_element(
    links: Links,
    reader: Reader,
    session: str,
    turn: int,
    asked: int | None = None,
    spent: Spent | None = None,
    when: datetime | None = None,
    opens: bool = False,
    forget: bool = False,
    window: int | None = None,
    running: Decimal | None = None,
) -> Element:
    """
    A line across the conversation where one round trip to the model began.

    A rule rather than a row on a panel, because everything on it is true of a *request* or of the
    turn around it, and a panel is neither. A request is a round trip and a panel is a run of one
    kind, so one response becomes as many panels as it has kinds of part: a marker hung on one of
    them attributed a round trip to a fraction of itself, and a turn's cost had nowhere to go at all.

    A rule per request, with the **turn rule** being the first one, which costs no new concept: every
    rule this transcript has ever drawn already stood at a request boundary, because a turn opens
    with its first request. What the first one carries besides is the turn's own facts - which turn
    it is, where the session may be forked from, and what the whole turn spent - and the later ones
    carry only their own request's.

    It is the fork's own place for a reason beyond tidiness. The branch point is *before* the forked
    turn's message, which is exactly where a turn rule sits, so the link names the position it acts
    on instead of sitting inside the first thing that comes after it. That also retires the hover: a
    control revealed by pointing at a panel does not exist on a touch screen, and forking is the
    only way a session changes its mind.

    A turn rule **names its turn**, and that is what makes it readable rather than decorative. It
    sits directly under the last panel of the turn before it, so a bare row of figures there reads as
    a footer summarising what is above it, which is the opposite of what it says: these are the
    counts for the turn that starts below. `#1` against the `#1.0` and `#1.1` on the panels under it
    settles the direction, and it doubles as the permalink to the boundary the fork acts on.

    **No snapshot hash**, although one is recorded at every boundary a rule stands on. Snapshots are
    how this console puts a session's files back for a fork, never something a reader handles: the
    checkout's own git names none of them, so a hash here would be a detail of the mechanism
    dressed as a fact about the work, and it would cost the line room the figures need.

    `rule--turn` is what the dock's turn arrows step, so that column keeps stepping turns now that
    there are rules between them as well as before them.

    **`rule--forget` is the one rule that describes what is *above* it**, and that is what lets the
    sentence be short: every other rule looks forward at the request or the turn it opens, so there is
    no ambiguity about which direction this one means. Its `cleared` is not the `clear` the control is
    deliberately not called, and the object is what tells them apart: what was cleared is the
    **context**, where a bare `clear` beside a transcript that keeps every word would be claiming the
    transcript was. It is drawn in a heavier line rather than a colour of its own, because the palette
    runs on one axis - cool for what the person produced, warm for what the model did - and a boundary
    is neither. The panels above are left exactly as they were: what changed is what the model is
    handed, not what is worth reading, and fading them would say the second thing while colliding with
    `muted`, which is the reader's own decision and already drawn that way.

    The fork link says something extra here and is the *same link*, at the same turn, posting the same
    thing. Continuing the conversation a forget closed is `fork` at that turn: `before` copies the
    turns below the branch point and the marker lives on the turn that opens, so the branch carries
    the whole backlog and no boundary. A control of its own would be a second name for one call.

    **The line is also the gauge**, filled from the left as far as this request's context reaches
    into the model's window and shading toward red as it goes. A rule is already a hairline drawn
    across the whole column at every request boundary, so the one thing a long conversation most
    wants to know - how close it is to the end of the window - costs no row and no control: a reader
    scrolling down watches the line lengthen and warm. The fraction is the only thing the server
    computes into the markup, as one custom property; the colours, the geometry and the cap are the
    stylesheet's, because they are decisions rather than facts.
    """
    filled = consumed(spent.context, window) if spent is not None else None
    return div(
        cls=("rule", "rule--turn" if opens else None, "rule--forget" if forget else None),
        attrs={
            "id": rule_id(turn) if opens else f"{rule_id(turn)}-{asked}",
            "data-turn": str(turn),
            # Declared rather than inferred from the modifier, so the dock's column steps stops it is
            # told about the way every other arrow does. See `dock_card`.
            "data-stop": "forget" if forget else None,
            # Capped here as well as clipped there, so a session past a window the database
            # understates asks for no more line than there is.
            "style": None if filled is None else f"--filled: {along(filled)}",
        },
        children=[
            *(
                (a(cls="rule__at", attrs={"href": f"#{rule_id(turn)}", "title": f"Turn {turn}"}, children=f"#{turn}"),)
                if opens
                else ()
            ),
            *(
                (
                    a(
                        cls="rule__fork",
                        attrs={
                            "href": links.to_fork_form(session, turn),
                            "title": (
                                f"Fork from turn {turn}, carrying everything above this line"
                                if forget
                                else f"Fork from turn {turn}"
                            ),
                        },
                        children="fork",
                    ),
                )
                if opens and session
                else ()
            ),
            *((request_label(turn, asked),) if asked is not None else ()),
            span(cls="rule__span"),
            # Between two spacers rather than beside the fork link, so it sits in the middle of the
            # line with the turn's own controls at one end and its figures at the other. That is what
            # a boundary is: it belongs to neither side, and drawn against the left group it read as
            # one more thing about the turn rather than as the thing the rule is saying.
            *((span(cls="rule__forget", children="context cleared"), span(cls="rule__span")) if forget else ()),
            *(
                figures_element(
                    spent,
                    f"Turn {turn}" if opens else f"Request {turn}.{asked}",
                    reader,
                    when=when,
                    opens=opens,
                    window=window,
                    running=running,
                )
                if spent is not None
                else ()
            ),
        ],
    )


def panel_opening(blocks: Sequence[Block]) -> str:
    """
    The line a shut panel stands for: the front of what is in it.

    A shut panel has to say what it holds, or folding prose is a control that trades a paragraph for
    nothing. What that line *is* differs by what the blocks are, and the split is the one the old
    per-block folds already drew. Prose - a message, a steer, a reply, a stretch of reasoning, a
    document - stands for itself with its own opening, clipped by the browser at whatever width the
    panel has. A call and a command have no prose opening to take, so the panel names what is in it:
    a batch is `read, read` and `git status --short, git diff --quiet`, which is the one thing a
    reader scanning a shut turn wants from either. A push is named by the branch it sent, as its
    panel is.

    The *first* block for the prose kinds, and every block for the two that are named. That is not an
    inconsistency: an opening is a prefix, and a prefix of a run of paragraphs is the front of the
    first one, where a list of calls that named only its first would be hiding the rest.
    """
    match blocks:
        case [ToolUse(), *_]:
            return ", ".join(block.tool for block in blocks if isinstance(block, ToolUse))
        case [Command(), *_]:
            return ", ".join(
                block.text if block.pushed is None else f"push {block.pushed}"
                for block in blocks
                if isinstance(block, Command)
            )
        case [Prose(text=text) | Steering(text=text) | Reasoning(text=text) | Guidance(text=text), *_]:
            return opening_of(text)
        case _:
            # A panel with nothing in it, which is the one being waited on: it is the working dots
            # and a role, and there is no line for it to stand for.
            return ""


def panel_element(links: Links, session: str, panel: Panel) -> Element:
    """
    One run of one kind of thing, folded from the row of facts above it.

    `data-kind` and `data-side` are the whole of what the chrome needs to know: the key filters by
    kind, the dock's flanking arrows step by side, and the stylesheet draws the edge from the same
    attribute. Nothing has to keep a list of selectors in step with a list of kinds.

    **Every panel folds, and the mark is on its own row rather than under it.** What a reader wants
    put away is decided by what they are reading, so the console picks where each kind *starts* -
    `OPENS` - and nothing more. Three kinds used to carry a `<details>` inside the panel whose summary
    was the front of its own body, which spent a second row restating what this row already says, and
    once open spent it on a lone marker. Hoisted here that row is gone, the opening line rides beside
    the role, and every other kind gains a fold it never had.

    **The row is the summary, and what is in it keeps working.** A press on the permalink or on the
    copy button does not toggle the panel: the summary's activation behaviour skips a press whose
    target is interactive content, so a link and a button inside one navigate and copy as they always
    did. That is what lets this row be the fold without the row losing anything; it is a fact about
    the browser rather than about this markup, so `TestFoldingAPanel` asks Chromium.

    What a panel says is still what is *in* it, and nothing about the turn or the request around it.
    The checkout, the fork, what was spent and which request it was are all facts about the exchange rather
    than about any one run of blocks, so they are on the rules between them. See `rule_element`.

    Nothing here draws the copy buttons, and that is not an omission. One of them sits inside a
    fenced block, which is markup the Markdown renderer produced and this has no node to reach into,
    so seating them is `mainplate.js`'s - and a button in the markup for the panel beside a seated one
    for the code in it would be two mechanisms for one thing.
    """
    return details(
        cls="panel",
        attrs={
            "id": panel.anchor,
            "data-kind": panel.kind,
            "data-side": SIDES[panel.kind],
            # Which ink a note takes, which is weight *within* its side rather than a hue competing
            # with it. Drawn from the attribute rather than from a colour in the markup, because a
            # hex a plugin wrote is one this console could never restyle and one that reads well in
            # the light theme is the one that disappears in the dark. Absent on every other kind,
            # since only a note is ever asked.
            "data-tone": panel.tone if panel.kind == "note" else None,
            "data-turn": str(panel.turn),
            **opens(OPENS[panel.kind]),
        },
        children=[
            panel_meta(
                panel.kind,
                panel_opening(panel.blocks),
                anchor=panel.anchor,
                label=panel.address,
                # What the plugin that asked for this note called it, and what it said the note is.
                # Both fall back to the console's own answer for the kind, which for the role is the
                # word `note` and for the hover text is the sentence saying nobody typed it.
                role=panel.role,
                title=panel.title,
            ),
            *(block_element(links, block, panel, at) for at, block in enumerate(panel.blocks)),
            *(
                (div(cls=("block", "block--diff"), children=batch),)
                if panel.diff is not None and (batch := batch_element(panel.anchor, panel.diff)) is not None
                else ()
            ),
        ],
    )


# How many lines a batch's diff may run to and still be drawn open. A batch that merged a branch or
# ran a formatter over the tree changes thousands of lines nobody asked to read, and drawn open that
# is the whole transcript spent on it; shut, the summary still says how many files and lines it was.
# A knob rather than a rule, since where "too long to read in passing" starts is a judgement.
LONGEST_OPEN_DIFF: Final = 150


def batch_element(anchor: str, diff: str) -> Element | None:
    """
    The net change a batch made, as a fold of its own below the panel's calls, or nothing where no
    file's lines changed.

    Not shaped as a call, because it is not one: it is what the batch's calls came to, so it stands
    under them as a labelled rule with the diff bare beneath it, where a card like theirs read as a
    fourth call. It is still a fold, which the dock's fold-all and the script's memory of a reader's
    toggles reach the way they reach a call's. The rule says what a reader scanning a turn wants
    without opening it: how many files, and how many lines went in and out.

    **Open unless it is long, and that is not a default that moves.** The diff is recorded once,
    whole, when the request after the batch snapshots, so it arrives at the size it will always be
    and the fold's starting state is decided exactly once; see `opens`.
    """
    files = changes_by_file(diff)
    if not files:
        return None
    changes = [change for _, changed in files for change in changed]
    added = sum(change.mark == "+" for change in changes)
    removed = sum(change.mark == "-" for change in changes)
    lines = len(files) + len(changes)
    return details(
        cls="batch",
        attrs={"id": f"{anchor}-diff", **opens(lines <= LONGEST_OPEN_DIFF)},
        children=[
            summary(
                children=[
                    span(cls="batch__word", children="changed"),
                    span(cls="batch__files", children=f"{len(files)} file{'' if len(files) == 1 else 's'}"),
                    span(
                        cls="batch__lines",
                        attrs={"title": f"{added} lines added and {removed} taken away, over {lines} lines"},
                        children=[
                            span(cls="batch__added", children=f"+{added}"),
                            " ",
                            span(cls="batch__removed", children=f"\N{MINUS SIGN}{removed}"),
                        ],
                    ),
                ]
            ),
            div(cls="batch__body", children=block_diff_element(files)),
        ],
    )


def panel_meta(
    kind: Kind,
    opening: str | None,
    *,
    anchor: str | None = None,
    label: str | None = None,
    role: str | None = None,
    title: str | None = None,
) -> Element:
    """
    A panel's row of facts, which is also the summary that folds it.

    One function for all three panel shapes - a `Panel`, the standing system prompt, and the one
    saying a reply is being written - because the row is the same row and a second rendering of it
    would eventually disagree about where the marker sits or what the label is called.

    `opening` is `None` where there is nothing yet to stand for, and the row carries the working dots
    in the line's own place: a reply not written yet, and a stretch of context whose instructions the
    pass has still to compose. In the row rather than in the panel, because a panel opened to show
    three dots is a row spent on three dots, which is the thing this row exists not to spend.

    `anchor` is absent on the one panel there is nothing to link to: the panel saying a reply is
    being written is gone the moment it arrives, so a permalink to it points at nothing by the time
    anybody follows one.

    The marker itself is the stylesheet's, on the role, because it turns with the panel's own `open`
    and nothing here would have to be told twice.

    `role` and `title` are what a note's own plugin named, and every other kind passes neither: what a
    panel's role says is the console's word for the kind, and the one kind that can carry somebody
    else's is the one nobody in the conversation wrote. A plugin that named nothing gets the console's
    answer for both, which is why these are overrides rather than the only source.
    """
    return summary(
        cls="panel__meta",
        children=[
            span(
                cls="panel__role",
                attrs={"title": said} if (said := title or TITLES.get(kind)) is not None else {},
                children=role or dict(NAMES)[kind],
            ),
            span(cls="opening", children=opening if opening is not None else working()),
            *(
                (a(cls="panel__anchor", attrs={"href": f"#{anchor}"}, children=f"#{label or anchor}"),)
                if anchor is not None
                else ()
            ),
        ],
    )


def system_prompt_panel(turn: int, said: str | None) -> Element:
    """
    What every request in one stretch of context carried, under the rule that opens the stretch.

    Panel-shaped and not a `Panel`, which is the same split `waiting_panel` makes: a panel's identity
    is its turn and its position, and this belongs to the first but not the second. Giving it one
    would have taken `#N.0` off the person's opening message, which is an address the fork link and
    every permalink already point at.

    **One per stretch, under its own rule**, rather than one at the top of the page. A forget
    composes again, so a single panel above everything would be the newest instructions standing over
    turns answered under older ones. Under the rule the reader gets the order the conversation
    happened in: the boundary, then what the model is told from here, then the message.

    **`None` is a stretch whose instructions are not composed yet**, drawn as the panel with the
    working dots in it. Composing reads the repository's guidance out of a checkout the pass is the
    one to plant, so on a session's first turn there is a real gap between the message being there to
    render and this being there to put in it. Drawn rather than left out, so what is coming is
    visible from the moment the message is; it resolves on the same swap the first response arrives
    on, and `instructed_in` is what keeps it off a stretch nothing will ever compose for.

    **The dots go on the panel's own row, in place of the opening line, and the panel stays shut.**
    An open panel holding nothing but a spinner is a whole row spent on three dots, which is the
    thing hoisting the fold up here got rid of everywhere else. It also keeps this panel's default
    from *moving*: a fold whose default changes under a reader is one the console cannot draw either
    way once they have pressed it, because a press that put it back where it was is a decision
    withdrawn. See `opens` and `wireFolds`. Shut throughout, the wait is one row and what replaces it
    is the same row saying what the prompt opens with.

    **Drawn as the Markdown it is**, because what is in it is `.md` files - the operator's guidance
    and the repository's `AGENTS.md`, concatenated - so its headings, lists and fences are the
    structure its authors wrote, and a wall of `##` is the one reading of it nobody meant.

    That does not weaken the claim that this is what was *sent*. What the model was handed is the
    source, and the source is what this hands back: the block carries `data-markdown`, so the panel's
    copy button gives the characters rather than the rendering.

    It is a `.block` and not bare prose, and that is what puts the copy button on the panel: the
    script seats one against a panel's blocks, and it is the whole prompt somebody reaches for. A
    fence inside gets its own besides, which is the seating everywhere else.

    It is drawn as the same `block--document` the guidance a turn is handed mid-way is drawn as: on
    the page the two are the same thing, and what separates them is where each sits in the request,
    which is what the panel says rather than anything inside it. The id names the turn the stretch
    began at, which never moves, so a reader who shut this keeps it shut across every swap.
    """
    anchor = f"system-prompt-{turn}"
    return details(
        cls="panel",
        attrs={
            "id": anchor,
            "data-kind": "system-prompt",
            "data-side": SIDES["system-prompt"],
            **opens(OPENS["system-prompt"]),
        },
        children=[
            panel_meta(
                "system-prompt",
                opening_of(said) if said is not None else None,
                anchor=anchor,
            ),
            *((written_block("block--document", said, document=True),) if said is not None else ()),
        ],
    )


def out_on_a_call(said: Transcript) -> bool:
    """
    Whether the turn in flight is waiting on a tool rather than on the model.

    What decides whether the transcript already says it is working. A call with no result is drawn
    working on its own panel, and it is the model's call, so a second panel of dots under it says
    the same thing twice and says it in a shape - an empty reply - that nothing is writing.

    Asked of the turn being answered rather than of the last panel on the page, because a person can
    type while a reply is coming: what is at the bottom may be their message, and the turn that is
    actually out is the one above it.

    A *command* running is not this. It runs outside the conversation and no model was told about
    it, so it says nothing about whether one is answering.
    """
    return any(
        isinstance(block, ToolUse) and block.returned is None
        for panel in said.panels
        if panel.turn == said.answering
        for block in panel.blocks
    )


def waiting_panel() -> Element:
    """
    One panel for however many messages are outstanding, because one reply is what is actually
    being written: the turns behind it are queued, not in flight.

    Shut, with the working dots on its own row where the opening line goes, which is what a panel
    with nothing in it yet should cost: the wait is the whole of what this says, and a panel opened
    to show three dots spends a second row saying it again. See `panel_meta`.

    Not drawn at all where a call is still out, which `out_on_a_call` decides: what this panel is for
    is a wait nothing else on the page accounts for.
    """
    return details(
        cls="panel",
        attrs={"id": "waiting", "data-kind": "assistant", "data-side": "model", **opens(False)},
        children=panel_meta("assistant", None),
    )


def running_to(before: Decimal | None, spent: Spent | None) -> Decimal | None:
    """
    What a conversation has cost once one more turn or request is counted into it.

    `altogether`'s rule, applied one at a time rather than to a whole session: unknown anywhere is
    unknown from there on, so the first unpriced turn takes the running total off every rule below
    it rather than leaving a figure that is quietly the sum of everything else. A total missing a
    part reads as the whole and understates it, and a reader has no way to tell that from a cheap
    conversation.

    A turn nothing is recorded for is a turn nobody has asked anything yet, which costs nothing and
    so leaves the total where it was. That is a different answer from an unpriced turn, and the two
    arrive here as the same `None` from two different places: one is a turn absent from the mapping,
    the other is a cost the reference could not supply.
    """
    if before is None:
        return None
    if spent is None:
        return before
    if spent.cost is None:
        return None
    return before + spent.cost


# `reserve_mark` used to be here, and the plugin protocol is what deleted it. It drew a bar across
# every rule at the fraction the handoff reserve opens at, read off two columns this table no longer
# has: a reserve is a plugin's own setting now, and the console has no vocabulary for a plugin
# drawing in the transcript region. Stretching the card language that far to reach it would be
# inventing an axis in order to have a cross-product, so the gauge stays the console's and a handoff
# shipped as a plugin does without the mark. The fill itself is unaffected, since how much of the
# window a request used is the console's own arithmetic.


def end_rule(links: Links, session: str, turns: int) -> Element:
    """
    The rule under the last turn, carrying the one control the end of a conversation has: fork.

    A rule rather than a button, so it reads as the boundary it is - the place turn `turns` would
    open - and takes the same faint chrome every other rule's fork link has. It carries no figures,
    because nothing has been asked at it yet.
    """
    return div(
        cls=("rule", "rule--end"),
        attrs={"id": rule_id(turns)},
        children=[
            span(cls="rule__at", attrs={"title": f"Where turn {turns} would open"}, children="end"),
            a(
                cls="rule__fork",
                attrs={
                    "href": links.to_fork_form(session, turns),
                    "title": "Fork from the end, carrying every turn and asking nothing again",
                },
                children="fork",
            ),
        ],
    )


def transcript_region(links: Links, reader: Reader, showing: Conversation) -> Element:
    """
    The conversation, and whether it is still waiting on the rest of it.

    A whole `Conversation` rather than the four things drawn out of one, because every caller had one
    in hand and was taking them apart the same way: what the region needs is the session, what was
    said, whether it is stalled, the model's window and where its reserve falls, and five arguments
    derived from one value are five chances for a caller to pair a transcript with another session's
    window.

    Markup and nothing else: it carries no `hx-` attribute at all, because it neither asks for
    itself nor decides when to. The page's one connection sends this region whenever the session
    records anything, so what used to be a trigger the region carried, cancelled by its own absence
    once a turn was answered, is now a message that simply stops arriving.

    That is a real simplification rather than a move. A trigger on a region that is itself replaced
    has to be got exactly right (`every` and not `load`, since morphing keeps the element and a
    `load` poll would fire once and wait forever); a region with no trigger has nothing to get
    wrong.

    The rules are drawn from the panels rather than carried beside them, which is what keeps them
    from disagreeing: a turn is a run of consecutive panels sharing a `turn` and a request is a run
    of consecutive panels sharing an `asked`, so the one walk that groups the panels is the one place
    that decides where either begins.

    A turn's own rule takes its tree from the turn's first panel rather than from request 0, which
    holds the same key. The panel has it a request earlier: `turn:{n}:tree:0` is written *before* the
    model is asked, so a turn whose first answer has not landed yet still says what it started on.

    Its moment comes the other way, off request 0 itself, because there is no such thing as a turn
    that has been answered at a moment nothing recorded: a turn whose first answer has not landed
    has no moment yet, and no figures either.
    """
    session = showing.session.id
    said = showing.said
    window = showing.window
    stalled = stalled_by(showing, reader)
    drawn: list[Element] = []
    # What the conversation has cost by the time each rule is drawn. A turn rule carries the total
    # through the turn it opens, exactly as it already carries that turn's own spend: both figures on
    # it summarise what is below rather than what is above, so the pair reads as one statement about
    # the turn. The request rules within it then step from the total the turn began at up to that
    # same figure.
    before: Decimal | None = Decimal(0)
    for turn, panels in groupby(said.panels, key=lambda panel: panel.turn):
        within = tuple(panels)
        asking = said.requests.get(turn, ())
        spent = said.spent.get(turn)
        through = running_to(before, spent)
        # And the same total at each request within the turn, by index rather than by counting the
        # rules that get drawn: request 0 never gets a rule of its own, since the turn's rule already
        # stands at that boundary, and a request that produced no panel gets none either.
        climbing: list[Decimal | None] = []
        for one in asking:
            climbing.append(running_to(climbing[-1] if climbing else before, one.spent))
        drawn.append(
            rule_element(
                links,
                reader,
                session,
                turn,
                asked=0 if asking else None,
                spent=spent,
                when=asking[0].when if asking else None,
                opens=True,
                # Off the turn's first panel, which is where a fact about a turn rather than about a
                # request is carried.
                forget=within[0].forget,
                window=window,
                running=through,
            )
        )
        # Directly under the rule that opens the stretch, so a reader meets the boundary, then what
        # the model is told from here, then the message it is told it about. Absent on every turn
        # that continues a stretch rather than beginning one.
        if turn in said.system_prompts:
            drawn.append(system_prompt_panel(turn, said.system_prompts[turn]))
        at = 0
        for panel in within:
            if panel.asked is not None and panel.asked != at:
                at = panel.asked
                if at < len(asking):
                    drawn.append(
                        rule_element(
                            links,
                            reader,
                            session,
                            turn,
                            asked=at,
                            spent=asking[at].spent,
                            when=asking[at].when,
                            window=window,
                            running=climbing[at],
                        )
                    )
            drawn.append(panel_element(links, session, panel))
        before = through
    # What is happening at the end of the conversation, as one of three things and never two. A
    # refusal outranks the rest because it is the only one nothing can be waiting on; then why nothing
    # is happening, where something should be; and the dots last, which is what a reply being written
    # actually looks like. A call still out is already drawn working on its own panel, so the dots are
    # left off there and the sentence is not: a pass can fall over with a call outstanding.
    waiting = None if stalled is not None else waiting_for(showing)
    # A fork from the end, on a rule of its own after the last turn, where every other fork sits on
    # the rule opening the turn it re-asks. It carries every turn and re-asks none, so what the branch
    # opens on is an empty box. Only on an archived session, because it is how one comes back and
    # the one control such a session has left: on a live one, carrying on is typing into the box, and
    # the composer's own `fork` covers wanting to carry on somewhere else. A turn the pass never
    # finished comes across too and is what the branch resumes, so nothing said in it is lost.
    if session and said.turns > 0 and showing.session.archived is not None:
        drawn.append(end_rule(links, session, said.turns))
    if stalled is not None:
        drawn.append(p(cls="stalled", children=stalled))
    elif waiting is not None:
        drawn.append(attention_element(showing, waiting, reader))
    elif said.awaiting and not out_on_a_call(said):
        drawn.append(waiting_panel())
    return div(
        cls="transcript",
        attrs={"id": TRANSCRIPT_ID},
        children=drawn or p(cls="empty", children="Ask it something."),
    )


def stalled_by(showing: Conversation, reader: Reader) -> str | None:
    """
    Why this session cannot be answered, or nothing at all when it can.

    Two ways to be stopped, and each says the one thing a person can act on. The endpoint is a
    configuration file somebody can put back, so the conversation continues exactly where it left
    off. A refused request cannot be put back at all, because what the provider turned down is the
    recorded history itself, so what it names is `fork`: forking at the refused turn drops that
    turn's own requests and keeps everything under them, which is the shape that fits again.

    The endpoint is asked first because it is the cheaper failure to fix, and because a session whose
    endpoint is gone has no provider to have been refused by.

    It names the endpoint and not the model on purpose. A model missing from the picker does not
    stop a session, since an endpoint routes more ids than it advertises, so saying so here would
    tell somebody to fix something that is not broken.
    """
    # Archived outranks the rest, because it is the one stop somebody chose: an endpoint put back or
    # a fork past a refusal would carry on a session that was deliberately closed.
    if showing.session.archived is not None:
        return (
            f"Archived {stamped(showing.session.archived, reader)}: nothing more is said in it, and its checkout "
            f"and scratch are taken off the disk. Fork it to carry on from where it left off."
        )
    if showing.chosen is None:
        return None
    if not showing.answerable:
        return (
            f"This session was started on endpoint {showing.chosen.endpoint!r}, which the configuration "
            f"no longer declares. Put it back to carry on, or start a new session."
        )
    if showing.refused is None:
        return None
    # The status where there is one, because the number is what somebody looks up, and never in
    # place of the provider's own words: a refusal says which of the several things a 400 can mean
    # this one was, and flattening that to a code would take the answer away.
    said = showing.refused.why
    coded = "" if showing.refused.status is None else f" ({showing.refused.status})"
    return (
        f"This turn stopped{coded} and would stop the same way again, so nothing is waiting on "
        f"it: {said}. Fork at this turn to carry on without the requests it made."
    )


DUE_FIELD: Final = "data-due"
"""
How long until the next pass at this session is due, in seconds, as of this render.

Read by `paintDue`, which is what keeps the figure current: the stream sends this region when the
worker's standing *changes*, and counting down is exactly the interval where it does not. The word is
here rather than at both ends, for `CACHE_ID`'s reason.
"""


ATTENTION_ID: Final = "attention"
"""The one line saying why nothing is happening, where nothing is and something should be."""


@dataclass(frozen=True, slots=True)
class Waiting:
    """
    Why nothing is happening to this session, in the three parts the line is drawn from.

    **Three parts and not one sentence, because one of them is not prose.** `reason` is an exception's
    `repr`: it can be a line or a paragraph, it is full of quotes and brackets and paths, and it is
    the one thing in the box a reader has to actually read. Run together with the text either side of
    it, it is a wall nobody can find the edges of, so the page sets it apart and this is what lets it.

    `then` is what happens next, which is the half that makes this different from a refusal: the
    session is coming back, and saying so is what turns an error into a wait. Absent only where the
    statement is already the whole of it.

    `until` is the moment a provider named for coming back, on the one arm that has one. It is a
    moment rather than words because it is the answer to *when*, and a duration counted down beside
    it says how far off that is; the two together are what a four-day wait needs, since `in 3d 4h`
    alone is a figure nobody can plan around.
    """

    said: str
    reason: str | None = None
    then: str | None = None
    until: datetime | None = None


def waiting_for(showing: Conversation) -> Waiting | None:
    """
    Why nothing is happening to this session, where something should be and nothing is.

    **The dots are the answer for every ordinary state, and this is the answer for the ones they lie
    about.** A reply being written and a session no worker will ever pick up drew the same three dots,
    for as long as the second lasted, which made a broken pass a thing nobody could see. So this
    speaks only where the dots would be wrong, and the caller draws them where it returns nothing.

    **Driven by the recorded failure rather than by the worker's standing**, which is what keeps it
    quiet. A delivery held back is ordinary for a moment on every pass - the queue reserves the row
    before the claim lands - so a line drawn on `Delayed` alone would flash "nothing is answering
    this" through healthy turns. A live failure is what tells a held-back delivery the worker is
    waiting out from the one nobody is coming back to, and `failure_in` is what makes it live.

    **The exception is a session nothing is scheduled for at all.** There is no race that produces one
    with something outstanding: a message and the row that queues it are written in a single commit,
    and a pass asks for the next one from inside itself, so this state is a session that has genuinely
    been dropped and is worth saying so about even with no reason recorded.

    Nothing at all where nothing is outstanding and nothing failed, which is a settled conversation:
    there is nothing to be stuck about, so there is nothing to say.

    It says why the session is stopped and not *how long* until it resumes, which is
    `attention_element`'s half: one of these is a fact that will read the same in an hour and the
    other is a figure that is wrong a second later, so only the second needs the script. A moment a
    provider named is the exception and belongs here, because it is a fact of exactly the first kind.

    **A wait the provider asked for outranks everything below it**, and it is the one arm here that
    is not about something going wrong. Nothing failed, nothing is refused, and the session is coming
    back at a moment somebody else chose; what the page owes a reader is that moment, rather than
    three dots for however many days a subscription's limit takes to reset.
    """
    failed = showing.failed
    if (held := showing.deferred) is not None:
        return Waiting(
            said="The provider will not take another request from this session yet.",
            reason=held.why,
            then="It is scheduled for another pass at",
            until=held.until,
        )
    if not showing.said.awaiting and failed is None:
        return None
    fell = "The last pass at this session failed." if failed is not None else None
    why = None if failed is None else failed.why
    match showing.attention:
        case Claimed():
            return None if fell is None else Waiting(said=fell, reason=why, then="Another pass is answering it now.")
        case Queued():
            return None if fell is None else Waiting(said=fell, reason=why, then="It is queued for another pass.")
        case Delayed():
            # **It does not promise the retry will work**, and that is deliberate rather than hedging.
            # Most of what lands here is fixable and the next pass carries on from where this one
            # stopped; some of it is not, because what a pass replays is *recorded*, so a response the
            # agent will not accept is one every later pass will also not accept. Nothing here can
            # tell those apart, so it says what the mechanism does and names the way out of the second
            # - which is `stalled_by`'s way out, for the same reason: forking drops the turn's own
            # requests and keeps everything under them.
            carrying = "It will be tried again, carrying on from here. Fork at this turn if it keeps failing."
            return None if fell is None else Waiting(said=fell, reason=why, then=carrying)
        case Idle():
            nothing = "Nothing is answering this session and nothing is scheduled to."
            return Waiting(said=nothing) if fell is None else Waiting(said=fell, reason=why, then=nothing)
        case _ as unreachable:
            assert_never(unreachable)


def attention_element(showing: Conversation, waiting: Waiting, reader: Reader) -> Element:
    """
    Why nothing is happening and how long until something does, with the reason set apart.

    **Three children rather than one paragraph**, which is what makes the reason readable: it is an
    exception's `repr`, so it is the one thing in the box a reader has to work through, and it sits in
    a block of its own in the monospace face with the prose above and below it. Centred prose around a
    left-aligned block, because a `repr` that wraps is unreadable centred and a one-line statement is
    not.

    **The server renders the figure and the script keeps it current**, which is `cache_note`'s bargain
    one field along and for the same reason: the stream sends this region when the worker's standing
    changes, and counting down is exactly the interval where it does not. `data-due` is what the script
    measures from, against its own clock from the moment it first saw the element, so no two machines'
    clocks are subtracted. A reader with no script gets the wait as it was when the page was drawn,
    which is a figure that goes stale rather than a sentence that is missing.

    The figure is only on the one arm that has one, and the sentence before it reads correctly alone.

    **A moment goes in beside it where the wait is one somebody named**, and the pair is the point: a
    provider deferring a session until Tuesday makes `in 3d 4h` a figure nobody can plan around,
    where the moment is what a reader actually wants and the countdown is what says how far off it
    is. It is drawn against the reader's own clock exactly as a rule's is.
    """
    due = showing.attention.until if isinstance(showing.attention, Delayed) else None
    then: list[Element | str] = [] if waiting.then is None else [waiting.then]
    if waiting.until is not None and waiting.then is not None:
        then.extend(
            (
                " ",
                when_element(
                    waiting.until,
                    cls="attention__when",
                    title=stamped(waiting.until, reader),
                    said=dated(waiting.until, reader),
                ),
                ".",
            )
        )
    if due is not None and waiting.then is not None:
        then.extend((" Due in ", span(cls="attention__due", children=elapsed(due)), "."))
    return div(
        # A wait somebody named is not a fault, and the stylesheet draws it in the ordinary ink
        # rather than the red one: what the red means on this page is that something went wrong.
        cls=("attention", "attention--waiting" if waiting.until is not None else None),
        attrs={"id": ATTENTION_ID, **({DUE_FIELD: f"{due.total_seconds():.0f}"} if due is not None else {})},
        children=[
            p(cls="attention__said", children=waiting.said),
            *(
                ()
                if waiting.reason is None
                else (pre(cls="attention__reason", children=code(children=waiting.reason)),)
            ),
            *(() if not then else (p(cls="attention__then", children=then),)),
        ],
    )
