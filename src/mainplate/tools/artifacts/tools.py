"""Artifact transfer uses the same path policy and locks as the anchored file tools."""

from __future__ import annotations

from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset
from without_durability_sqlite import Database

from mainplate import artifacts
from mainplate.tools.files import Files
from mainplate.tools.files.tools import Refused as FileRefused


def artifact_tools(database: Database, files: Files | None, session: str) -> FunctionToolset[None]:
    """Expose catalogue reads everywhere and transfer only where file tools are available."""
    tools = FunctionToolset[None]()

    async def list_artifacts(
        query: str | None = None, artifact_id: str | None = None, cursor: int | None = None
    ) -> str:
        """
        List up to 30 artifacts or versions as metadata only; use `artifact_id` for version history.

        For version history, `cursor` is the last version returned with `artifact_id`.
        For the catalogue, `cursor` is an offset (start at 0; add 30 per page).
        HTML is never included in this listing.
        """
        try:
            found = await artifacts.listing(database, query, artifact_id, cursor)
        except artifacts.Refused as error:
            raise ModelRetry(str(error)) from None
        return (
            "\n".join(
                f"{each.id} v{each.version}: {each.title} ({each.created_at}) /artifacts/{each.id}?version={each.version}"
                for each in found
            )
            or "No artifacts found."
        )

    tools.add_function(list_artifacts)
    if files is None:
        return tools

    async def file_to_artifact(
        ctx: RunContext[None],
        path: str,
        id: str | None = None,  # noqa: A002 - public artifact identifier
        expected_version: int | None = None,
        title: str | None = None,
        root: str = "",
    ) -> str:
        """
        Import a self-contained UTF-8 HTML file as a new artifact or an atomic version update.

        Pass `id` and `expected_version` together to update; a stale version is refused.
        A retried call creates no extra version. The returned URL opens the immutable version.
        Embed scripts, styles, fonts and images; preview blocks network access.
        """
        try:
            if ctx.tool_call_id is None:
                raise RuntimeError("An artifact import needs a recorded tool call id")
            operation = f"{session}:{ctx.tool_call_id}"
            if previous := await artifacts.completed(database, operation):
                return f"{previous.title}: {previous.id} version {previous.version} /artifacts/{previous.id}?version={previous.version}"
            here = files.resolved(path, root).path
            async with files.exclusively(here):
                if not here.is_file() or here.is_symlink():
                    raise FileRefused(f"{path!r} is not a regular file")
                if here.stat().st_size > artifacts.MAX_HTML:
                    raise artifacts.Refused("HTML is too large (2 MiB maximum)")
                html = here.read_bytes()
            result = await artifacts.import_html(database, html, operation, id, expected_version, title)
        except (artifacts.Refused, FileRefused) as error:
            raise ModelRetry(str(error)) from None
        return f"{result.title}: {result.id} version {result.version} /artifacts/{result.id}?version={result.version}"

    async def artifact_to_file(
        id: str,  # noqa: A002 - public artifact identifier
        path: str,
        version: int | None = None,
        root: str = "",
    ) -> str:
        """Export exact HTML bytes into a new file under one of this session's roots; never overwrite."""
        try:
            found = await artifacts.content(database, id, version)
            if found is None:
                raise artifacts.Refused("No such artifact version")
            selected, html = found
            here = files.resolved(path, root).path
            async with files.exclusively(here):
                here.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with here.open("xb") as output:
                        output.write(html)
                except FileExistsError:
                    raise FileRefused(f"{path!r} already exists") from None
        except (artifacts.Refused, FileRefused) as error:
            raise ModelRetry(str(error)) from None
        return f"Exported {selected.id} version {selected.version} to {path} ({len(html)} bytes)"

    tools.add_function(file_to_artifact, takes_ctx=True)
    tools.add_function(artifact_to_file)
    return tools
