"""The index, manual invocation and read-only roots of skills and commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from calling import calling
from conftest import DEFAULT_CHOICE
from conftest import INSTRUCTIONS
from conftest import Provider
from conftest import already
from conftest import passing
from conftest import started
from without_asgi import Inventory

from mainplate.agent import reaching
from mainplate.app import build_app
from mainplate.context import BadContext
from mainplate.context import Entry
from mainplate.context import body
from mainplate.context import discover
from mainplate.context import expanded
from mainplate.context import index
from mainplate.context import leaders
from mainplate.context import parse_catalogue
from mainplate.context import skill_roots
from mainplate.conversation import CONTEXT_KEY
from mainplate.conversation import conversing
from mainplate.pages.composer import context_answers
from mainplate.sandbox import Filesystem
from mainplate.sandbox import Isolation
from mainplate.sandbox import confined_by
from mainplate.service import Service
from mainplate.tools.files.tools import Files
from mainplate.tools.files.tools import Refused
from mainplate.tools.files.tools import Skills


def write(root: Path, kind: str, name: str, text: str) -> Path:
    """Put one entry in the layout discovery understands."""
    path = root / kind / name / ("SKILL.md" if kind == "skills" else "COMMAND.md")
    path.parent.mkdir(parents=True)
    path.write_text(f"---\ndescription: Do {name}\n---\n{text}")
    return path


def test_the_index_discloses_only_skills_and_not_their_bodies(tmp_path: Path) -> None:
    """The model learns what it can read, not what the person can invoke alone."""
    skill = write(tmp_path, "skills", "review", "Review these changes")
    write(tmp_path, "commands", "release", "Release with care")
    entries = discover(tmp_path, "user")

    listed = index(entries)

    assert "user:review" in listed
    assert "Do review" in listed
    assert "Review these changes" not in listed
    assert "release" not in listed
    assert skill.read_text() == body(entries[0], None)


def test_a_manual_invocation_records_the_body_it_sends(tmp_path: Path) -> None:
    """An edit after invocation does not alter the message already delivered."""
    path = write(tmp_path, "skills", "review", "Check the patch")
    entry = discover(tmp_path, "user")[0]
    sent = expanded(entry, body(entry, None), "Focus on tests")
    path.write_text("---\ndescription: Do review\n---\nDo something else")
    assert "Check the patch" in sent
    assert "Focus on tests" in sent
    assert "Do something else" not in sent
    assert parse_catalogue([entry.model_dump()]) == (entry,)


def test_an_ambiguous_bare_leader_is_not_offered(tmp_path: Path) -> None:
    """Both tier-qualified names remain usable without silently choosing a winner."""
    first = Entry(kind="skill", tier="user", name="review", description="First", path="/first")
    second = Entry(kind="skill", tier="repository", name="review", description="Second", path="/second")
    offered = leaders((first, second), frozenset({"run"}))
    assert set(offered) == {"user:review", "repository:review"}


def test_an_existing_composer_leader_keeps_its_meaning() -> None:
    """The qualified form remains usable if a directory is called run."""
    entry = Entry(kind="command", tier="user", name="run", description="Something", path="/somewhere")
    assert set(leaders((entry,), frozenset({"run"}))) == {"user:run"}


def test_the_composer_offers_one_direct_leader_per_context_entry() -> None:
    """A qualified spelling remains resolvable without filling the menu with duplicates."""
    entry = Entry(kind="command", tier="user", name="release", description="Ship", path="/release")
    assert [answer.leader for answer in context_answers((entry,), ())] == ["release"]


def test_a_repository_link_cannot_be_read_as_context(tmp_path: Path) -> None:
    """An entry symlink cannot escape the checkout when a person invokes it."""
    checkout = tmp_path / "checkout"
    outside = tmp_path / "outside"
    outside.write_text("secret")
    path = write(checkout / ".mainplate", "skills", "review", "Safe")
    entry = discover(checkout / ".mainplate", "repository")[0]
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(OSError, match=r"link|Symbolic"):
        body(entry, checkout)


@pytest.mark.asyncio
async def test_a_skill_root_is_readable_but_never_writable(tmp_path: Path) -> None:
    """File tools enforce read-only reach without relying on bwrap's mount flag."""
    write(tmp_path, "skills", "review", "Review")
    entries = discover(tmp_path, "user")
    assert skill_roots(entries) == (("user", tmp_path / "skills"),)
    files = Files(roots=(Skills(tmp_path / "skills", label="user_skills", allowed=frozenset({"review"})),))
    assert "Review" in await files.read("review/SKILL.md", 1, 30, "user_skills")
    with pytest.raises(Refused, match="read-only"):
        await files.create("review/other.md", "Not allowed", "user_skills")
    with pytest.raises(Refused, match="read-only"):
        await files.edit("review/SKILL.md", (), "user_skills")
    with pytest.raises(Refused, match="read-only"):
        await files.create_bytes("review/other.md", b"Not allowed", "user_skills")
    with pytest.raises(Refused, match="advertised"):
        await files.read("other/SKILL.md", 1, 30, "user_skills")


