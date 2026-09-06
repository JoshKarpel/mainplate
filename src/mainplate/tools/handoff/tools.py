# The one tool here that does nothing to a file and nothing to a machine: what it acts on is the
# conversation running it.

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from typing import Final

from pydantic_ai import ModelRetry
from pydantic_ai.toolsets import FunctionToolset

type Handing = Callable[[str], Awaitable[None]]
"""
Where a written handoff goes, injected rather than reached for.

Symmetric with `Pricer`, `Draining` and `Guiding`, and for the same cycle: what a handoff *is* is a
message put into a session's inbox, and this package cannot import the service that writes one. It
keeps the tool ignorant of checkpoints and inboxes alike, which is the same ignorance that lets one
toolset serve any session.
"""

LEAST: Final = 200
"""
How short a handoff may be before it is refused as one somebody will not be able to work from.

A number rather than a judgement, because there is no judgement available here: what this can tell
apart is a document from an acknowledgement, and two hundred characters is comfortably below any
real handoff and comfortably above "Done, I have written the handoff." A model that trips it is told
so and writes a real one, which is a `ModelRetry` doing exactly what retries are for.
"""


ASKING: Final = (
    "Hand this conversation off. Summarise where the work has got to, what was decided and why, and "
    "what to do next, and pass that to `hand_off`. Check the working tree rather than trusting your "
    "recollection of it."
)
"""
The message the console writes to open a handoff turn, which is the other half of the tool's contract.

Beside the tool rather than in the service that delivers it, because the two are one thing said
twice: what a handoff is *for* is the tool's description, and this is the instruction to go and write
one. Split across two modules they would drift, and the drift would be a turn that produced prose
instead of a call.

**Deliberately not a template.** It says what a handoff is about and stops, because what somebody
picking up a refactor needs and what somebody picking up an investigation needs are not the same
document, and a fixed set of headings would have every session filling in the ones it has nothing to
say under. The model is better placed than this string to know which parts matter, and a person who
disagrees can say so: `Service.hand_off` appends whatever guidance they gave to this.

**Short, because the tool's description is the other half.** A long ask in the message and a long
description on the tool would be two sets of instructions about one document, and a model reconciling
them writes to neither.
"""


def handoff_tools(handing: Handing) -> FunctionToolset[None]:
    """
    One tool, which ends a stretch of context by writing down what the next one needs.

    **Present in every session rather than added when a handoff is wanted**, and that is a decision
    about the cache rather than about convenience. Tool definitions sit at the top of the cached
    prefix, above the system prompt, so adding one invalidates the whole conversation below it: a
    tool introduced at handoff time would cost a full uncached read of the entire window, where a
    permanent one costs its own description at cache-read prices on every request. Four orders of
    magnitude apart, measured in what a provider charges rather than in taste.

    That it is always there is also why it is worth being useful outside a handoff turn. A model that
    decides on its own that the context is spent can write one, and what happens next is the same
    thing that happens when the console asks.
    """
    toolset = FunctionToolset[None]()

    async def hand_off(document: str) -> str:
        """
        Write down what the next agent needs, and start this conversation's context again.

        What you pass becomes the whole of what the model after you is told. Everything said before
        it stays in the transcript for a person to read and none of it is in the context, so the test
        for the document is whether somebody who has read only it, plus the repository, can carry on
        without asking a question you already know the answer to.

        **Pass the document and nothing else.** No preamble, no "here is the handoff", no question at
        the end. What you pass is stored verbatim and read as though it had always been the opening
        of the conversation, so a sentence addressed to whoever asked for it is a sentence the next
        model has to work out the meaning of.

        Check rather than recall before you write. The files are in front of you and the context you
        are summarising is the part you are least able to trust; a path, a command, or a state you
        assert from memory is the thing most likely to be wrong, and it is cheap to look. What is
        already written down does not need repeating - name a file rather than quoting it.

        How to shape it is yours. What a refactor needs handed over and what an investigation needs
        are different documents, and you have read the conversation.

        Args:
            document: The handoff itself, as Markdown, and nothing around it.

        """
        written = document.strip()
        if len(written) < LEAST:
            raise ModelRetry(
                f"a handoff of {len(written)} characters is an acknowledgement rather than a document; "
                f"write what the next agent needs in order to carry on, and pass that"
            )
        await handing(written)
        return (
            "Recorded. This conversation's context starts again from that document, and everything "
            "said before it stays in the transcript for a person to read."
        )

    toolset.add_function(hand_off)
    return toolset
