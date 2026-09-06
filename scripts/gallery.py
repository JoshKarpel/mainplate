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
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import SystemPromptPart
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ThinkingPart
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.usage import RequestUsage
from without_durability.interfaces import inbox_key

from mainplate import records
from mainplate.agent import Choice
from mainplate.agent import Listed
from mainplate.catalogue import Catalogue
from mainplate.catalogue import Offering
from mainplate.console import LINKS
from mainplate.conversation import Result
from mainplate.conversation import heard_key
from mainplate.conversation import instructions_key
from mainplate.conversation import messages_key
from mainplate.conversation import model_key
from mainplate.conversation import opened_key
from mainplate.conversation import recorded_command
from mainplate.conversation import recorded_instructions
from mainplate.conversation import recorded_messages
from mainplate.conversation import recorded_prompt
from mainplate.conversation import recorded_result
from mainplate.conversation import recorded_steer
from mainplate.conversation import result_key
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
from mainplate.reference import facts_of
from mainplate.sandbox import Filesystem
from mainplate.sandbox import Isolation
from mainplate.service import Conversation
from mainplate.sessions import Origin
from mainplate.sessions import Session
from mainplate.snapshots import branch_named
from mainplate.tools import ASKING

ASSETS = Path(__file__).resolve().parent.parent / "src" / "mainplate" / "assets"

WHEN = datetime(2031, 3, 14, 15, 9, 26, tzinfo=UTC)

# The session every page below is about, named here rather than on `PARENT` because the default
# choice needs it too: a session's branch is named after its id, so the two would otherwise be one
# literal written twice and a page saying it is on a branch belonging to some other session.
PARENT_ID = "aa" * 16

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
        # Both settled here for the reason `Service.start` settles them: a fixture naming a
        # repository while recording that it reaches no files, or that it is on no branch, would draw
        # a page no real session can produce. Every session working in a repository is on a branch
        # named after it, so the line under the message box says one and a screenshot has to show it.
        isolation=Isolation(filesystem=Filesystem.WORKTREE),
        branch=branch_named(PARENT_ID),
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
            # The window every session in this gallery is answered on, and the smaller of the two
            # deliberately: what the gauge along each rule has to show is a conversation getting
            # somewhere near the end of one, and a fixture on a million-token window would draw every
            # rule as an empty line. The card beside it still renders `1M` for the model below.
            context=200_000,
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

PARENT = Session(id=PARENT_ID, created_at=WHEN, title="Why does the poll stop after one answer", repository=WORKING_IN)

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

# What a read comes back as, in the shape `anchored` renders: a name, the gutter, and the line. Here
# rather than assembled from the tool, so a fixture stays a value and needs no workspace to build,
# and written out with the bar in it because that column is what the monospace row pitch is for. A
# read is the most common thing a panel in this console ever shows and nothing else in this gallery
# had one, so a pitch that broke the gutter into dashes broke it where nobody was looking.
READ = 'qwrt│WAITING = "every 1s"\n----│\nmkpv│SWAP = "outerMorph"'


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

    The counts climb across the conversation the way a real one's do - each request carries
    everything said before it, and almost all of that comes back out of the cache - because that is
    the whole of what the gauge on each rule draws. Fixtures that all sat at four thousand tokens
    would draw four identical empty lines and say nothing about the one figure they are for. The
    prices are what this reference's own rates come to on these counts, so a screenshot shows a
    session whose money and tokens agree with each other.
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

# What the requests in this fixture carried, so the panel that draws a session's system prompt has
# something to draw. Three scopes in the order `instructing` composes them - the console's standing
# directions, the working note the session's own isolation adds, and the repository's own
# `AGENTS.md` last - because what that panel is for is showing a reader which of those they are
# looking at.
INSTRUCTIONS = """\
You are a helpful assistant, working with a software engineer. Be concise and direct.

You are working in a git worktree, which is called `worktree`. The file tools take paths relative to
it and reach nothing outside it. Changes you make there are snapshotted automatically; you never
need to commit, and you should not run git commands to record your work. You also have a scratch
directory called `scratch`, outside the worktree and outside every snapshot. Reach it by passing
`root: "scratch"` to `read`, `edit` or `create`; in a command it is `$MAINPLATE_SCRATCH`, and the
worktree is `$MAINPLATE_WORKTREE`.

Commands you run cannot reach the network: no fetching, no installing, no cloning. Something that
needs one fails rather than hanging.

`AGENTS.md`, this repository's own guidance:

# Polling

The console holds one connection per page and the transcript carries no `hx-` attribute of its
own. A region that asks for itself has to get its own trigger right; a region with no trigger has
nothing to get wrong.

Run `just test` before saying anything is done.
"""

