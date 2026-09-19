from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from dataclasses import field
from typing import Literal
from typing import assert_never

from pydantic_ai import ModelRetry
from pydantic_ai.toolsets import FunctionToolset

from mainplate.snapshots import Worktree

type Operation = Literal["stage", "stage-all", "intent-to-add"]

RETRIES = 3


class Refused(ValueError):
    """A Git index change that was not specific enough to perform."""


@dataclass(frozen=True, slots=True)
class GitIndex:
    """The real index of one session's worktree, changed only through fixed Git operations."""

    worktree: Worktree
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, compare=False)

    async def apply(self, operation: Operation, paths: tuple[str, ...] = ()) -> str:
        """Apply one closed index operation and report the resulting state change."""
        match operation:
            case "stage":
                arguments = self.paths_for(("add",), paths)
                reported = f"Staged changes selected by {len(paths)} path{'s' if len(paths) != 1 else ''}."
            case "stage-all":
                if paths:
                    raise Refused("stage-all takes no paths; it stages every change in the worktree")
                arguments = ("add", "-A")
                reported = "Staged every change in the worktree."
            case "intent-to-add":
                arguments = self.paths_for(("add", "--intent-to-add"), paths)
                reported = f"Registered {len(paths)} path{'s' if len(paths) != 1 else ''} with intent to add."
            case _ as unreachable:
                assert_never(unreachable)

        async with self.lock:
            ran = await self.worktree.git(*arguments)
        if not ran.ok:
            raise Refused(f"git refused the index change ({ran.code}): {ran.err or ran.out}")
        return reported

    @staticmethod
    def paths_for(command: tuple[str, ...], paths: tuple[str, ...]) -> tuple[str, ...]:
        """The fixed argument vector for a path-based operation."""
        if not paths:
            raise Refused(f"{' '.join(command)} needs at least one path")
        if any(not path or "\0" in path for path in paths):
            raise Refused("paths must be non-empty and contain no NUL bytes")
        return ("--literal-pathspecs", *command, "--", *paths)


async def guarded[T](changing: Awaitable[T]) -> T:
    """Turn a correctable index refusal into something the model can retry."""
    try:
        return await changing
    except Refused as refusal:
        raise ModelRetry(str(refusal)) from None


def git_tools(worktree: Worktree) -> FunctionToolset[None]:
    """The one Git tool, bound to the real index of one session's worktree."""
    index = GitIndex(worktree)
    toolset = FunctionToolset[None]()

    async def git(operation: Operation, paths: tuple[str, ...] = ()) -> str:
        """
        Make a controlled change to Git's index.

        This tool does not accept Git commands or options. Choose one operation:

        - `stage`: stage changes selected by the literal paths in `paths`.
        - `stage-all`: stage every modification, deletion, and untracked file. `paths` must be empty.
        - `intent-to-add`: register new literal paths without staging their content. Use this when a
          pre-commit hook needs to see a newly generated file while leaving its content as an unstaged
          change.

        Git commit history, branches, remotes, and configuration are out of reach. Use `stage-all`
        deliberately: ignored files stay ignored, but every other worktree change enters the index.

        Args:
            operation: Which closed index operation to perform.
            paths: Literal repository-relative paths for `stage` or `intent-to-add`.

        """
        return await guarded(index.apply(operation, paths))

    toolset.add_function(git, retries=RETRIES)
    return toolset
