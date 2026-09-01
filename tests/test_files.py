from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai import ModelRetry

from mainplate.anchors import Anchored
from mainplate.anchors import Splice
from mainplate.anchors import Substitute
from mainplate.files import MAX_BYTES
from mainplate.files import Files
from mainplate.files import Refused
from mainplate.files import Text
from mainplate.files import file_tools
from mainplate.files import guarded

SOURCE = "def first():\n    return 1\n\n\ndef second():\n    return 2\n"


@pytest.fixture
def files(tmp_path: Path) -> Files:
    (tmp_path / "app.py").write_text(SOURCE)
    return Files(root=tmp_path)


def naming(files: Files, at: int) -> str:
    """The anchor of one line of the fixture file, read the way the tool would compute it."""
    found = Anchored.over(Text.of((files.root / "app.py").read_text()).lines).codes[at]
    assert found is not None
    return found


class TestStayingInsideTheWorkspace:
    @pytest.mark.parametrize(
        "escape",
        [
            pytest.param("../outside.txt", id="a relative path climbing out"),
            pytest.param("/etc/passwd", id="an absolute path replacing the root"),
            pytest.param("sub/../../outside.txt", id="a climb hidden mid-path"),
        ],
    )
    async def test_a_path_leaving_the_worktree_is_refused(self, files: Files, escape: str) -> None:
        with pytest.raises(Refused, match="outside this session's workspace"):
            await files.read(escape, 1, 10)

    async def test_a_symlink_pointing_out_of_the_worktree_is_refused(self, files: Files, tmp_path: Path) -> None:
        """
        The case only resolving catches. Comparing the joined path would see a name under the root
        and let the read through to wherever the link actually goes.
        """
        outside = tmp_path.parent / "secret.txt"
        outside.write_text("not yours\n")
        (files.root / "link.txt").symlink_to(outside)

        with pytest.raises(Refused, match="outside this session's workspace"):
            await files.read("link.txt", 1, 10)

    async def test_writing_outside_the_worktree_is_refused_too(self, files: Files) -> None:
        with pytest.raises(Refused, match="outside this session's workspace"):
            await files.create("../planted.txt", "anything")


class TestRefusingWhatCannotBeRead:
    async def test_a_missing_file_says_so(self, files: Files) -> None:
        with pytest.raises(Refused, match="there is no file at"):
            await files.read("absent.py", 1, 10)

    async def test_a_directory_is_not_a_file(self, files: Files) -> None:
        (files.root / "pkg").mkdir()
        with pytest.raises(Refused, match="is a directory"):
            await files.read("pkg", 1, 10)

    async def test_something_that_is_not_text_has_no_lines_to_anchor(self, files: Files) -> None:
        (files.root / "blob.bin").write_bytes(b"\xff\xfe\x00\x01binary")
        with pytest.raises(Refused, match="not UTF-8 text"):
            await files.read("blob.bin", 1, 10)

    async def test_something_far_too_large_is_refused_before_it_is_decoded(self, files: Files) -> None:
        (files.root / "huge.txt").write_bytes(b"x" * (MAX_BYTES + 1))
        with pytest.raises(Refused, match="too large to read"):
            await files.read("huge.txt", 1, 10)


class TestKeepingWhatSplittingLinesThrowsAway:
    def test_a_file_with_windows_endings_keeps_them(self) -> None:
        text = Text.of("one\r\ntwo\r\n")
        assert text.lines == ("one", "two")
        assert text.rejoined(text.lines) == "one\r\ntwo\r\n"

    def test_a_file_ending_without_a_newline_still_does(self) -> None:
        text = Text.of("one\ntwo")
        assert text.rejoined(text.lines) == "one\ntwo"

    def test_a_form_feed_is_content_rather_than_a_line_break(self) -> None:
        """`splitlines` breaks on one, so rejoining would silently edit a file nobody touched."""
        assert Text.of("one\n\x0ctwo\n").lines == ("one", "\x0ctwo")

    async def test_an_edit_leaves_the_endings_of_the_rest_of_the_file_alone(self, files: Files) -> None:
        (files.root / "crlf.py").write_text("alpha\r\nbeta\r\ngamma\r\n", newline="")
        found = Anchored.over(("alpha", "beta", "gamma")).codes[1]
        assert found is not None

        await files.edit("crlf.py", [Substitute(op="substitute", at=found, find="beta", replace="middle")])

        assert (files.root / "crlf.py").read_bytes() == b"alpha\r\nmiddle\r\ngamma\r\n"


