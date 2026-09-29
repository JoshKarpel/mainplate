from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Annotated
from typing import Final

from pydantic import Field
from pydantic_ai import ModelRetry
from pydantic_ai.toolsets import FunctionToolset

from mainplate.tools.files.anchors import Anchored
from mainplate.tools.files.tools import Files
from mainplate.tools.files.tools import GitTracked
from mainplate.tools.files.tools import Refused

CONTEXT: Final = 2
MAX_CONTEXT: Final = 3
MAX_MATCHES: Final = 100


@dataclass(frozen=True, slots=True)
class Found:
    path: str
    anchored: Anchored
    matches: tuple[int, ...]


def regions(matches: tuple[int, ...], total: int, context: int) -> tuple[tuple[int, int], ...]:
    """Matching lines padded with context, with overlapping or touching regions joined."""
    joined: list[tuple[int, int]] = []
    for at in matches:
        start = max(0, at - context)
        stop = min(total, at + context + 1)
        if joined and start <= joined[-1][1]:
            joined[-1] = (joined[-1][0], max(joined[-1][1], stop))
        else:
            joined.append((start, stop))
    return tuple(joined)


def included(path: str, glob: str) -> bool:
    """Whether a repository-relative path matches the optional file glob."""
    return not glob or PurePosixPath(path).match(glob)


def rendered(
    pattern: str,
    path: str,
    found: tuple[Found, ...],
    skipped: int,
    truncated: bool,
    context: int,
) -> str:
    """A bounded search result whose file lines carry the same anchors as `read`."""
    many = sum(len(each.matches) for each in found)
    if not found:
        answer = f"{pattern!r} found no matching lines in {path}"
        return f"{answer}; skipped {skipped} files that `read` refuses" if skipped else answer

    qualifier = "first " if truncated else ""
    files = "file" if len(found) == 1 else "files"
    lines = "line" if many == 1 else "lines"
    parts = [f"{pattern!r} in {path}: {qualifier}{many} matching {lines} in {len(found)} {files}"]
    if truncated:
        parts[0] += "; narrow `pattern`, `path`, or `glob` to see the rest"
    if skipped:
        parts[0] += f"; skipped {skipped} files that `read` refuses"
    parts.append("")

    for result in found:
        for start, stop in regions(result.matches, len(result.anchored.lines), context):
            parts.append(f"{result.path}, lines {start + 1}-{stop} of {len(result.anchored.lines)}:")
            parts.append(result.anchored.rendered(start, stop))
            parts.append("")
    return "\n".join(parts).rstrip()


async def searched(
    files: Files,
    pattern: str,
    path: str = ".",
    glob: str = "",
    context: int = CONTEXT,
    limit: int = MAX_MATCHES,
) -> str:
    """Search repository files and render the matching regions with whole-file anchors."""
    try:
        expression = re.compile(pattern)
    except re.error as error:
        raise Refused(f"{pattern!r} is not a valid regular expression: {error}") from None

    located = files.resolved(path)
    if not isinstance(located.root, GitTracked):
        raise Refused(
            f"{path!r} is not in a repository, and `grep` only searches one: use `bash` with `rg` or `grep` there"
        )
    if not located.path.exists():
        raise Refused(f"there is no file or directory at {path!r}")

    tree = located.root
    if located.path.is_dir():
        entries = await tree.entries(located.path)
        candidates = tuple(located.path / entry for entry in entries)
    else:
        candidates = (located.path,)

    results: list[Found] = []
    skipped = 0
    remaining = limit
    truncated = False
    for candidate in candidates:
        relative = candidate.relative_to(tree.path.resolve()).as_posix()
        if not included(relative, glob):
            continue
        try:
            async with files.exclusively(candidate):
                _, text = await asyncio.to_thread(files.loaded, relative)
        except Refused:
            skipped += 1
            continue

        matches = tuple(at for at, line in enumerate(text.lines) if expression.search(line))
        if not matches:
            continue
        selected = matches[:remaining]
        if selected:
            results.append(Found(path=relative, anchored=Anchored.over(text.lines), matches=selected))
            remaining -= len(selected)
        if len(matches) > len(selected):
            truncated = True
            break
        if remaining == 0:
            continue

    return rendered(pattern, path, tuple(results), skipped, truncated, context)


def grep_tools(files: Files) -> FunctionToolset[None]:
    """The anchored repository search tool, bound to the same roots and locks as the file tools."""
    toolset = FunctionToolset[None]()

    async def grep(
        pattern: str,
        path: str = ".",
        glob: str = "",
        context: Annotated[int, Field(ge=0, le=MAX_CONTEXT)] = CONTEXT,
        limit: Annotated[int, Field(ge=1, le=MAX_MATCHES)] = MAX_MATCHES,
    ) -> str:
        r"""
        Search repository text files and return matching regions with anchors accepted by `edit`.

        Use this instead of shell `rg` or `grep` when the result may be edited: each returned line has
        the same whole-file anchor `read` would give it, so a separate read is unnecessary. The search
        is line-oriented and `pattern` uses Python regular-expression syntax. Use `bash` for multiline,
        structural, or unusually configured searches.

        A directory search covers only files Git tracks or would track, so ignored build output and
        installed environments stay out. Files that `read` refuses, including binary and oversized
        files, are skipped and counted. Results stop at `limit`; narrow the pattern, path, or optional
        file `glob` when the header says more matches exist.

        Args:
            pattern: Python regular expression to search for on each line.
            path: Repository file or directory to search. Defaults to the repository root.
            glob: Optional repository-relative file glob, such as `*.py` or `src/**/*.rs`.
            context: Lines of context on each side of a match, from 0 through 3.
            limit: Maximum matching lines to return, from 1 through 100.

        """
        try:
            return await searched(files, pattern, path, glob, context, limit)
        except Refused as refusal:
            raise ModelRetry(str(refusal)) from None

    toolset.add_function(grep)
    return toolset
