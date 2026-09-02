from __future__ import annotations

import asyncio
import subprocess
from inspect import cleandoc
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry

from mainplate.tools.files.anchors import GUTTER
from mainplate.tools.files.anchors import Anchored
from mainplate.tools.files.anchors import Splice
from mainplate.tools.files.anchors import Substitute
from mainplate.tools.files.tools import MAX_BYTES
from mainplate.tools.files.tools import MAX_ROWS
from mainplate.tools.files.tools import Files
from mainplate.tools.files.tools import Refused
from mainplate.tools.files.tools import Scratch
from mainplate.tools.files.tools import Text
from mainplate.tools.files.tools import Worktree
from mainplate.tools.files.tools import catalogue
from mainplate.tools.files.tools import catalogued
from mainplate.tools.files.tools import file_tools
from mainplate.tools.files.tools import guarded

SOURCE = "def first():\n    return 1\n\n\ndef second():\n    return 2\n"


@pytest.fixture
def files(tmp_path: Path) -> Files:
    (tmp_path / "app.py").write_text(SOURCE)
    return Files(roots=(Worktree(path=tmp_path),))


def naming(files: Files, at: int) -> str:
    """The anchor of one line of the fixture file, read the way the tool would compute it."""
    found = Anchored.over(Text.of((files.roots[0].path / "app.py").read_text()).lines).codes[at]
    assert found is not None
    return found


class TestTwoCallsAtOneFileAtOnce:
    """
    A model emits several tool calls in one response and they run concurrently.

    Aimed at one file that used to interleave: each call read, each computed against what it read,
    each wrote, and the loser's work vanished while *both* calls reported success. Every case here
    is repeated, because the old behaviour lost a write only sometimes and one round of a race
    proves nothing about a race.
    """

    ROUNDS = 20

    async def test_two_edits_to_different_lines_both_survive(self, files: Files) -> None:
        target = files.roots[0].path / "app.py"
        anchored = Anchored.over(("alpha", "bravo", "charlie"))
        first, third = anchored.codes[0], anchored.codes[2]
        assert first is not None
        assert third is not None

        for _ in range(self.ROUNDS):
            target.write_text("alpha\nbravo\ncharlie\n")

            await asyncio.gather(
                files.edit("app.py", [Substitute(op="substitute", at=first, find="alpha", replace="ALPHA")]),
                files.edit("app.py", [Substitute(op="substitute", at=third, find="charlie", replace="CHARLIE")]),
            )

            after = target.read_text()
            assert "ALPHA" in after, "the first edit was lost"
            assert "CHARLIE" in after, "the second edit was lost"

    async def test_two_creates_of_one_path_leave_exactly_one_refusal(self, files: Files) -> None:
        """
        `create` promises never to overwrite, and checked outside the lock it silently did.

        Both callers saw a path that was not there yet, both wrote, and the second replaced the
        first while telling the model it had created something.
        """
        for at in range(self.ROUNDS):
            done = await asyncio.gather(
                files.create(f"made-{at}.txt", "from the first\n"),
                files.create(f"made-{at}.txt", "from the second\n"),
                return_exceptions=True,
            )

            refusals = [each for each in done if isinstance(each, Refused)]
            assert len(refusals) == 1, "exactly one of the two should be told it already exists"
            assert "already exists" in str(refusals[0])


