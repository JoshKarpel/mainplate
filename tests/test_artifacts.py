"""Artifact versions stay durable and isolated from session checkpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from calling import calling
from pydantic_ai import ModelRetry
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage
from without_asgi import ASGIApp
from without_durability_sqlite import connect

from mainplate import artifacts
from mainplate.service import Service
from mainplate.tools.artifacts.tools import artifact_tools
from mainplate.tools.files import Files
from mainplate.tools.files import Scratch

HTML = b"<!doctype html>\r\n<html><script>document.body.textContent='hello'</script></html>"
NEXT = b"<html><body>version two</body></html>"


@pytest.mark.asyncio
async def test_import_compare_and_swap_and_replay(service: Service) -> None:
    first = await artifacts.import_html(service.database, HTML, "session:turn:call", title="First")
    again = await artifacts.import_html(service.database, HTML, "session:turn:call", title="First")
    assert first == again
    assert (await artifacts.content(service.database, first.id, 1)) == (first, HTML)
    second = await artifacts.import_html(service.database, NEXT, "next-call", first.id, 1)
    assert second.version == 2
    assert (await artifacts.content(service.database, first.id, 1)) == (first, HTML)
    with pytest.raises(artifacts.Refused, match="expected_version"):
        await artifacts.import_html(service.database, NEXT, "stale-call", first.id, 1)
    assert await artifacts.import_html(service.database, NEXT, "next-call", first.id, 1) == second
    assert [each.version for each in await artifacts.listing(service.database, artifact_id=first.id)] == [2, 1]
    assert [each.version for each in await artifacts.listing(service.database, artifact_id=first.id, cursor=2)] == [1]
    assert [each.version for each in await artifacts.listing(service.database)] == [2]


@pytest.mark.asyncio
async def test_versions_survive_reopening_database(tmp_path: Path) -> None:
    database = tmp_path / "artifacts.db"
    first = connect(database)
    try:
        await artifacts.prepare(first)
        saved = await artifacts.import_html(first, HTML, "call")
    finally:
        await first.aclose()
    reopened = connect(database)
    try:
        await artifacts.prepare(reopened)
        assert await artifacts.content(reopened, saved.id) == (saved, HTML)
    finally:
        await reopened.aclose()


@pytest.mark.asyncio
async def test_export_never_overwrites_and_keeps_exact_bytes(service: Service, tmp_path: Path) -> None:
    saved = await artifacts.import_html(service.database, HTML, "call")
    tools = artifact_tools(service.database, Files(roots=(Scratch(tmp_path),)), "session")

    ctx = RunContext(deps=None, model=TestModel(), usage=RunUsage())
    registered = await tools.get_tools(ctx)
    exported = await tools.call_tool(
        "artifact_to_file", {"id": saved.id, "path": "copy.html"}, ctx, registered["artifact_to_file"]
    )
    assert "version 1" in str(exported)
    assert (tmp_path / "copy.html").read_bytes() == HTML
    with pytest.raises(ModelRetry, match="already exists"):
        await tools.call_tool(
            "artifact_to_file", {"id": saved.id, "path": "copy.html"}, ctx, registered["artifact_to_file"]
        )
    assert (tmp_path / "copy.html").read_bytes() == HTML
    listed = await tools.call_tool("list_artifacts", {}, ctx, registered["list_artifacts"])
    assert saved.id in str(listed)
    assert "script" not in str(listed)


@pytest.mark.asyncio
async def test_tool_import_replays_without_rereading_changed_file(service: Service, tmp_path: Path) -> None:
    source = tmp_path / "page.html"
    source.write_bytes(HTML)
    tools = artifact_tools(service.database, Files(roots=(Scratch(tmp_path),)), "session:turn")
    ctx = RunContext(deps=None, model=TestModel(), usage=RunUsage(), tool_call_id="call-one")
    registered = await tools.get_tools(ctx)
    arguments = {"path": "page.html", "title": "Saved"}
    first = await tools.call_tool("file_to_artifact", arguments, ctx, registered["file_to_artifact"])
    source.unlink()
    replay = await tools.call_tool("file_to_artifact", arguments, ctx, registered["file_to_artifact"])
    assert replay == first
    assert len(await artifacts.listing(service.database)) == 1


@pytest.mark.asyncio
async def test_page_pins_preview_and_download_to_identical_bytes(app: ASGIApp, service: Service) -> None:
    saved = await artifacts.import_html(service.database, HTML, "call", title="<Unsafe>")
    async with calling(app) as client:
        page = await client.get(f"/artifacts/{saved.id}")
        assert page.status == 200
        assert f"version={saved.version}" in page.text
        assert 'sandbox="allow-scripts"' in page.text
        assert "&lt;Unsafe&gt;" in page.text
        preview = await client.get(f"/artifacts/{saved.id}/content?version=1")
        download = await client.get(f"/artifacts/{saved.id}/content?version=1&download=1")
        assert preview.body == download.body == HTML
        assert "sandbox allow-scripts" in preview.headers["content-security-policy"]
        assert "connect-src 'none'" in preview.headers["content-security-policy"]
        assert download.headers["content-disposition"].startswith("attachment;")
        assert preview.headers["x-content-type-options"] == "nosniff"
        assert "<Unsafe>" not in (await client.get("/artifacts")).text
        await artifacts.import_html(service.database, NEXT, "next", saved.id, 1)
        assert (await client.get(f"/artifacts/{saved.id}/content?version=1")).body == HTML
