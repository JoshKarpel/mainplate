"""
The bundled skills, and the script that writes what they cannot be trusted to restate.

A bundled skill is prose a model reads in somebody else's repository, so nothing runs it; what can be
held is that it names only files that exist and that what it names as generated is what
`scripts/skills.py` writes. Whether the generated files are current is the pre-commit hook's to
fail, since it rewrites them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mainplate.context import discover
from scripts.skills import GENERATED
from scripts.skills import SKILLS
from scripts.skills import generated
from scripts.skills import literal
from scripts.skills import section

BUNDLED = SKILLS.parent
WRITTEN = frozenset(each.path for each in generated())
SKILL_DIRECTORIES = tuple(sorted(path for path in SKILLS.iterdir() if path.is_dir()))

# A generated file named in prose: `generated/x`, or `skill/generated/x` for another skill's.
NAMED_GENERATED = re.compile(rf"`((?:[a-z]+/)?{GENERATED}/[\w.-]+)`")


def test_every_bundled_entry_is_discoverable() -> None:
    """A skill or command missing its description stops discovery, so it fails here first."""
    found = discover(BUNDLED, "bundled")

    assert {entry.name for entry in found if entry.kind == "skill"} == {path.name for path in SKILL_DIRECTORIES}


@pytest.mark.parametrize("skill", SKILL_DIRECTORIES, ids=lambda path: path.name)
def test_a_skill_names_only_generated_files_the_script_writes(skill: Path) -> None:
    named = NAMED_GENERATED.findall((skill / "SKILL.md").read_text(encoding="utf-8"))

    missing = [each for each in named if Path(each) not in WRITTEN and Path(skill.name, each) not in WRITTEN]

    assert not missing


@pytest.mark.parametrize("skill", SKILL_DIRECTORIES, ids=lambda path: path.name)
def test_every_file_beside_a_skill_is_named_by_it(skill: Path) -> None:
    """A supporting file nothing points at is one no model reads."""
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    beside = [
        path.relative_to(skill).as_posix()
        for path in skill.rglob("*")
        if path.is_file() and path.name != "SKILL.md" and "__pycache__" not in path.parts
    ]

    assert [each for each in beside if f"`{each}`" not in text] == []


def test_a_section_runs_to_the_next_heading_as_high(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("## Before\n\nskip\n\n### Wanted\n\nkept\n\n#### Deeper\n\nalso kept\n\n### After\n\nnot kept\n")

    taken = section(note, "### Wanted")

    assert taken == "### Wanted\n\nkept\n\n#### Deeper\n\nalso kept\n"


def test_a_section_ignores_a_heading_inside_a_fence(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("### Wanted\n\n```sh\n# a comment, not a heading\n```\n\nkept\n\n### After\n")

    taken = section(note, "### Wanted")

    assert "kept" in taken
    assert "After" not in taken


def test_a_literal_is_read_from_an_annotated_assignment(tmp_path: Path) -> None:
    module = tmp_path / "module.py"
    module.write_text('from typing import Final\nOTHER = 1\nNAMES: Final = ("ALPHA.md", "BETA.md")\n')

    assert literal(module, "NAMES") == ("ALPHA.md", "BETA.md")


def test_a_computed_value_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    module = tmp_path / "module.py"
    module.write_text("SIZE = 2 * 1024\n")

    with pytest.raises(ValueError, match="malformed"):
        literal(module, "SIZE")