class TestReachingTheScratchDirectory:
    """
    The second place a session may touch, and the three ways it differs from the worktree.

    Named absolutely rather than reached relatively, covered by `read`/`edit`/`create` but not by
    `list`, and still bounded: somewhere out of reach is refused exactly as it was before.
    """

    @pytest.fixture
    def reaching(self, tmp_path: Path) -> Files:
        scratch = tmp_path.parent / "scratch-for-session"
        scratch.mkdir(exist_ok=True)
        (tmp_path / "app.py").write_text(SOURCE)
        return Files(roots=(Worktree(path=tmp_path), Scratch(path=scratch)))

    async def test_a_file_there_can_be_created_read_and_edited(self, reaching: Files) -> None:
        where = str(reaching.roots[1].path / "plan.md")

        await reaching.create(where, "one\ntwo\n")
        said = await reaching.read(where, 1, 10)

        assert "one" in said
        assert (reaching.roots[1].path / "plan.md").read_text() == "one\ntwo\n"

    def test_a_relative_path_still_means_the_repository(self, reaching: Files) -> None:
        """
        The asymmetry that keeps every path a model already writes meaning what it meant.

        A bare name is about the repository, so adding a second reachable root must not make it
        ambiguous or let it drift somewhere else.
        """
        found = reaching.resolved("app.py")

        assert found.path == reaching.roots[0].path.resolve() / "app.py"
        assert found.root == reaching.roots[0], "and it says which root, so nothing re-checks"

    async def test_list_refuses_it_and_says_what_does_answer(self, reaching: Files) -> None:
        """
        `list` asks git, and the scratch is deliberately not in git.

        Refused by the *root* rather than by a check in the tool, and refused rather than left to
        `entries`, where "not a repository" would arrive as a fault and end the turn instead of
        telling the model to reach for `bash`.
        """
        with pytest.raises(Refused, match="which `list` does not read"):
            await reaching.listing(str(reaching.roots[1].path), 2)

    async def test_somewhere_reachable_by_neither_is_still_refused(self, reaching: Files) -> None:
        with pytest.raises(Refused, match="outside this session's workspace"):
            await reaching.read("/etc/passwd", 1, 10)


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
        (files.roots[0].path / "link.txt").symlink_to(outside)

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
        (files.roots[0].path / "pkg").mkdir()
        with pytest.raises(Refused, match="is a directory"):
            await files.read("pkg", 1, 10)

    async def test_something_that_is_not_text_has_no_lines_to_anchor(self, files: Files) -> None:
        (files.roots[0].path / "blob.bin").write_bytes(b"\xff\xfe\x00\x01binary")
        with pytest.raises(Refused, match="not UTF-8 text"):
            await files.read("blob.bin", 1, 10)

    async def test_something_far_too_large_is_refused_before_it_is_decoded(self, files: Files) -> None:
        (files.roots[0].path / "huge.txt").write_bytes(b"x" * (MAX_BYTES + 1))
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
        (files.roots[0].path / "crlf.py").write_text("alpha\r\nbeta\r\ngamma\r\n", newline="")
        found = Anchored.over(("alpha", "beta", "gamma")).codes[1]
        assert found is not None

        await files.edit("crlf.py", [Substitute(op="substitute", at=found, find="beta", replace="middle")])

        assert (files.roots[0].path / "crlf.py").read_bytes() == b"alpha\r\nmiddle\r\ngamma\r\n"


class TestBuildingATreeFromFlatPaths:
    """
    `git ls-files` answers with one full path per entry and no structure at all, so the tree is
    made here. Pure, so these need no repository and no disk.
    """

    FOUND = ("README.md", "src/demo/app.py", "src/demo/tools/read.py", "src/demo/tools/write.py", "tests/test_app.py")

    def test_a_directory_at_the_asked_depth_is_counted_rather_than_opened(self) -> None:
        assert list(catalogue(self.FOUND, depth=1, level=0)) == ["README.md", "src/ (3 files)", "tests/ (1 file)"]

    def test_a_deeper_call_opens_one_more_level(self) -> None:
        assert list(catalogue(self.FOUND, depth=2, level=0)) == [
            "README.md",
            "src/",
            "  demo/ (3 files)",
            "tests/",
            "  test_app.py",
        ]

    def test_a_levels_own_files_come_before_its_subdirectories(self) -> None:
        """
        Otherwise a level's own files arrive after everything nested below it, so the files at the
        root of a deep repository land at the very bottom of the answer, furthest from the line
        that names where they are.
        """
        rows = list(catalogue(("z.py", "a/deep/one.py", "a/deep/two.py"), depth=3, level=0))
        assert rows.index("z.py") < rows.index("a/")

    def test_an_empty_listing_says_so_rather_than_showing_a_bare_header(self) -> None:
        assert catalogued(".", (), depth=2) == ". holds no files git knows about"

    def test_a_listing_past_the_row_cap_is_cut_and_says_what_to_do(self) -> None:
        many = tuple(f"pkg/mod{at}.py" for at in range(MAX_ROWS + 50))
        said = catalogued(".", many, depth=2)
        assert f"the first {MAX_ROWS} rows" in said
        assert "smaller `depth`" in said
        assert len(said.splitlines()) == MAX_ROWS + 2