HANDED_OVER = (
    "## Objective\n"
    "\n"
    "Find out why the transcript stops updating after the first answer, and fix it.\n"
    "\n"
    "## What is settled\n"
    "\n"
    "The connection is held by the page rather than by the transcript, so a swap of the transcript"
    " no longer tears it down. `streaming.py` sends a whole current render on every change rather"
    " than a delta, which is why a reconnect needs no cursor.\n"
    "\n"
    "## Ruled out\n"
    "\n"
    "An `hx-trigger` on the transcript itself. It has to be `every` rather than `load`, because"
    " morphing keeps the element and a `load` poll fires once and then waits for ever on an answer"
    " that already arrived, invisibly to any markup assertion.\n"
    "\n"
    "## Next\n"
    "\n"
    "Read `src/mainplate/streaming.py`, then `transcript_region` in `pages.py`. The change token is"
    " `Service.token`, which counts recorded rows.\n"
)
"""
A handoff as one actually reads: what the task is, what is settled, what was ruled out, what is next.

Written out rather than generated, because what a screenshot has to show is a document long enough to
need the panel it is drawn in and structured enough that its Markdown is doing something.

Joined from single logical lines rather than written as a wrapped triple-quoted string, and that is
about the rendering rather than about the source. A message's newlines are the author's, so
`as_message` draws every one of them as a break; a fixture wrapped to fit this file would put a break
mid-sentence in the screenshot and misrepresent what a model's own paragraph looks like.
"""

