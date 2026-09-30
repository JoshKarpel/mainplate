"""
Artifacts: the store's versions, the tools that move a document between a file and an artifact, and
the pages that frame one.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pytest
from calling import calling
from conftest import started
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ToolReturn
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import RunUsage
from without_asgi import ASGIApp
from without_durability_sqlite import connect
from without_html import render

from mainplate import artifacts
from mainplate.artifacts import Call
from mainplate.artifacts import Html
from mainplate.artifacts import Updating
from mainplate.console import LINKS
from mainplate.conversation import Returned
from mainplate.conversation import ToolUse
from mainplate.pages.transcript import tool_block
from mainplate.service import Service
from mainplate.tools.artifacts import Artifacts
from mainplate.tools.artifacts import artifact_tools
from mainplate.tools.artifacts.tools import KEPT
from mainplate.tools.files import Files
from mainplate.tools.files import Scratch

# A carriage return and a byte order mark, because the promise is the file byte for byte and those
# are the two things a text-mode round trip would quietly take out.
FIRST = Html.parse(b"\xef\xbb\xbf<!doctype html>\r\n<html><body><script>document.title='one'</script></body></html>")
SECOND = Html.parse(b"<html><body>two</body></html>")
THIRD = Html.parse(b"<!DOCTYPE HTML><html><body>three</body></html>")

KEPT_AT = datetime(2031, 3, 14, 15, 9, 26, tzinfo=UTC)


def by(call: str, session: str = "7" * 32, turn: int = 3) -> Call:
    return Call(session=session, turn=turn, call=call)


class TestWhatIsADocument:
    @pytest.mark.parametrize(
        ("raw", "why"),
        [
            (b"<html>" + b"x" * artifacts.LARGEST, "at most"),
            (b"<html>\xff</html>", "not UTF-8"),
            (b"just some text", "not an HTML document"),
        ],
        ids=["too large", "not utf-8", "not html"],
    )
    def test_what_is_not_one_is_refused_saying_why(self, raw: bytes, why: str) -> None:
        with pytest.raises(artifacts.Refused, match=why):
            Html.parse(raw)

    def test_a_document_is_kept_as_the_bytes_it_arrived_as(self) -> None:
        assert FIRST.raw.startswith(b"\xef\xbb\xbf<!doctype html>\r\n")


class TestKeepingAVersion:
    async def test_a_new_artifact_starts_at_version_one(self, service: Service) -> None:
        kept = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT, title="The poll")
        assert (kept.version, kept.current, kept.title, kept.made_by) == (1, 1, "The poll", by("call-a"))

    async def test_its_bytes_come_back_exactly(self, service: Service) -> None:
        kept = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT)
        assert await artifacts.content(service.database, kept.artifact, 1) == (kept, FIRST.raw)

    async def test_the_same_call_again_keeps_nothing_more(self, service: Service) -> None:
        first = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT)
        again = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT + timedelta(minutes=5))
        assert again == first
        assert len(await artifacts.catalogue(service.database)) == 1

    async def test_the_same_call_with_other_bytes_is_refused(self, service: Service) -> None:
        await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT)
        with pytest.raises(artifacts.Refused, match="already kept a different version"):
            await artifacts.keep(service.database, SECOND, by("call-a"), KEPT_AT)

    async def test_the_same_call_id_in_another_turn_is_another_call(self, service: Service) -> None:
        first = await artifacts.keep(service.database, FIRST, by("call-a", turn=3), KEPT_AT)
        other = await artifacts.keep(service.database, SECOND, by("call-a", turn=4), KEPT_AT)
        assert other.artifact != first.artifact


class TestUpdatingAnArtifact:
    async def test_an_update_onto_the_current_version_is_the_next_version(self, service: Service) -> None:
        first = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT, title="The poll")
        second = await artifacts.keep(service.database, SECOND, by("call-b"), KEPT_AT, Updating(first.artifact, 1))
        assert (second.artifact, second.version, second.current) == (first.artifact, 2, 2)

    async def test_an_earlier_version_keeps_its_bytes(self, service: Service) -> None:
        first = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT)
        await artifacts.keep(service.database, SECOND, by("call-b"), KEPT_AT, Updating(first.artifact, 1))
        found = await artifacts.content(service.database, first.artifact, 1)
        assert found is not None
        assert found[1] == FIRST.raw

    async def test_an_update_onto_a_version_no_longer_current_changes_nothing(self, service: Service) -> None:
        first = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT)
        await artifacts.keep(service.database, SECOND, by("call-b"), KEPT_AT, Updating(first.artifact, 1))
        with pytest.raises(artifacts.Refused, match="is at version 2, not 1"):
            await artifacts.keep(service.database, THIRD, by("call-c"), KEPT_AT, Updating(first.artifact, 1))
        assert [kept.version for kept in await artifacts.history(service.database, first.artifact)] == [2, 1]

    async def test_an_update_onto_nothing_says_there_is_no_such_artifact(self, service: Service) -> None:
        with pytest.raises(artifacts.Refused, match="there is no artifact nowhere"):
            await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT, Updating("nowhere", 1))

    async def test_an_update_naming_no_title_keeps_the_one_it_had(self, service: Service) -> None:
        first = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT, title="The poll")
        second = await artifacts.keep(service.database, SECOND, by("call-b"), KEPT_AT, Updating(first.artifact, 1))
        assert second.title == "The poll"

    async def test_an_update_naming_a_title_renames_the_artifact(self, service: Service) -> None:
        first = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT, title="The poll")
        await artifacts.keep(
            service.database, SECOND, by("call-b"), KEPT_AT, Updating(first.artifact, 1), title="The poll, labelled"
        )
        found = await artifacts.content(service.database, first.artifact, 1)
        assert found is not None
        assert found[0].title == "The poll, labelled"


class TestListing:
    async def test_the_catalogue_is_each_artifact_at_its_current_version_newest_first(self, service: Service) -> None:
        poll = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT, title="The poll")
        token = await artifacts.keep(service.database, SECOND, by("call-b"), KEPT_AT, title="The token")
        await artifacts.keep(service.database, THIRD, by("call-c"), KEPT_AT, Updating(poll.artifact, 1))
        listed = await artifacts.catalogue(service.database)
        assert [(kept.artifact, kept.version) for kept in listed] == [(poll.artifact, 2), (token.artifact, 1)]

    async def test_the_catalogue_continues_from_where_a_page_left_off(self, service: Service) -> None:
        for at in range(artifacts.LISTED + 2):
            await artifacts.keep(service.database, FIRST, by(f"call-{at}"), KEPT_AT, title=f"Page {at}")
        first = await artifacts.catalogue(service.database)
        rest = await artifacts.catalogue(service.database, first[-1].seq)
        assert [kept.title for kept in rest] == ["Page 1", "Page 0"]

    async def test_a_query_matches_a_title_as_written_whatever_its_case(self, service: Service) -> None:
        await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT, title="100% of THE poll")
        await artifacts.keep(service.database, SECOND, by("call-b"), KEPT_AT, title="1000 of the poll")
        listed = await artifacts.catalogue(service.database, query="0% of the")
        assert [kept.title for kept in listed] == ["100% of THE poll"]

    async def test_a_history_continues_from_the_last_version_a_page_held(self, service: Service) -> None:
        first = await artifacts.keep(service.database, FIRST, by("call-0"), KEPT_AT)
        for at in range(1, 4):
            await artifacts.keep(service.database, SECOND, by(f"call-{at}"), KEPT_AT, Updating(first.artifact, at))
        assert [kept.version for kept in await artifacts.history(service.database, first.artifact, 3)] == [2, 1]

    async def test_what_a_session_kept_is_its_own_in_the_order_it_kept_them(self, service: Service) -> None:
        mine, theirs = "a" * 32, "b" * 32
        await artifacts.keep(service.database, FIRST, by("call-a", session=mine), KEPT_AT, title="First")
        await artifacts.keep(service.database, SECOND, by("call-b", session=theirs), KEPT_AT, title="Theirs")
        await artifacts.keep(service.database, THIRD, by("call-c", session=mine), KEPT_AT, title="Second")
        assert [kept.title for kept in await artifacts.made_in(service.database, mine)] == ["First", "Second"]

    async def test_versions_outlast_the_connection_that_kept_them(self, tmp_path: Path) -> None:
        database = tmp_path / "artifacts.db"
        opened = connect(database)
        try:
            await artifacts.prepare(opened)
            kept = await artifacts.keep(opened, FIRST, by("call-a"), KEPT_AT)
        finally:
            await opened.aclose()
        reopened = connect(database)
        try:
            await artifacts.prepare(reopened)
            assert await artifacts.content(reopened, kept.artifact) == (kept, FIRST.raw)
        finally:
            await reopened.aclose()


def context(call: str | None = "call-kept") -> RunContext[None]:
    return RunContext(deps=None, model=TestModel(), usage=RunUsage(), tool_call_id=call)


async def called(
    tools: FunctionToolset[None], name: str, arguments: dict[str, object], call: str = "call-kept"
) -> object:
    ctx = context(call)
    registered = await tools.get_tools(ctx)
    return await tools.call_tool(name, arguments, ctx, registered[name])


class TestTheTools:
    async def test_a_session_with_no_files_can_list_and_nothing_more(self, service: Service) -> None:
        tools = artifact_tools(Artifacts(service.database, "s" * 32, 0), None)
        assert set(await tools.get_tools(context())) == {"list_artifacts"}

    async def test_keeping_a_file_records_the_version_for_the_page(self, service: Service, tmp_path: Path) -> None:
        (tmp_path / "page.html").write_bytes(FIRST.raw)
        tools = artifact_tools(Artifacts(service.database, "s" * 32, 2), Files(roots=(Scratch(tmp_path),)))
        returned = await called(tools, "file_to_artifact", {"path": "page.html", "title": "The poll"})
        assert isinstance(returned, ToolReturn)
        listed = await artifacts.catalogue(service.database)
        assert returned.metadata == {KEPT: {"artifact": listed[0].artifact, "version": 1}}
        assert listed[0].made_by == Call(session="s" * 32, turn=2, call="call-kept")

    async def test_a_call_run_again_gets_its_version_without_reading_the_file(
        self, service: Service, tmp_path: Path
    ) -> None:
        source = tmp_path / "page.html"
        source.write_bytes(FIRST.raw)
        tools = artifact_tools(Artifacts(service.database, "s" * 32, 2), Files(roots=(Scratch(tmp_path),)))
        first = await called(tools, "file_to_artifact", {"path": "page.html"})
        source.unlink()
        again = await called(tools, "file_to_artifact", {"path": "page.html"})
        assert isinstance(first, ToolReturn)
        assert isinstance(again, ToolReturn)
        assert again.metadata == first.metadata

    @pytest.mark.parametrize(
        ("arguments", "why"),
        [
            ({"artifact": "7a" * 16}, "pass expected_version with artifact"),
            ({"expected_version": 1}, "pass artifact with it"),
            ({"artifact": "7a" * 16, "expected_version": 0}, "starts at 1"),
        ],
        ids=["artifact alone", "version alone", "version zero"],
    )
    async def test_an_update_named_by_half_is_sent_back(
        self, service: Service, tmp_path: Path, arguments: dict[str, object], why: str
    ) -> None:
        (tmp_path / "page.html").write_bytes(FIRST.raw)
        tools = artifact_tools(Artifacts(service.database, "s" * 32, 2), Files(roots=(Scratch(tmp_path),)))
        with pytest.raises(ModelRetry, match=why):
            await called(tools, "file_to_artifact", {"path": "page.html", **arguments})

    async def test_writing_a_version_out_is_its_exact_bytes(self, service: Service, tmp_path: Path) -> None:
        kept = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT)
        tools = artifact_tools(Artifacts(service.database, "s" * 32, 2), Files(roots=(Scratch(tmp_path),)))
        await called(tools, "artifact_to_file", {"artifact": kept.artifact, "path": "out/copy.html"})
        assert (tmp_path / "out" / "copy.html").read_bytes() == FIRST.raw

    async def test_writing_a_version_out_never_overwrites(self, service: Service, tmp_path: Path) -> None:
        kept = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT)
        (tmp_path / "copy.html").write_bytes(b"mine")
        tools = artifact_tools(Artifacts(service.database, "s" * 32, 2), Files(roots=(Scratch(tmp_path),)))
        with pytest.raises(ModelRetry, match="already exists"):
            await called(tools, "artifact_to_file", {"artifact": kept.artifact, "path": "copy.html"})
        assert (tmp_path / "copy.html").read_bytes() == b"mine"

    async def test_a_listing_never_carries_the_document(self, service: Service) -> None:
        await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT, title="The poll")
        tools = artifact_tools(Artifacts(service.database, "s" * 32, 2), None)
        listed = await called(tools, "list_artifacts", {})
        assert "The poll" in str(listed)
        assert "script" not in str(listed)


class TestTheCallLinksToWhatItKept:
    def test_the_summary_links_to_the_version_the_call_recorded(self) -> None:
        used = ToolUse(
            tool="file_to_artifact",
            arguments='{"path": "page.html"}',
            returned=Returned(
                "success", "The poll: artifact 7a, version 2 of 2", {KEPT: {"artifact": "7a", "version": 2}}
            ),
        )
        assert f'href="{LINKS.to_artifact("7a", 2)}"' in render(tool_block(LINKS, used, "panel-1-0", 0))

    def test_a_call_that_recorded_nothing_links_nowhere(self) -> None:
        used = ToolUse(tool="file_to_artifact", arguments='{"path": "page.html"}', returned=Returned("failed", "no"))
        assert "tool__kept" not in render(tool_block(LINKS, used, "panel-1-0", 0))


class TestThePages:
    async def test_the_catalogue_names_each_artifact_escaped(self, app: ASGIApp, service: Service) -> None:
        kept = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT, title="<The poll>")
        async with calling(app) as client:
            page = await client.get(LINKS.to_artifacts())
        assert page.status == 200
        assert "&lt;The poll&gt;" in page.text
        assert f'href="{LINKS.to_artifact(kept.artifact)}"' in page.text

    async def test_an_artifact_page_frames_the_version_it_is_at_in_a_sandbox(
        self, app: ASGIApp, service: Service
    ) -> None:
        first = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT)
        await artifacts.keep(service.database, SECOND, by("call-b"), KEPT_AT, Updating(first.artifact, 1))
        async with calling(app) as client:
            page = await client.get(LINKS.to_artifact(first.artifact, 1))
        assert f'sandbox="allow-scripts" src="{LINKS.to_artifact_content(first.artifact, 1)}"' in page.text
        assert f'href="{LINKS.to_artifact_download(first.artifact, 1)}"' in page.text

    async def test_an_artifact_page_links_to_the_turn_that_kept_it(self, app: ASGIApp, service: Service) -> None:
        kept = await artifacts.keep(service.database, FIRST, by("call-a", turn=4), KEPT_AT)
        async with calling(app) as client:
            page = await client.get(LINKS.to_artifact(kept.artifact))
        assert f'href="{LINKS.to_turn(kept.made_by.session, 4)}"' in page.text

    async def test_an_artifact_nothing_kept_is_a_page_saying_so(self, app: ASGIApp) -> None:
        async with calling(app) as client:
            page = await client.get(LINKS.to_artifact("nowhere"))
        assert page.status == 404
        assert "no artifact nowhere" in page.text

    async def test_the_preview_and_the_download_are_the_same_bytes(self, app: ASGIApp, service: Service) -> None:
        kept = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT)
        async with calling(app) as client:
            shown = await client.get(LINKS.to_artifact_content(kept.artifact, 1))
            saved = await client.get(LINKS.to_artifact_download(kept.artifact, 1))
        assert shown.body == saved.body == FIRST.raw

    @pytest.mark.parametrize("route", ["content", "download"])
    async def test_both_are_served_under_the_sandboxing_policy(
        self, app: ASGIApp, service: Service, route: str
    ) -> None:
        kept = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT)
        async with calling(app) as client:
            served = await client.get(f"/artifacts/{kept.artifact}/{route}?version=1")
        policy = served.headers["content-security-policy"]
        assert policy.startswith("sandbox allow-scripts;")
        assert "connect-src 'none'" in policy

    async def test_the_download_is_named_from_the_version_the_store_holds(self, app: ASGIApp, service: Service) -> None:
        kept = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT)
        async with calling(app) as client:
            saved = await client.get(LINKS.to_artifact_download(kept.artifact, 1))
        assert saved.headers["content-disposition"] == f'attachment; filename="artifact-{kept.artifact}-v1.html"'

    async def test_the_bytes_are_only_served_at_a_named_version(self, app: ASGIApp, service: Service) -> None:
        kept = await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT)
        async with calling(app) as client:
            unpinned = await client.get(f"/artifacts/{kept.artifact}/content")
        assert unpinned.status == 400

    async def test_the_dashboard_names_the_artifacts_most_recently_kept(self, app: ASGIApp, service: Service) -> None:
        await artifacts.keep(service.database, FIRST, by("call-a"), KEPT_AT, title="The poll")
        async with calling(app) as client:
            page = await client.get(LINKS.to_home())
        assert "The poll" in page.text
        assert f'href="{LINKS.to_artifacts()}"' in page.text

    async def test_a_dashboard_with_no_artifacts_draws_no_section_for_them(self, app: ASGIApp) -> None:
        async with calling(app) as client:
            page = await client.get(LINKS.to_home())
        assert f'href="{LINKS.to_artifacts()}"' not in page.text

    async def test_a_sessions_rail_lists_what_it_kept(self, app: ASGIApp, service: Service) -> None:
        session = await started(service, "draw the poll")
        kept = await artifacts.keep(
            service.database, FIRST, by("call-a", session=session.id), KEPT_AT, title="The poll"
        )
        async with calling(app) as client:
            page = await client.get(LINKS.to_session(session.id))
        assert f'href="{LINKS.to_artifact(kept.artifact, 1)}"' in page.text
