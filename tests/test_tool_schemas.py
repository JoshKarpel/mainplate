from __future__ import annotations

import gc
import weakref
from dataclasses import replace
from typing import cast

import pytest
from conftest import DEFAULT_CHOICE
from conftest import INSTRUCTIONS
from conftest import Provider
from pydantic import ValidationError
from pydantic_ai import FunctionToolset
from pydantic_ai import RunContext
from pydantic_ai import Tool
from test_agent import BWRAP
from test_agent import CHECKOUT
from test_agent import SCRATCH
from test_agent import Unrun

from mainplate.agent import agent_for
from mainplate.memo import MEMO
from mainplate.sandbox import Filesystem
from mainplate.sandbox import Isolation
from mainplate.tools import JobsInTurn
from mainplate.tools.schemas import add_function
from mainplate.tools.schemas import prototype

NO_CONTEXT = cast(RunContext[None], None)
"""What a call through a schema is handed for a tool that takes no context, which it never reads."""


def every_tool_a_session_is_given() -> list[Tool[None]]:
    """Every tool `agent_for` builds for a checkout with a sandbox and jobs, which is all of them but artifacts."""
    chosen = replace(DEFAULT_CHOICE, isolation=Isolation(filesystem=Filesystem.CHECKOUT))
    jobs = JobsInTurn(jobs=Unrun(), session="s", turn=0)
    agent = agent_for(Provider().endpoints(), chosen, INSTRUCTIONS, CHECKOUT, SCRATCH, BWRAP, jobs=jobs)
    return [
        tool for toolset in agent.toolsets if isinstance(toolset, FunctionToolset) for tool in toolset.tools.values()
    ]


def greeting_tool(greeting: str) -> FunctionToolset[None]:
    """A toolset of one tool, a closure over `greeting`, as a tool package builds one per pass."""

    def greet(name: str, times: int = 1) -> str:
        """
        Greet somebody.

        Args:
            name: Who to greet.
            times: How many times.

        """
        return " ".join([f"{greeting}, {name}"] * times)

    toolset = FunctionToolset[None]()
    add_function(toolset, greet)
    return toolset


class TestWhatPydanticAIWouldHaveBuilt:
    """
    The guard on leaning on Pydantic AI's insides: if an upgrade moves where a tool keeps its function
    or how its schema is made, these are what fail, rather than a session in production.
    """

    @pytest.mark.parametrize("tool", every_tool_a_session_is_given(), ids=lambda tool: tool.name)
    def test_every_tool_a_session_gets_is_the_tool_pydantic_ai_builds_from_the_same_function(
        self, tool: Tool[None]
    ) -> None:
        fresh = FunctionToolset[None]().add_function(tool.function, name=tool.name, takes_ctx=tool.takes_ctx)
        assert tool.tool_def == fresh.tool_def

    async def test_each_pass_s_copy_calls_its_own_closure(self) -> None:
        hello = greeting_tool("hello").tools["greet"]
        howdy = greeting_tool("howdy").tools["greet"]
        assert await hello.function_schema.call({"name": "ada", "times": 2}, NO_CONTEXT) == "hello, ada hello, ada"
        assert await howdy.function_schema.call({"name": "grace"}, NO_CONTEXT) == "howdy, grace"

    def test_arguments_are_still_validated(self) -> None:
        tool = greeting_tool("hi").tools["greet"]
        with pytest.raises(ValidationError):
            tool.function_schema.validator.validate_python({"name": "ada", "times": "several"})


class TestTheSchemaIsWorkedOutOnce:
    def test_a_second_pass_s_closure_is_answered_from_the_memo(self) -> None:
        name = f"{prototype.__module__}.{prototype.__qualname__}"

        def tally() -> tuple[int, int]:
            found = next(tally for tally in MEMO.account().tallies if tally.name == name)
            return found.hits, found.misses

        greeting_tool("first")
        hits, misses = tally()
        greeting_tool("second")
        assert tally() == (hits + 1, misses)

    def test_the_memo_keeps_no_pass_s_closure_alive(self) -> None:
        """
        A closure is a session's reach, and one held by the memo would be held for the process.

        A `def` of this test's own, so its closure is the first of its shape, which is the one the
        memo works the schema out from: a shape another test memoized first would keep that test's
        closure, if any, and this one would go free whatever the memo did.
        """

        def built(reach: str) -> FunctionToolset[None]:
            def reached() -> str:
                """Say what this pass reaches."""
                return reach

            toolset = FunctionToolset[None]()
            add_function(toolset, reached)
            return toolset

        toolset = built("one session's files")
        function = weakref.ref(toolset.tools["reached"].function)
        del toolset
        gc.collect()
        assert function() is None
