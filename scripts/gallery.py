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

from mainplate.agent import Choice
from mainplate.agent import Listed
from mainplate.catalogue import Catalogue
from mainplate.catalogue import Offering
from mainplate.console import LINKS
from mainplate.conversation import messages_key
from mainplate.conversation import prompt_key
from mainplate.conversation import transcript
from mainplate.conversation import tree_key
from mainplate.forge import Reachable
from mainplate.forge import Repository
from mainplate.pages import fork_page
from mainplate.pages import session_page
from mainplate.pages import start_page
from mainplate.reference import Cost
from mainplate.reference import Facts
from mainplate.reference import Reference
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

PARENT = Session(id="aa" * 16, created_at=WHEN, title="Why does the poll stop after one answer")

# A branch, and a branch of that branch, so the sidebar's nesting is drawn at more than one depth
# and the turn each left at is visible on the row.
LISTED = (
    PARENT,
    Session(
        id="bb" * 16,
        created_at=WHEN + timedelta(minutes=4),
        title=PARENT.title,
        forked=Origin(session=PARENT.id, turn=1),
    ),
    Session(
        id="cc" * 16,
        created_at=WHEN + timedelta(minutes=9),
        title=PARENT.title,
        forked=Origin(session="bb" * 16, turn=2),
    ),
    Session(id="dd" * 16, created_at=WHEN, title="Add a thinking control to the picker"),
)

# One turn per kind of thing a panel can hold, so a styling change can be seen against all of them
# at once rather than against whichever session happened to be open. The code block is deliberately
# wider than the column, because the one thing a transcript must never do is scroll the page
# sideways.
LONG_LINE = "    return Response.from_content(status, html_content(render(transcript_region(links, session, said))))"

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
        ]
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
        ]
    ),
]

TOOL_IN_FLIGHT: list[ModelMessage] = [
    ModelRequest(parts=[UserPromptPart(content="Now check the stylesheet handles a long line.")]),
    ModelResponse(
        parts=[
            TextPart(content="Checking."),
            ToolCallPart(tool_name="grep", args={"pattern": "overflow-x"}, tool_call_id="call-2"),
        ]
    ),
]


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
    """A checkpoint holding these turns, in exactly the shape a pass would have written."""
    written: dict[str, object] = {}
    for turn, messages in enumerate(turns):
        written[prompt_key(turn)] = opening(messages)
        written[messages_key(turn)] = ModelMessagesTypeAdapter.dump_python(list(messages), mode="json")
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

TREES = ("9e75602b2554519c9f620dfdb2010586fde7e076", "3de66468884176acb6dc1a522aa8cfa5ace6f0e0")


def snapshotted(written: dict[str, object]) -> dict[str, object]:
    """The same checkpoint with a tree recorded per turn, as a console with a workspace writes."""
    return {**written, **{tree_key(turn): tree for turn, tree in enumerate(TREES)}}


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
        workspace=WORKSPACE / session.id if working else None,
    )


def pages() -> dict[str, str]:
    """Every page worth looking at, by the file it is written to."""
    settled = recorded(CONVERSATION, TOOL_IN_FLIGHT)
    waiting = dict(settled)
    waiting[prompt_key(2)] = "And what about a turn still being answered?"

    stalled = showing(LISTED[3], recorded(CONVERSATION), answerable=False)

    return {
        "start.html": start_page(LINKS, LISTED, CATALOGUE, REACHABLE, REFERENCE),
        # The same page with nothing configured to look models up in, which is the default and the
        # one a screenshot has to prove still reads as a finished page rather than as a broken one.
        "start-unreferenced.html": start_page(LINKS, LISTED, CATALOGUE, REACHABLE, None),
        "session.html": session_page(LINKS, LISTED, showing(PARENT, settled)),
        "waiting.html": session_page(LINKS, LISTED, showing(PARENT, waiting)),
        "stalled.html": session_page(LINKS, LISTED, stalled),
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


def write(into: Path) -> None:
    """
    Every page, into a directory holding those pages and nothing else.

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
    print(json.dumps(sorted(written), indent=2))


if __name__ == "__main__":
    write(Path(sys.argv[1] if len(sys.argv) > 1 else "build/gallery"))
