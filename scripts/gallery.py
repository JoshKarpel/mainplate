# Every page this console renders, written to a directory as files a browser can open.
#
# The point is to look at the stylesheet, which is a deliverable no string assertion can check. A
# page here is produced by the *same* function that serves it, from a checkpoint shaped exactly as
# a real session's is, so what a screenshot shows is what a reader gets rather than a second
# rendering that resembles it.
#
# No server, no database, no provider and no `config.yaml`. Pages are pure functions of
# already-answered questions, so the whole of what this needs is to answer them with fixtures. The
# assets are copied beside the pages, which is why a static server rooted here renders identically
# to the real one: `/assets/mainplate.css` resolves the same way either way.

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ThinkingPart
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.usage import RequestUsage

from mainplate.agent import Choice
from mainplate.agent import Listed
from mainplate.catalogue import Catalogue
from mainplate.catalogue import Offering
from mainplate.console import LINKS
from mainplate.conversation import messages_key
from mainplate.conversation import model_key
from mainplate.conversation import prompt_key
from mainplate.conversation import steer_key
from mainplate.conversation import took_key
from mainplate.conversation import tool_key
from mainplate.conversation import transcript
from mainplate.conversation import tree_key
from mainplate.durability import TOOK
from mainplate.durability import ModelResponseTypeAdapter
from mainplate.forge import Reachable
from mainplate.forge import Repository
from mainplate.pages import fork_page
from mainplate.pages import session_page
from mainplate.pages import start_page
from mainplate.reference import Cost
from mainplate.reference import Facts
from mainplate.reference import Reference
from mainplate.sandbox import Filesystem
from mainplate.sandbox import Isolation
from mainplate.service import Conversation
from mainplate.sessions import Origin
from mainplate.sessions import Session

ASSETS = Path(__file__).resolve().parent.parent / "src" / "mainplate" / "assets"

WHEN = datetime(2031, 3, 14, 15, 9, 26, tzinfo=UTC)

CATALOGUE = Catalogue(
    offered={
        "llm-anthropic": Offering(
            endpoint="llm-anthropic",
            format="anthropic",
            url="https://llm.int.exe.xyz",
            models=(
                Listed(id="anthropic/claude-sonnet-4-6", label="Claude Sonnet 4.6", provider="anthropic"),
                Listed(id="anthropic/claude-opus-4-8", label="Claude Opus 4.8", provider="anthropic"),
                Listed(
                    id="fireworks/kimi-k2",
                    label="Kimi K2",
                    provider="fireworks",
                    upstream="accounts/fireworks/models/kimi-k2",
                ),
            ),
        ),
        # Named with the `/v1` the OpenAI SDK wants, because the two rows differing only by a wire
        # and a suffix is exactly the case the endpoint card exists to make legible.
        "llm-openai": Offering(
            endpoint="llm-openai",
            format="openai",
            url="https://llm.int.exe.xyz/v1",
            models=(Listed(id="openai/gpt-5.5", label="openai/gpt-5.5", provider="openai"),),
        ),
    },
    default=Choice(
        endpoint="llm-anthropic",
        model="anthropic/claude-sonnet-4-6",
        repository="exe-github:mainplate",
        # Settled here for the reason `Service.start` settles it: a fixture naming a repository and
        # recording that it reaches no files would draw a page no real session can produce.
        isolation=Isolation(filesystem=Filesystem.WORKTREE),
        thinking="high",
    ),
)

