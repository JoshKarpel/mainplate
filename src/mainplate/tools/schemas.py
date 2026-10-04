# How every tool package adds a tool to its toolset, with the tool's schema worked out once a process.
#
# A toolset is built per pass, because each tool is a closure over what that session may reach, and
# Pydantic AI derives a tool's JSON schema and argument validator from the function's signature every
# time one is added: a few milliseconds a tool, paid by every pass of every turn, for an answer that
# depends on nothing the closure captures. So the first `Tool` built for a function is memoized, and
# every later pass gets a copy of it holding its own closure. Not a tool package itself, and not
# reached by the harness: each package's constructor calls `add_function` here where it would have
# called the toolset's own.
#
# **What is memoized holds no closure**, which is the one thing easy to get wrong here: a closure is a
# session's reach, its `Files` and locks and checkout, and one kept in the memo would keep that
# session alive for as long as the process. So the key holds the function only weakly, and the tool
# kept holds a placeholder where the function was.
#
# **This leans on how `Tool` is built inside, which is Pydantic AI's to change.** It assumes a tool
# reaches its function only through `function_schema.function`, which is where `FunctionSchema.call`
# looks, and that `function_schema` is its private module's dataclass. `tests/test_tool_schemas.py`
# holds both against the installed version, so an upgrade that moves either fails the suite.

from __future__ import annotations

import copy
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace

from pydantic_ai import FunctionToolset
from pydantic_ai import Tool

from mainplate.memo import MEMO


def unbound(*args: object, **kwargs: object) -> object:
    """Where a memoized tool's function was, so the memo holds no session's closure; never called."""
    raise RuntimeError("a memoized tool was called, rather than a copy holding a pass's own function")


@dataclass(frozen=True, slots=True, eq=False)
class Shape:
    """
    What decides a tool's schema, and a weak way back to the function to work it out from.

    Equal by the function's *code* and its defaults rather than by the function, because every pass
    defines a new closure from the same `def`, and those share their code, their docstring and their
    signature, which is everything the schema is made of. Two closures differing only in what they
    captured are one shape; a different default, name or `takes_ctx` is another. The cost, stated: an
    annotation computed from a captured value would not be told apart, and no tool here has one.

    The function is held weakly, because a shape is the memo's key and outlives the pass: the caller
    holds the function strongly for as long as the schema is being worked out, which is the only time
    the shape reaches for it.
    """

    decided_by: tuple[object, ...]
    name: str
    takes_ctx: bool | None
    function: weakref.ref[Callable[..., object]]

    @classmethod
    def of(cls, function: Callable[..., object], name: str, takes_ctx: bool | None) -> Shape:
        defaults = function.__defaults__ or ()
        keyword_defaults = tuple(sorted((function.__kwdefaults__ or {}).items()))
        return cls(
            decided_by=(function.__code__, defaults, keyword_defaults, name, takes_ctx),
            name=name,
            takes_ctx=takes_ctx,
            function=weakref.ref(function),
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Shape) and self.decided_by == other.decided_by

    def __hash__(self) -> int:
        return hash(self.decided_by)


@MEMO.memoized
def prototype(shape: Shape) -> Tool[None]:
    """
    The tool Pydantic AI builds for `shape`, built by the toolset's own `add_function`, with its
    function taken out again.

    Through `add_function` on a toolset made as every tool package makes its own, rather than by
    calling `Tool` with arguments copied out of it, so every default the toolset applies is the one it
    applies, whatever a release changes them to.
    """
    function = shape.function()
    if function is None:  # pragma: no cover - `add_function` holds it for the whole of this call
        raise RuntimeError(f"the function behind tool {shape.name} was gone before its schema was made")
    built = FunctionToolset[None]().add_function(function, name=shape.name, takes_ctx=shape.takes_ctx)
    return holding(built, unbound)


def holding(tool: Tool[None], function: Callable[..., object]) -> Tool[None]:
    """
    A shallow copy of `tool` that calls `function`, sharing the schema's validator and everything else.

    Both places a `Tool` keeps its function are set: `function`, which Pydantic AI reads only while
    building one, and `function_schema.function`, which is what a call goes through.
    """
    held = copy.copy(tool)
    held.function = function
    held.function_schema = replace(tool.function_schema, function=function)
    return held


def add_function(
    toolset: FunctionToolset[None],
    function: Callable[..., object],
    *,
    name: str | None = None,
    takes_ctx: bool | None = None,
) -> None:
    """`toolset.add_function(function, ...)`, with the schema worked out once a process."""
    shape = Shape.of(function, name or function.__name__, takes_ctx)
    toolset.add_tool(holding(prototype(shape), function))