class TestReadingAFile:
    async def test_the_whole_file_comes_back_with_a_line_count(self, files: Files) -> None:
        shown = await files.read("app.py", 1, 100)
        assert shown.startswith("app.py, 6 lines")
        assert f"{naming(files, 0)} def first():" in shown

    async def test_a_partial_read_says_where_it_stopped(self, files: Files) -> None:
        shown = await files.read("app.py", 2, 2)
        assert shown.startswith("app.py, lines 2-3 of 6; pass `offset` to read further")
        assert "def second():" not in shown

    async def test_offset_counts_from_one(self, files: Files) -> None:
        assert f"{naming(files, 1)}     return 1" in await files.read("app.py", 2, 1)


class TestEditingAFile:
    async def test_the_change_reaches_disk(self, files: Files) -> None:
        await files.edit("app.py", [Substitute(op="substitute", at=naming(files, 1), find="1", replace="42")])
        assert (files.root / "app.py").read_text() == SOURCE.replace("return 1", "return 42")

    async def test_the_reply_shows_the_changed_region_with_fresh_anchors(self, files: Files) -> None:
        said = await files.edit("app.py", [Substitute(op="substitute", at=naming(files, 1), find="1", replace="42")])
        assert said.startswith("edited app.py, now 6 lines")
        assert f"{naming(files, 1)}     return 42" in said

    async def test_a_refused_edit_writes_nothing(self, files: Files) -> None:
        with pytest.raises(ModelRetry):
            await guarded(files.edit("app.py", [Substitute(op="substitute", at="zzzz", find="x", replace="y")]))
        assert (files.root / "app.py").read_text() == SOURCE

    async def test_a_span_ending_before_a_line_swallows_the_blanks_above_it(self, files: Files) -> None:
        await files.edit("app.py", [Splice(op="splice", from_=naming(files, 0), before=naming(files, 4), text="")])
        assert (files.root / "app.py").read_text() == "def second():\n    return 2\n"


class TestCreatingAFile:
    async def test_a_new_file_is_written_and_shown_back(self, files: Files) -> None:
        said = await files.create("sub/deep/new.py", "print('hi')")

        assert (files.root / "sub" / "deep" / "new.py").read_text() == "print('hi')\n"
        assert said.startswith("created sub/deep/new.py, 1 line")

    async def test_an_existing_path_is_refused_rather_than_overwritten(self, files: Files) -> None:
        with pytest.raises(Refused, match="never overwrites"):
            await files.create("app.py", "clobbered")
        assert (files.root / "app.py").read_text() == SOURCE

    async def test_a_missing_trailing_newline_is_supplied(self, files: Files) -> None:
        await files.create("terse.txt", "no newline here")
        assert (files.root / "terse.txt").read_text().endswith("\n")


class TestHowARefusalReachesTheModel:
    async def test_a_refusal_becomes_something_the_model_is_told(self, files: Files) -> None:
        """
        A `ModelRetry` puts the sentence in front of the model as its own call's result, where it
        can act on it. Anything else would fail the whole turn over a stale anchor.
        """
        with pytest.raises(ModelRetry, match="there is no file at"):
            await guarded(files.read("absent.py", 1, 10))

    async def test_a_genuine_fault_is_left_alone(self, files: Files) -> None:
        """Not everything is retryable, and a bug here is not something a model can retry past."""

        async def broken() -> str:
            raise ZeroDivisionError("a real fault")

        with pytest.raises(ZeroDivisionError):
            await guarded(broken())


class TestWhatTheToolsetOffers:
    def test_it_offers_exactly_read_edit_and_create(self, files: Files) -> None:
        """No `write`: a tool that overwrites a whole file is the escape hatch from anchored editing."""
        assert set(file_tools(files).tools) == {"read", "edit", "create"}

    def test_the_edit_schema_spells_from_under_its_own_name(self, files: Files) -> None:
        """`from_` is what Python allows; `from` is what the model has to write."""
        schema = file_tools(files).tools["edit"].function_schema.json_schema
        assert "from" in schema["$defs"]["Splice"]["properties"]
        assert "from_" not in schema["$defs"]["Splice"]["properties"]

    def test_the_two_shapes_of_operation_are_told_apart_by_op(self, files: Files) -> None:
        schema = file_tools(files).tools["edit"].function_schema.json_schema
        assert schema["$defs"]["Operation"]["discriminator"]["propertyName"] == "op"