# A reference covering three of the four models on offer, so the gallery shows a full card, a card
# whose record carries less, and the marker on the one the database has never heard of. Written as
# the indexes rather than parsed from a document, because what these pages are for is looking at a
# rendering: a fixture that went through `parse_models_dev` would test the parser, which
# `test_reference.py` does, and would make this file carry a copy of somebody else's schema.
REFERENCE = Reference(
    qualified={
        "anthropic/claude-sonnet-4-6": Facts(
            cost=Cost(input=3, output=15, cache_read=0.3, cache_write=3.75),
            context=1_000_000,
            output=128_000,
            released=date(2030, 11, 19),
            about="Balanced everyday model: fast enough to iterate with, careful enough to trust.",
            traits=(("thinking", True), ("tools", True), ("vision", True), ("pdf", True), ("structured", True)),
        ),
        "anthropic/claude-opus-4-8": Facts(
            cost=Cost(input=15, output=75, cache_read=1.5, cache_write=18.75),
            context=1_000_000,
            output=128_000,
            released=date(2030, 8, 5),
            traits=(("thinking", True), ("tools", True), ("vision", True), ("structured", True)),
        ),
        "openai/gpt-5.5": Facts(
            cost=Cost(input=1.25, output=10),
            context=400_000,
            output=128_000,
            released=date(2030, 9, 30),
            traits=(("tools", True), ("vision", True)),
        ),
    },
    upstream={},
)

WORKING_IN = "exe-github:mainplate"

# A repository no forge reaches any more, so one row is drawn as the recorded id. That is the case
# a screenshot is for: the name and the id are different lengths and different shapes, and whether
# the second one still reads as a repository is not a thing a markup assertion can answer.
DETACHED = "exe-github:archived"

PARENT = Session(id="aa" * 16, created_at=WHEN, title="Why does the poll stop after one answer", repository=WORKING_IN)

# A branch, and a branch of that branch, so the sidebar's nesting is drawn at more than one depth
# and the turn each left at is visible on the row. A fork inherits its parent's repository, so the
# three of them read the same, and the two below are the other states a row can be in: one working
# in nothing, one working in something nothing reaches.
LISTED = (
    PARENT,
    Session(
        id="bb" * 16,
        created_at=WHEN + timedelta(minutes=4),
        title=PARENT.title,
        forked=Origin(session=PARENT.id, turn=1),
        repository=WORKING_IN,
    ),
    Session(
        id="cc" * 16,
        created_at=WHEN + timedelta(minutes=9),
        title=PARENT.title,
        # An aside rather than a plain fork, so the sidebar's two marks are both on the page and a
        # styling change can be seen against the pair rather than against one of them.
        forked=Origin(session="bb" * 16, turn=2, aside=True),
        repository=WORKING_IN,
    ),
    Session(id="dd" * 16, created_at=WHEN, title="Add a thinking control to the picker"),
    Session(id="ee" * 16, created_at=WHEN - timedelta(hours=3), title="Port the old notes", repository=DETACHED),
)

# One turn per kind of thing a panel can hold, so a styling change can be seen against all of them
# at once rather than against whichever session happened to be open. The code block is deliberately
# wider than the column, because the one thing a transcript must never do is scroll the page
# sideways.
LONG_LINE = "    return Response.from_content(status, html_content(render(transcript_region(links, session, said))))"


def spending(asked: int, answered: int, cached: int = 0, cost: str = "0") -> RequestUsage:
    """
    What one fixture response cost, as the wire would have reported it and the step recorded it.

    Fixtures carry this because a rule with no numbers on it is a rule nobody can look at, and the
    shots exist to be looked at. The counts nest the way a real one does - `input_tokens` includes
    the cached reads - so the fixtures exercise the same arithmetic a live turn does rather than a
    tidier version of it that would hide a sign error.

    The cost is on the response because that is where this console now records it, before the
    response is written rather than after. A fixture that left it off would draw the one state the
    live path no longer produces: a settled turn with counts and no price.
    """
    return RequestUsage(
        input_tokens=asked,
        output_tokens=answered,
        cache_read_tokens=cached,
        cost=Decimal(cost),
    )


def timing(seconds: float) -> dict[str, object]:
    """
    How long one fixture response took to come back, in the slot a real pass stamps.

    On the response rather than in a key beside it, because that is where `Stepping.stamp` puts it:
    a fixture recording it anywhere else would draw a figure the live path cannot produce.
    """
    return {TOOK: seconds}