def test_a_skill_is_bound_read_only_in_the_session_sandbox(tmp_path: Path) -> None:
    """The shell sees the same skill root as `read`, but cannot write through it."""
    write(tmp_path, "skills", "review", "Read me")
    found = reaching(
        Isolation(filesystem=Filesystem.NOTHING),
        scratch=tmp_path / "scratch",
        bwrap="/usr/bin/bwrap",
        context=discover(tmp_path, "user"),
    )
    assert found.confinement is not None
    binds = confined_by(found.confinement).places
    assert any(bind.name == "user_skills" and not bind.writable for bind in binds)
    assert any(isinstance(root, Skills) and root.name == "user_skills" for root in found.roots)


@pytest.mark.asyncio
async def test_first_pass_records_the_index_and_not_its_body(tmp_path: Path, service: Service) -> None:
    """A second pass replays discovery even when a skill changes on disk."""
    path = write(tmp_path / "mainplate", "skills", "review", "Read the patch")
    session = await service.start(DEFAULT_CHOICE)
    loop = conversing(Provider().endpoints(), INSTRUCTIONS, config_home=tmp_path)
    await passing(service, session.id, loop)
    recorded = await service.checkpointer.load(session.id)
    assert CONTEXT_KEY in recorded
    assert "Read the patch" not in str(recorded[CONTEXT_KEY])
    path.write_text("---\ndescription: Different\n---\nDifferent body")
    await passing(service, session.id, loop)
    found = await service.read(session.id)
    assert found is not None
    assert next(entry for entry in found.context if entry.tier == "user").description == "Do review"
    assert any(entry.tier == "bundled" and entry.name == "review" for entry in found.context)


@pytest.mark.asyncio
async def test_manual_invocation_delivers_the_expansion_to_the_checkpoint(tmp_path: Path, service: Service) -> None:
    """The service sends the selected skill, not merely its leader or a path."""
    path = write(tmp_path, "skills", "review", "Inspect the tests")
    entry = discover(tmp_path, "user")[0]
    session = await started(service, "hello", DEFAULT_CHOICE)
    await service.checkpointer.supply(session.id, CONTEXT_KEY, [entry.model_dump()])
    found = await service.read(session.id)
    assert found is not None
    assert await service.invoke_context(session.id, found, "review", "Focus on errors")
    recorded = await service.checkpointer.load(session.id)
    assert "Inspect the tests" in str(recorded)
    assert "Focus on errors" in str(recorded)
    path.write_text("Changed")
    assert "Changed" not in str(recorded)


@pytest.mark.asyncio
async def test_a_command_post_uses_its_direct_leader(tmp_path: Path, service: Service, assets: Inventory) -> None:
    """The composer shows the directory's leader, and its post delivers the expanded body."""
    write(tmp_path, "commands", "release", "Ship carefully")
    entry = discover(tmp_path, "user")[0]
    session = await started(service, "hello", DEFAULT_CHOICE)
    await service.checkpointer.supply(session.id, CONTEXT_KEY, [entry.model_dump()])
    async with calling(build_app(already(service), assets)) as caller:
        page = await caller.get(f"/sessions/{session.id}")
        assert "/release" in page.text
        posted = await caller.post(
            f"/sessions/{session.id}/messages",
            {"prompt": "Try staging", "disposition": "context:release"},
        )
    assert posted.status == 200
    recorded = await service.checkpointer.load(session.id)
    assert "Ship carefully" in str(recorded)
    assert "Try staging" in str(recorded)


def test_the_directory_name_is_the_leader(tmp_path: Path) -> None:
    """A frontmatter name does not override the directory name."""
    write(tmp_path, "commands", "release", "Ship")
    assert "release" in leaders(discover(tmp_path, "user"), frozenset())
    write(tmp_path, "skills", "release", "Review")
    with pytest.raises(BadContext, match="same name"):
        discover(tmp_path, "user")
