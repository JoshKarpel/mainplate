from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mainplate.snapshots import Worktree
from mainplate.tools.files.anchors import GUTTER
from mainplate.tools.files.anchors import Anchored
from mainplate.tools.files.anchors import Substitute
from mainplate.tools.files.tools import Files
from mainplate.tools.files.tools import GitTracked
from mainplate.tools.files.tools import Refused
from mainplate.tools.files.tools import Scratch
from mainplate.tools.files.tools import Text
from mainplate.tools.grep.tools import grep_tools
from mainplate.tools.grep.tools import regions
from mainplate.tools.grep.tools import searched


@pytest.fixture
def repository(tmp_path: Path) -> Files:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "build").mkdir()
    (tmp_path / ".gitignore").write_text("build/\n")
    (tmp_path / "app.py").write_text("first needle\nkeep\nsecond needle\n")
    (tmp_path / "pkg" / "other.py").write_text("before\nneedle\nafter\n")
    (tmp_path / "notes.txt").write_text("needle in prose\n")
    (tmp_path / "build" / "ignored.py").write_text("needle in ignored output\n")
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe needle")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return Files(roots=(GitTracked(worktree=Worktree(root=tmp_path)),))


def anchor_of(files: Files, path: str, at: int) -> str:
    text = Text.of((files.roots[0].path / path).read_text())
    found = Anchored.over(text.lines).codes[at]
    assert found is not None
    return found


class TestSearchingARepository:
    async def test_matches_have_the_whole_file_anchors_edit_accepts(self, repository: Files) -> None:
        said = await searched(repository, "needle", path="app.py", context=0)

        assert f"{anchor_of(repository, 'app.py', 0)}{GUTTER}first needle" in said
        assert f"{anchor_of(repository, 'app.py', 2)}{GUTTER}second needle" in said
        assert "keep" not in said

        second = next(line.split(GUTTER, 1)[0] for line in said.splitlines() if line.endswith("second needle"))
        await repository.edit("app.py", [Substitute(op="substitute", at=second, find="needle", replace="changed")])

        assert (repository.roots[0].path / "app.py").read_text().endswith("second changed\n")

    async def test_a_directory_search_obeys_gitignore_and_skips_unreadable_files(self, repository: Files) -> None:
        said = await searched(repository, "needle", context=0)

        assert "app.py" in said
        assert "pkg/other.py" in said
        assert "notes.txt" in said
        assert "ignored.py" not in said
        assert "skipped 1 file" in said

    async def test_a_glob_narrows_the_files(self, repository: Files) -> None:
        said = await searched(repository, "needle", glob="*.py", context=0)

        assert "app.py" in said
        assert "pkg/other.py" in said
        assert "notes.txt" not in said

    async def test_a_limit_is_stated_and_stops_the_output(self, repository: Files) -> None:
        said = await searched(repository, "needle", path="app.py", context=0, limit=1)

        assert "first 1 matching line" in said
        assert "narrow `pattern`, `path`, or `glob`" in said
        assert "first needle" in said
        assert "second needle" not in said

    async def test_an_invalid_regular_expression_is_refused(self, repository: Files) -> None:
        with pytest.raises(Refused, match="not a valid regular expression"):
            await searched(repository, "[")

    async def test_a_non_repository_root_points_to_bash(self, tmp_path: Path) -> None:
        files = Files(roots=(Scratch(path=tmp_path),))

        with pytest.raises(Refused, match="use `bash`"):
            await searched(files, "needle")


class TestRenderingSearchContext:
    def test_overlapping_context_is_rendered_once(self) -> None:
        assert regions((2, 4), total=8, context=1) == ((1, 6),)

    def test_separate_context_stays_separate(self) -> None:
        assert regions((1, 6), total=8, context=1) == ((0, 3), (5, 8))


class TestWhatTheToolsetOffers:
    def test_it_offers_grep(self, repository: Files) -> None:
        assert set(grep_tools(repository).tools) == {"grep"}

    def test_the_schema_bounds_context_and_matches(self, repository: Files) -> None:
        schema = grep_tools(repository).tools["grep"].function_schema.json_schema

        assert schema["properties"]["context"]["minimum"] == 0
        assert schema["properties"]["context"]["maximum"] == 3
        assert schema["properties"]["limit"]["minimum"] == 1
        assert schema["properties"]["limit"]["maximum"] == 100