# How long each fixture call ran, by the call id, so a folded turn carries the figure a real one
# does. Two scales rather than one, because `elapsed` formats them differently and a shot is where
# you find out whether both read well. A call still out is absent from here on purpose: its duration
# is written after its return, so a call with a time and no result is a state nothing can record.
TIMINGS = {"call-1": 0.184, "call-7": 12.65}

CONVERSATION: list[ModelMessage] = [
    ModelRequest(parts=[UserPromptPart(content="Why does the poll stop after one answer?")]),
    ModelResponse(
        parts=[
            ThinkingPart(
                content=(
                    "The trigger is `load`, which fires once per element load. Morphing keeps the "
                    "element, so it never loads again. It needs to be an interval."
                )
            ),
            TextPart(content="Let me look at how the region is swapped."),
            ToolCallPart(
                tool_name="read_file",
                args={"path": "src/mainplate/pages.py", "pattern": "hx-trigger"},
                tool_call_id="call-1",
            ),
        ],
        usage=spending(asked=4_182, answered=196, cost="0.0156"),
        metadata=timing(3.4),
    ),
    ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="read_file",
                content='WAITING = "every 1s"\nSWAP = "outerMorph"',
                tool_call_id="call-1",
            )
        ]
    ),
    ModelResponse(
        parts=[
            TextPart(
                content=(
                    "Because the swap is `outerMorph`, the element is **kept** rather than replaced.\n\n"
                    "A `load` trigger fires once per element *load*, so it only ever repeated because "
                    "each answer replaced the region. Here is the handler it lives in:\n\n"
                    "```python\n"
                    '@get(t"/fragments/sessions/{session_id}", session_id)\n'
                    "async def session_fragment(service: Service, session: str) -> Response:\n"
                    "    found = await service.read(session)\n"
                    "    if found is None:\n"
                    '        return page_response(404, refusal_page(LINKS, 404, f"no session {session}"))\n'
                    f"{LONG_LINE}\n"
                    "```\n\n"
                    "So the fix is an interval, which belongs to the element rather than to its arrival:\n\n"
                    "| trigger | fires | survives a morph |\n"
                    "| --- | --- | --- |\n"
                    "| `load` | once per load | no |\n"
                    "| `every 1s` | on a timer | yes |\n"
                )
            )
        ],
        usage=spending(asked=4_610, answered=832, cached=3_968, cost="0.0219"),
        metadata=timing(9.7),
    ),
]

TOOL_IN_FLIGHT: list[ModelMessage] = [
    ModelRequest(parts=[UserPromptPart(content="Now check the stylesheet handles a long line.")]),
    ModelResponse(
        parts=[
            TextPart(content="Checking."),
            ToolCallPart(tool_name="grep", args={"pattern": "overflow-x"}, tool_call_id="call-2"),
        ],
        usage=spending(asked=5_604, answered=88, cached=4_608, cost="0.0041"),
        metadata=timing(1.2),
    ),
]

# The one response a turn has produced so far, as the capability recorded it partway through. Two
# calls in one response because that is the case worth looking at: they run at once, so one comes
# back while the other is still out, and the panel has to read as both at the same time.
PARTWAY = ModelResponse(
    parts=[
        ThinkingPart(content="Two files to look at. I can read them at the same time."),
        ToolCallPart(tool_name="read", args={"path": "src/mainplate/streaming.py"}, tool_call_id="call-7"),
        ToolCallPart(tool_name="read", args={"path": "src/mainplate/pages.py", "depth": 2}, tool_call_id="call-8"),
    ],
    # A turn in flight has a cost too, which is the point of pricing a response before the step
    # records it rather than after the run ends. Left off, this fixture would draw the state the
    # console used to have and no longer does.
    usage=spending(asked=6_120, answered=142, cached=5_120, cost="0.0067"),
    metadata=timing(2.8),
)