class TestListingADirectory:
    @pytest.fixture
    def repository(self, files: Files) -> Files:
        (files.roots[0].path / ".gitignore").write_text("build/\n")
        (files.roots[0].path / "build").mkdir()
        (files.roots[0].path / "build" / "out.js").write_text("")
        (files.roots[0].path / "pkg").mkdir()
        (files.roots[0].path / "pkg" / "deep.py").write_text("")
        subprocess.run(["git", "init", "-q"], cwd=files.roots[0].path, check=True)
        return files

    async def test_it_shows_what_is_there(self, repository: Files) -> None:
        said = await repository.listing(".", 2)
        assert "app.py" in said
        assert "pkg/" in said
        assert "deep.py" in said

    async def test_an_ignored_directory_is_left_out(self, repository: Files) -> None:
        """
        The reason this asks git rather than walking. A worktree usually carries an installed
        environment or a build directory, and one of those listed in full is tens of thousands of
        paths spent before the model has asked its first real question.
        """
        said = await repository.listing(".", 3)
        assert "build" not in said
        assert "out.js" not in said

    async def test_a_file_created_but_never_committed_still_shows(self, repository: Files) -> None:
        """`--others` is what covers this, and it is the case the agent hits most: its own work."""
        (repository.roots[0].path / "brand_new.py").write_text("")
        assert "brand_new.py" in await repository.listing(".", 1)

    async def test_a_subdirectory_is_listed_relative_to_itself(self, repository: Files) -> None:
        assert await repository.listing("pkg", 1) == "pkg, 1 file within 1 level\n\ndeep.py"

    async def test_a_file_is_not_a_directory(self, repository: Files) -> None:
        with pytest.raises(Refused, match="`read` is what opens one"):
            await repository.listing("app.py", 1)

    async def test_a_missing_directory_says_so(self, repository: Files) -> None:
        with pytest.raises(Refused, match="there is no directory at"):
            await repository.listing("absent", 1)

    async def test_a_path_leaving_the_worktree_is_refused(self, repository: Files) -> None:
        with pytest.raises(Refused, match="outside this session's workspace"):
            await repository.listing("..", 1)


class TestReadingAFile:
    async def test_the_whole_file_comes_back_with_a_line_count(self, files: Files) -> None:
        shown = await files.read("app.py", 1, 100)
        assert shown.startswith("app.py, 6 lines")
        assert f"{naming(files, 0)}{GUTTER}def first():" in shown

    async def test_a_partial_read_says_where_it_stopped(self, files: Files) -> None:
        shown = await files.read("app.py", 2, 2)
        assert shown.startswith("app.py, lines 2-3 of 6; pass `offset` to read further")
        assert "def second():" not in shown

    async def test_offset_counts_from_one(self, files: Files) -> None:
        assert f"{naming(files, 1)}{GUTTER}    return 1" in await files.read("app.py", 2, 1)


