# The three artifact tools: finding what artifacts there are, keeping a file as a version of one, and
# writing a version back out as a file.
#
# Moving a document between a file and an artifact is the whole of what the model does with one. It
# builds the page with the file tools it already has, anchored edits included, and keeps it; to change
# a kept page it writes a version back out, edits the file, and keeps that. A tool that took HTML as
# an argument would be a second way to write a file, one with no anchors and no diff, and every
# update would be the page retyped whole.

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from typing import Final

from pydantic_ai import ModelRetry
from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset
from without_durability_sqlite import Database

from mainplate import artifacts
from mainplate.tools.files import Files
from mainplate.tools.files.tools import Refused as FileRefused

# Where `file_to_artifact` records which version it kept, under the call's `metadata`, for the page
# to link to. The model is never sent it; it has the same two numbers in words.
KEPT: Final = "artifact"


@dataclass(frozen=True, slots=True)
class Artifacts:
    """
    The artifact store as one turn of one session reaches it.

    The session and the turn are carried because a version records the call that kept it, and a
    call's id is only unique within its turn of its session: together they are the call's own key in
    the checkpoint. Built per pass, like the rest of the agent.
    """

    database: Database
    session: str
    turn: int

    def call(self, called: str) -> artifacts.Call:
        return artifacts.Call(session=self.session, turn=self.turn, call=called)


def said(kept: artifacts.Version) -> str:
    """What the model is told a version is, which is everything it needs to update it next."""
    return f"{kept.title}: artifact {kept.artifact}, version {kept.version} of {kept.current}"


def paged(lines: list[str], last: int | None, empty: str) -> str:
    """
    One page of a listing as the model is sent it, ending with where to continue when it was full.

    A full page is what says there may be more, as it is for the catalogue's link on the page, so the
    model is told in the listing rather than told the page size in the description: a number written
    in the description is a second copy of `LISTED`, and a model counting lines against it is doing
    the store's arithmetic. The cost, stated: a listing of exactly `LISTED` says there may be more,
    and one more call finds nothing.
    """
    if not lines or last is None:
        return empty
    if len(lines) < artifacts.LISTED:
        return "\n".join(lines)
    return "\n".join([*lines, f"There may be more: pass before={last} for the next page."])


