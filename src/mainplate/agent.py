# The agent, which today is a model, some instructions, and durability.
#
# No tools yet, deliberately: the thing worth getting right first is that a conversation survives
# the process running it, and a tool call is another effect to record rather than a different
# kind of one. When tools arrive they are a toolset on this agent, and `StepwiseDurability` is
# where the recording of their calls will go.

from __future__ import annotations

from pydantic_ai import Agent

from mainplate.durability import StepwiseDurability

# The bundled durability capabilities require a name, because they mint an activity or step name
# per agent from it. `StepwiseDurability` does not: a step's name comes from the turn it belongs
# to, so a nameless agent records exactly as well. It is set anyway because a name is what an
# agent is called in instrumentation and in an error, and one name is right here: every session
# runs the same agent, and what tells two sessions apart is the workflow id.
AGENT_NAME = "mainplate"


def build_agent(model: str, instructions: str) -> Agent[None, str]:
    """
    The one agent every session talks to, with its model requests routed through the checkpoint.

    `deps_type` is left at its default because nothing here needs per-run dependencies yet; the
    output is text, because a chat console renders text.
    """
    return Agent(
        model,
        name=AGENT_NAME,
        instructions=instructions,
        capabilities=[StepwiseDurability()],
    )