def opening(messages: Sequence[ModelMessage]) -> str:
    """
    What the person said to start a turn, taken from the turn's own messages.

    Read back out rather than passed in beside them, so a fixture cannot record a prompt that the
    messages beneath it disagree with. Loud when the shape is wrong, because a fixture that is not
    shaped like a real turn is exactly what these pages must not be built from.
    """
    first = messages[0]
    if not isinstance(first, ModelRequest):
        raise TypeError(f"a turn opens with a request, not {type(first).__name__}")
    asked = first.parts[0]
    if not isinstance(asked, UserPromptPart) or not isinstance(asked.content, str):
        raise TypeError(f"a turn opens with what somebody typed, not {asked!r}")
    return asked.content


def recorded(*turns: Sequence[ModelMessage]) -> dict[str, object]:
    """
    A checkpoint holding these turns, in exactly the shape a pass would have written.

    Each response is written twice over, as part of the turn's `messages` and as the step that
    recorded it, because a pass writes both: the step is what the rule at that request's boundary
    opens, so a fixture with only the messages renders a fold that answers 404 for every request.

    A call's duration is a key of its own, written for the calls `TIMINGS` names, because that is
    what a pass writes: a return and, beside it, how long the call took.
    """
    written: dict[str, object] = {}
    for turn, messages in enumerate(turns):
        written[prompt_key(turn)] = opening(messages)
        written[messages_key(turn)] = ModelMessagesTypeAdapter.dump_python(list(messages), mode="json")
        for at, response in enumerate(message for message in messages if isinstance(message, ModelResponse)):
            written[model_key(turn, at)] = ModelResponseTypeAdapter.dump_python(response, mode="json")
            for part in response.parts:
                if isinstance(part, ToolCallPart) and part.tool_call_id in TIMINGS:
                    written[took_key(turn, part.tool_call_id)] = TIMINGS[part.tool_call_id]
    return written


# A stand-in repository and one session's worktree of it, so the pages show what a console with
# snapshots on looks like: the tree each turn started on, beside the fork link that would put it
# back, and the repository the session is working in.
REPOSITORY = "JoshKarpel/mainplate"
WORKSPACE = Path("/home/you/.local/share/mainplate/workspaces/worktrees")

# What a forge reaches, so the start page's picker has something in it. Two attachments of one
# repository, because that is the case the labels have to disambiguate and a screenshot is where
# you find out whether they read well.
REACHABLE = Reachable(
    repositories=(
        Repository(forge="exe-github", key="mainplate", name="JoshKarpel/mainplate", url="https://x.invalid/a.git"),
        Repository(forge="exe-github", key="without", name="JoshKarpel/without", url="https://x.invalid/b.git"),
        Repository(forge="exe-github", key="dotfiles-rw", name="JoshKarpel/dotfiles", url="https://x.invalid/c.git"),
        Repository(forge="exe-github", key="dotfiles-ro", name="JoshKarpel/dotfiles", url="https://x.invalid/d.git"),
    )
)

# A tree per model request rather than per turn, which is what a console with a worktree writes: a
# snapshot is taken before every request, so the second request of a turn stands on whatever the
# first one's tool calls left behind. The rules within a turn are the only place that shows, so a
# fixture with one hash per turn would draw the design as it was before there were any.
TREES = (
    ("9e75602b2554519c9f620dfdb2010586fde7e076", "c41d7a08b8e6cf2f4a90b3d5eec1120b7f5a3e91"),
    ("3de66468884176acb6dc1a522aa8cfa5ace6f0e0",),
)


def snapshotted(written: dict[str, object]) -> dict[str, object]:
    """The same checkpoint with a tree recorded before each of a turn's model requests."""
    return {
        **written,
        **{tree_key(turn, at): tree for turn, taken in enumerate(TREES) for at, tree in enumerate(taken)},
    }


def showing(
    session: Session, written: dict[str, object], *, answerable: bool = True, working: bool = True
) -> Conversation:
    """One session as a page sees it. `working` is whether it picked a repository at all."""
    chosen = CATALOGUE.default if working else replace(CATALOGUE.default, repository=None)
    return Conversation(
        session=session,
        said=transcript(snapshotted(written) if working else written),
        chosen=chosen,
        answerable=answerable,
        repository=REPOSITORY if working else None,
        worktree=WORKSPACE / session.id if working else None,
    )