CONVERSATION: list[ModelMessage] = [
    ModelRequest(
        parts=[UserPromptPart(content="Why does the poll stop after one answer?")],
        instructions=INSTRUCTIONS,
    ),
    ModelResponse(
        parts=[
            ThinkingPart(
                content=(
                    "The trigger is `load`, which fires once per element load. Morphing keeps the "
                    "element, so it never loads again. It needs to be an interval:\n\n"
                    # A fenced block inside reasoning, which is what says that the italic a
                    # reasoning panel is set in stops at code. A model reasons *about* code, so it
                    # quotes some, and a quotation slanted away from the thing it quotes is a
                    # rendering that has changed what it is showing.
                    "```html\n"
                    '<div hx-get="/fragments/sessions/{id}" hx-trigger="every 1s"></div>\n'
                    "```"
                )
            ),
            TextPart(content="Let me look at how the region is swapped."),
            ToolCallPart(
                tool_name="read_file",
                args={"path": "src/mainplate/pages.py", "pattern": "hx-trigger"},
                tool_call_id="call-1",
            ),
        ],
        usage=spending(asked=38_400, answered=196, cost="0.1181"),
        metadata=timing(3.4),
    ),
    ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="read_file",
                content=READ,
                tool_call_id="call-1",
            ),
            # What the console hands over when a turn reaches into a part of the repository that
            # carries its own guidance. A `SystemPromptPart` rather than a user one, because nobody
            # typed it, which is also how the transcript tells the two apart.
            SystemPromptPart(
                content=(
                    "`src/mainplate/AGENTS.md`, guidance for this part of the repository:\n\n"
                    "# Pages\n\n"
                    "Pages are `without-html` node trees, pure functions of already-answered\n"
                    "questions. A page and the fragment inside it are the same function called at\n"
                    "two depths, which is what stops the two renderings from disagreeing.\n"
                )
            ),
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
                    "| `every 1s` | on a timer | yes |\n\n"
                    "Which puts the deciding where it belongs:\n\n"
                    # An unlabelled fence, because a diagram is not a language Pygments knows and a
                    # guess at one would colour it by a grammar it is not written in. It is here so
                    # that the shots show the other half of what the row pitch is for: a diagram
                    # somebody drew, where a pitch too tall for the font draws every join apart.
                    "```\n"
                    "┌──────────┐  records   ┌──────────┐\n"
                    "│  worker  │ ─────────▶ │  store   │\n"
                    "└──────────┘            └────┬─────┘\n"
                    "                             │ token\n"
                    "                        ┌────▼─────┐\n"
                    "                        │   page   │\n"
                    "                        └──────────┘\n"
                    "\n"
                    "  ✓ survives a morph        ✗ fires once\n"
                    "```\n"
                )
            )
        ],
        usage=spending(asked=44_800, answered=832, cached=38_400, cost="0.0432"),
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
        usage=spending(asked=96_300, answered=88, cached=44_600, cost="0.1698"),
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
    usage=spending(asked=151_900, answered=142, cached=96_200, cost="0.1981"),
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

    A call's own record carries what it returned and how long it took, written for the calls
    `TIMINGS` names, because that is what a pass writes. What it returned is read back out of the
    turn's messages rather than restated, for the reason `opening` reads the prompt back out: a
    fixture that said one thing in the step and another in the messages would draw a turn this
    console cannot produce.
    """
    # What the stretch of context beginning at turn 0 is answered under, which is what the system
    # prompt panel under that turn's rule draws. Written before the turn's first request by a pass,
    # so a fixture that left it out would render a session whose first turn landed with nothing
    # saying what it was told. A fixture with a forget in it records another beside the boundary,
    # because that is where the next stretch begins.
    written: dict[str, object] = {instructions_key(0): recorded_instructions(INSTRUCTIONS)}
    for turn, messages in enumerate(turns):
        came_back = returns(messages)
        # The two records a turn opens with: the entry the message arrived as, and the cursor saying
        # this turn took it. `seed.py` and this script are the two writers that are not `Service`, so
        # what a pass would write is written by hand, both halves or neither.
        written[inbox_key(turn)] = recorded_prompt(opening(messages))
        written[opened_key(turn)] = inbox_key(turn)
        written[heard_key(turn, 0)] = inbox_key(turn)
        written[messages_key(turn)] = recorded_messages(messages)
        for at, response in enumerate(message for message in messages if isinstance(message, ModelResponse)):
            written[model_key(turn, at)] = records.Response(
                response=ModelResponseTypeAdapter.dump_python(response, mode="json")
            ).recorded()
            for part in response.parts:
                if isinstance(part, ToolCallPart) and part.tool_call_id in TIMINGS:
                    written[tool_key(turn, part.tool_call_id)] = records.Returned(
                        returned=came_back.get(part.tool_call_id),
                        took=timedelta(seconds=TIMINGS[part.tool_call_id]),
                    ).recorded()
    return written


def returns(messages: Sequence[ModelMessage]) -> dict[str, object]:
    """What each call in a turn came back with, by the id that names which call it answers."""
    return {
        part.tool_call_id: part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }


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
        **{
            tree_key(turn, at): records.Tree(tree=tree).recorded()
            for turn, taken in enumerate(TREES)
            for at, tree in enumerate(taken)
        },
    }


def showing(
    session: Session,
    written: dict[str, object],
    *,
    answerable: bool = True,
    working: bool = True,
    started: bool = True,
    refused: records.Refused | None = None,
) -> Conversation:
    """
    One session as a page sees it.

    `working` is whether it picked a repository at all. `started` is whether a pass has been there
    yet: a session whose message is still queued has no turn, so it has no tree recorded before a
    request nobody has made, and a fixture that gave it one would be a checkpoint no pass could
    write.
    """
    chosen = CATALOGUE.default if working else replace(CATALOGUE.default, repository=None)
    return Conversation(
        session=session,
        said=transcript(snapshotted(written) if working and started else written),
        chosen=chosen,
        answerable=answerable,
        refused=refused,
        repository=REPOSITORY if working else None,
        worktree=WORKSPACE / session.id if working else None,
        # Which follows the repository, because a command runs in a session's worktree: it is what
        # puts `Run` among the sending menu's answers, and so what makes `/run` and `!` reach a mode
        # at all.
        runnable=working,
        # Looked up here rather than written down, exactly as `Service.read` does it, so the gauge
        # on every rule is drawn against the same number the model's own card shows.
        window=facts.context if (facts := facts_of(CATALOGUE, REFERENCE, chosen)) is not None else None,
    )


def pages() -> dict[str, str]:
    """Every page worth looking at, by the file it is written to."""
    settled = recorded(CONVERSATION, TOOL_IN_FLIGHT)
    # Commands the person ran themselves, which no model was told about and which the store holds
    # beside what was said. All three states, because they are drawn differently and the differences
    # are exactly what a screenshot is for: an exit of zero, an exit that is not a failure - `git
    # diff --quiet` exits 1 to say there *are* changes - and one still running.
    settled[inbox_key(2)] = recorded_command("git status --short")
    settled[result_key(inbox_key(2))] = recorded_result(
        Result(status=0, output=" M src/mainplate/pages.py\n", took=timedelta(seconds=0.11))
    )
    settled[inbox_key(3)] = recorded_command("git diff --quiet")
    settled[result_key(inbox_key(3))] = recorded_result(Result(status=1, output="", took=timedelta(seconds=0.08)))
    settled[inbox_key(4)] = recorded_command("just test")
    # And a turn that opens on a clean history, so the boundary is drawn somewhere it can be looked
    # at. Turn 1 rather than a turn of its own, because what a screenshot has to show is the rule
    # standing *between* two turns with the first still on the page above it: a boundary at the top
    # of a conversation would draw the same markup and prove nothing about what it says.
    settled[inbox_key(1)] = recorded_prompt(opening(TOOL_IN_FLIGHT), forget=True)
    # And what the stretch it opens is answered under, composed again because a forget has thrown the
    # cached prefix away and composing exactly there is free. The same words here, since nothing under
    # them moved between the two turns; what the second panel shows is that a boundary gets one.
    settled[instructions_key(1)] = recorded_instructions(INSTRUCTIONS)
    waiting = dict(settled)
    waiting[inbox_key(5)] = recorded_prompt("And what about a turn still being answered?")

    # A turn part way through, read from the steps behind it rather than from messages it has not
    # written yet. The state exists only while a pass is actually running, so a fixture is the one
    # way to look at it - and looking at it is the point, since what it has to prove is that a
    # half-drawn turn reads as a turn in progress rather than as a broken one.
    answering = dict(waiting)
    answering[opened_key(2)] = inbox_key(5)
    answering[heard_key(2, 0)] = inbox_key(5)
    answering[model_key(2, 0)] = records.Response(
        response=ModelResponseTypeAdapter.dump_python(PARTWAY, mode="json")
    ).recorded()
    answering[tool_key(2, "call-7")] = records.Returned(
        returned="# The one connection a page holds open, and what goes down it.",
        took=timedelta(seconds=TIMINGS["call-7"]),
    ).recorded()
    # A steer sent into that turn and not yet put to any model, which is the state Send now reaches
    # every time somebody types while a reply is coming. It is drawn from the entry rather than from
    # messages that do not exist yet, so a screenshot is where you find out whether a message that has
    # been sent and not yet heard reads as one.
    answering[inbox_key(6)] = recorded_steer("and while you are there, check the phone width")

    # A handoff, which is two panels of a kind nobody typed: the console's own ask, and the document
    # that came back and starts the model's history again. Turn 1's opener is replaced rather than a
    # turn being added, so the ask sits under a turn that actually worked - which is what a handoff
    # turn looks like, and what a screenshot has to show is that the two panels read as the console's
    # rather than as somebody's. The document is left unread, since a message waiting for its turn is
    # both the honest state a moment after a handoff and the one that draws the boundary on the page.
    handed = dict(settled)
    handed[inbox_key(1)] = records.Handoff(said=ASKING).recorded()
    handed[inbox_key(7)] = records.Handoff(said=HANDED_OVER, forget=True).recorded()

    stalled = showing(LISTED[3], recorded(CONVERSATION), answerable=False)
    # The other way to be stopped, which points somewhere different because nothing can be put back:
    # what the provider turned down is the recorded history itself, so the sentence names the fork.
    turned_down = showing(
        LISTED[3],
        recorded(CONVERSATION),
        refused=records.Refused(
            why="prompt is too long: 214331 tokens > 200000 maximum",
            status=400,
        ),
    )
    # The state every session opens in, and stays in for as long as the clone and the worktree take:
    # the message is there to be drawn and what the session is answered under is not, because
    # composing that reads a repository the pass is the one to fetch. The system prompt panel is
    # drawn with nothing in it rather than left out, and a screenshot is where you find out whether
    # that reads as something on its way or as something broken.
    queued = showing(
        LISTED[1], {inbox_key(0): recorded_prompt("Why does the poll stop after one answer?")}, started=False
    )

    return {
        "start.html": start_page(LINKS, LISTED, CATALOGUE, REACHABLE, REFERENCE),
        # The same page with nothing configured to look models up in, which is the default and the
        # one a screenshot has to prove still reads as a finished page rather than as a broken one.
        "start-unreferenced.html": start_page(LINKS, LISTED, CATALOGUE, REACHABLE, None),
        "opening.html": session_page(LINKS, LISTED, queued, REACHABLE),
        "session.html": session_page(LINKS, LISTED, showing(PARENT, settled), REACHABLE),
        "waiting.html": session_page(LINKS, LISTED, showing(PARENT, waiting), REACHABLE),
        "answering.html": session_page(LINKS, LISTED, showing(PARENT, answering), REACHABLE),
        "handed-off.html": session_page(LINKS, LISTED, showing(PARENT, handed), REACHABLE),
        "stalled.html": session_page(LINKS, LISTED, stalled, REACHABLE),
        "refused.html": session_page(LINKS, LISTED, turned_down, REACHABLE),
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