def artifact_tools(store: Artifacts, files: Files | None) -> FunctionToolset[None]:
    """
    `list_artifacts` everywhere, and the two that move a document between a file and an artifact
    wherever there are files to move it between.

    The listing is offered to a session with no files because an artifact is the console's rather than
    any session's, and asking what exists is a question with no path in it. The pair needs a root to
    resolve a path against, so a session that reaches nothing gets neither, rather than two tools that
    refuse every call. `files` is the same `Files` the file tools hold, so a file being kept and the
    same file being edited take one lock.
    """
    toolset = FunctionToolset[None]()

    async def list_artifacts(query: str | None = None, artifact: str | None = None, before: int | None = None) -> str:
        """
        List artifacts, or one artifact's versions, newest first, a page at a time. Never returns HTML.

        With no `artifact`, lists every artifact at its current version, and `query` narrows that to
        titles containing it. With `artifact`, lists that artifact's versions instead.

        A page that may not be the last ends by saying so, with the `before` to pass for the next.

        Args:
            query: Text a title contains, matched without regard to case.
            artifact: An artifact's id, to list its versions rather than every artifact.
            before: From the end of a previous listing, to continue it.

        """
        if artifact is None:
            found = await artifacts.catalogue(store.database, before, query)
            return paged(
                [f"{said(kept)}, kept {kept.made_at:%Y-%m-%d %H:%M}" for kept in found],
                found[-1].seq if found else None,
                "No artifacts.",
            )
        versions = await artifacts.history(store.database, artifact, before)
        return paged(
            [f"version {kept.version}, kept {kept.made_at:%Y-%m-%d %H:%M}" for kept in versions],
            versions[-1].version if versions else None,
            f"No versions of {artifact}.",
        )

    toolset.add_function(list_artifacts)
    if files is None:
        return toolset

    async def file_to_artifact(
        ctx: RunContext[None],
        path: str,
        artifact: str | None = None,
        expected_version: int | None = None,
        title: str | None = None,
        root: str = "",
    ) -> ToolReturn:
        """
        Keep a self-contained HTML file as a new artifact, or as the next version of one.

        The file is kept exactly as it is, and the person sees it in the console, sandboxed with no
        network: put every script, style, font and image inside the file, and link to nothing.

        To update an artifact, pass its id as `artifact` and the version you last saw as
        `expected_version`. If someone has kept a newer version since, this refuses and changes
        nothing: write the current version out with `artifact_to_file`, look at what changed, and
        keep again.

        Args:
            ctx: The run this call is part of, for the call's own id; never sent to the model.
            path: The HTML file, relative to `root`.
            artifact: The artifact to keep this as the next version of. Left out, a new artifact.
            expected_version: The version of `artifact` you last saw, required with it.
            title: What the artifact is called. Left out on an update, it keeps its title.
            root: Which of this session's places `path` is relative to. Your instructions name them.

        """
        if ctx.tool_call_id is None:
            raise ModelRetry("this call has no id to keep a version under")
        onto = parsed_onto(artifact, expected_version)
        made_by = store.call(ctx.tool_call_id)
        # Before the file is read, so a call run again after a pass fell over gets the version it
        # already kept, whatever has happened to the file since.
        kept = await artifacts.made(store.database, made_by)
        if kept is None:
            try:
                html = await read_html(files, path, root)
                kept = await artifacts.keep(store.database, html, made_by, datetime.now(UTC), onto, title)
            except (artifacts.Refused, FileRefused) as refused:
                raise ModelRetry(str(refused)) from None
        return ToolReturn(
            return_value=said(kept), metadata={KEPT: {"artifact": kept.artifact, "version": kept.version}}
        )

    async def artifact_to_file(artifact: str, path: str, version: int | None = None, root: str = "") -> str:
        """
        Write one version of an artifact out as a new file, byte for byte. Refuses a path that exists.

        This is how to change an artifact: write it out, edit the file, and keep it again with
        `file_to_artifact`, naming the version you wrote out as `expected_version`.

        Args:
            artifact: The artifact's id.
            path: Where to write it, relative to `root`. Must not exist yet.
            version: Which version. Left out, the current one.
            root: Which of this session's places `path` is relative to. Your instructions name them.

        """
        found = await artifacts.content(store.database, artifact, version)
        if found is None:
            raise ModelRetry(f"there is no artifact {artifact}" + ("" if version is None else f" at version {version}"))
        kept, html = found
        try:
            await files.create_bytes(path, html, root)
        except FileRefused as refused:
            raise ModelRetry(str(refused)) from None
        return f"Wrote {said(kept)} to {path}, {len(html)} bytes."

    toolset.add_function(file_to_artifact, takes_ctx=True)
    toolset.add_function(artifact_to_file)
    return toolset


def parsed_onto(artifact: str | None, expected: int | None) -> artifacts.Updating | None:
    """The two arguments that name an update as the one value that does, or a refusal saying which is missing."""
    match artifact, expected:
        case None, None:
            return None
        case str(), int() if expected >= 1:
            return artifacts.Updating(artifact=artifact, expected=expected)
        case str(), int():
            raise ModelRetry("expected_version is a version number, which starts at 1")
        case str(), None:
            raise ModelRetry("an update names the version you last saw: pass expected_version with artifact")
        case _:
            raise ModelRetry("expected_version belongs to an update: pass artifact with it")


async def read_html(files: Files, path: str, root: str) -> artifacts.Html:
    """A file's bytes as a document the store will keep, read under the file's lock and bounded before reading."""
    return artifacts.Html.parse(await files.read_bytes(path, root, artifacts.LARGEST))