class TestEditingAFile:
    async def test_the_change_reaches_disk(self, files: Files) -> None:
        await files.edit("app.py", [Substitute(op="substitute", at=naming(files, 1), find="1", replace="42")])
        assert (files.roots[0].path / "app.py").read_text() == SOURCE.replace("return 1", "return 42")

    async def test_the_reply_shows_the_changed_region_with_fresh_anchors(self, files: Files) -> None:
        said = await files.edit("app.py", [Substitute(op="substitute", at=naming(files, 1), find="1", replace="42")])
        assert said.startswith("edited app.py, now 6 lines")
        assert f"{naming(files, 1)}{GUTTER}    return 42" in said

    async def test_a_refused_edit_writes_nothing(self, files: Files) -> None:
        with pytest.raises(ModelRetry):
            await guarded(files.edit("app.py", [Substitute(op="substitute", at="zzzz", find="x", replace="y")]))
        assert (files.roots[0].path / "app.py").read_text() == SOURCE

    async def test_a_span_ending_before_a_line_swallows_the_blanks_above_it(self, files: Files) -> None:
        await files.edit("app.py", [Splice(op="splice", from_=naming(files, 0), before=naming(files, 4), text="")])
        assert (files.roots[0].path / "app.py").read_text() == "def second():\n    return 2\n"


class TestCreatingAFile:
    async def test_a_new_file_is_written_and_shown_back(self, files: Files) -> None:
        said = await files.create("sub/deep/new.py", "print('hi')")

        assert (files.roots[0].path / "sub" / "deep" / "new.py").read_text() == "print('hi')\n"
        assert said.startswith("created sub/deep/new.py, 1 line")

    async def test_an_existing_path_is_refused_rather_than_overwritten(self, files: Files) -> None:
        with pytest.raises(Refused, match="never overwrites"):
            await files.create("app.py", "clobbered")
        assert (files.roots[0].path / "app.py").read_text() == SOURCE

    async def test_a_missing_trailing_newline_is_supplied(self, files: Files) -> None:
        await files.create("terse.txt", "no newline here")
        assert (files.roots[0].path / "terse.txt").read_text().endswith("\n")


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
    def test_it_offers_exactly_list_read_edit_and_create(self, files: Files) -> None:
        """No `write`: a tool that overwrites a whole file is the escape hatch from anchored editing."""
        assert set(file_tools(files).tools) == {"list", "read", "edit", "create"}

    def test_the_listing_tool_is_asked_for_as_list(self, files: Files) -> None:
        """
        The function is `listing` because `list` is a builtin and shadowing one here is a lint
        error, but the name a model reaches for is the one that has to be right, so it is set
        explicitly at registration rather than left to follow the function.
        """
        assert "listing" not in file_tools(files).tools

    def test_the_edit_schema_spells_from_under_its_own_name(self, files: Files) -> None:
        """`from_` is what Python allows; `from` is what the model has to write."""
        schema = file_tools(files).tools["edit"].function_schema.json_schema
        assert "from" in schema["$defs"]["Splice"]["properties"]
        assert "from_" not in schema["$defs"]["Splice"]["properties"]

    async def test_the_worked_example_is_what_a_read_really_returns(self, files: Files) -> None:
        """
        The one place a description can be worse than none. `read` teaches the gutter by showing a
        rendered file, so an example that drifted from the renderer would be teaching a format the
        tool does not emit: the misreading the gutter exists to prevent, reintroduced by the text
        meant to prevent it.

        The file is recovered from the example by stripping its own gutter rather than kept here as
        a second copy, so there is nothing to hold in step with anything: whatever the docstring
        shows is written, read back, and compared against itself.
        """
        described = file_tools(files).tools["read"].function.__doc__
        assert described is not None
        example = cleandoc(described).split("```")[1].strip("\n")

        header, body = example.split("\n\n", 1)
        path = header.split(",")[0]
        (files.roots[0].path / path).write_text("\n".join(line.split(GUTTER, 1)[1] for line in body.split("\n")) + "\n")

        assert await files.read(path, 1, 100) == example

    def test_the_two_shapes_of_operation_are_told_apart_by_op(self, files: Files) -> None:
        schema = file_tools(files).tools["edit"].function_schema.json_schema
        assert schema["$defs"]["Operation"]["discriminator"]["propertyName"] == "op"