def pages() -> dict[str, str]:
    """Every page worth looking at, by the file it is written to."""
    settled = recorded(CONVERSATION, TOOL_IN_FLIGHT)
    waiting = dict(settled)
    waiting[prompt_key(2)] = "And what about a turn still being answered?"

    # A turn part way through, read from the steps behind it rather than from messages it has not
    # written yet. The state exists only while a pass is actually running, so a fixture is the one
    # way to look at it - and looking at it is the point, since what it has to prove is that a
    # half-drawn turn reads as a turn in progress rather than as a broken one.
    answering = dict(waiting)
    answering[model_key(2, 0)] = ModelResponseTypeAdapter.dump_python(PARTWAY, mode="json")
    answering[tool_key(2, "call-7")] = "# The one connection a page holds open, and what goes down it."
    answering[took_key(2, "call-7")] = TIMINGS["call-7"]
    # A steer sent into that turn and not yet put to any model, which is the state Send now reaches
    # every time somebody types while a reply is coming. It is drawn from `turn:{n}:steer:{k}` rather
    # than from messages that do not exist yet, so a screenshot is where you find out whether a
    # message that has been sent and not yet heard reads as one.
    answering[steer_key(2, 0)] = "and while you are there, check the phone width"

    stalled = showing(LISTED[3], recorded(CONVERSATION), answerable=False)

    return {
        "start.html": start_page(LINKS, LISTED, CATALOGUE, REACHABLE, REFERENCE),
        # The same page with nothing configured to look models up in, which is the default and the
        # one a screenshot has to prove still reads as a finished page rather than as a broken one.
        "start-unreferenced.html": start_page(LINKS, LISTED, CATALOGUE, REACHABLE, None),
        "session.html": session_page(LINKS, LISTED, showing(PARENT, settled), REACHABLE),
        "waiting.html": session_page(LINKS, LISTED, showing(PARENT, waiting), REACHABLE),
        "answering.html": session_page(LINKS, LISTED, showing(PARENT, answering), REACHABLE),
        "stalled.html": session_page(LINKS, LISTED, stalled, REACHABLE),
        # Forking at turn 1, so the page has something to show as carried over and something to
        # leave behind: the fork keeps turn 0 and waits to be told turn 1 differently. This session
        # is already in a repository, so no repository control appears - it inherits that one.
        "forking.html": fork_page(LINKS, LISTED, showing(PARENT, settled), 1, CATALOGUE, REACHABLE, REFERENCE),
        # And a fork of a session in *no* repository, which is the one that may pick one up: the
        # ordinary shape of having thought something through and then going to work on it.
        "forking-attach.html": fork_page(
            LINKS, LISTED, showing(PARENT, settled, working=False), 0, CATALOGUE, REACHABLE, REFERENCE
        ),
    }


def write(into: Path) -> tuple[str, ...]:
    """
    Every page, into a directory holding those pages and nothing else, named back to the caller.

    A page renamed or dropped is *removed* rather than left behind, because the alternative is a
    directory that accumulates: a stale page sits beside the current ones, a screenshot run can be
    pointed at one the console no longer renders, and what a reader is looking at is a page from
    some earlier version of this file.
    """
    into.mkdir(parents=True, exist_ok=True)
    served = into / "assets"
    if served.exists():
        shutil.rmtree(served)
    shutil.copytree(ASSETS, served)
    written = pages()
    for stale in set(into.glob("*.html")) - {into / name for name in written}:
        stale.unlink()
    for name, markup in written.items():
        (into / name).write_text(markup)
    return tuple(sorted(written))


if __name__ == "__main__":
    print(json.dumps(write(Path(sys.argv[1] if len(sys.argv) > 1 else "build/gallery")), indent=2))
