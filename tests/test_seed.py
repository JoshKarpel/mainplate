from __future__ import annotations

from pathlib import Path

from conftest import DEFAULT_CHOICE

from mainplate.app import open_store
from mainplate.artifacts import history
from mainplate.catalogue import Catalogues
from mainplate.conversation import wrote_key
from mainplate.sessions import read_sessions
from scripts.gallery import CAPTIONS
from scripts.gallery import CATALOGUE
from scripts.gallery import FIXTURES
from scripts.gallery import POLL_ARTIFACT
from scripts.gallery import TOKEN_ARTIFACT
from scripts.gallery import VERSIONS
from scripts.gallery import pages
from scripts.seed import LEASE
from scripts.seed import seed

STRAY = "planted:by-hand"


async def test_seeding_again_replaces_a_fixtures_session_from_scratch(tmp_path: Path) -> None:
    """A record left under a fixture's id by anything but the table is gone after the next seed."""
    database = tmp_path / "demo.db"
    fixture = FIXTURES[0]
    await seed(database)
    async with open_store(database, LEASE, Catalogues(current=CATALOGUE)) as service:
        await service.checkpointer.supply(fixture.session.id, STRAY, {"said": "left over"})

    await seed(database)

    async with open_store(database, LEASE, Catalogues(current=CATALOGUE)) as service:
        held = await service.checkpointer.load(fixture.session.id)
        listed = await read_sessions(service.database)
    assert STRAY not in held
    assert {key: held.get(key) for key in fixture.checkpoint} == fixture.checkpoint
    assert sorted(session.id for session in listed) == sorted(each.session.id for each in FIXTURES)


async def test_a_session_that_is_not_a_fixture_is_left_as_it_was(tmp_path: Path) -> None:
    database = tmp_path / "demo.db"
    async with open_store(database, LEASE, Catalogues(current=CATALOGUE)) as service:
        mine = await service.start(DEFAULT_CHOICE)
        before = await service.checkpointer.load(mine.id)

    await seed(database)

    async with open_store(database, LEASE, Catalogues(current=CATALOGUE)) as service:
        assert await service.checkpointer.load(mine.id) == before
        assert mine.id in {session.id for session in await read_sessions(service.database)}


async def test_a_seeded_session_with_a_repository_carries_its_batchs_diff(tmp_path: Path) -> None:
    database = tmp_path / "demo.db"
    await seed(database)
    working = next(fixture for fixture in FIXTURES if fixture.session.repository is not None)
    async with open_store(database, LEASE, Catalogues(current=CATALOGUE)) as service:
        held = await service.checkpointer.load(working.session.id)
    assert wrote_key(1, 1) in held


async def test_the_seeded_artifacts_are_the_versions_the_gallery_draws(tmp_path: Path) -> None:
    """The gallery derives its versions rather than keeping them, so this is what holds the two equal."""
    database = tmp_path / "demo.db"
    await seed(database)
    async with open_store(database, LEASE, Catalogues(current=CATALOGUE)) as service:
        planted = [
            kept for artifact in (POLL_ARTIFACT, TOKEN_ARTIFACT) for kept in await history(service.database, artifact)
        ]
    assert sorted(planted, key=lambda kept: kept.seq) == list(VERSIONS)


async def test_seeding_again_replaces_the_artifacts_rather_than_adding_versions(tmp_path: Path) -> None:
    database = tmp_path / "demo.db"
    await seed(database)
    await seed(database)
    async with open_store(database, LEASE, Catalogues(current=CATALOGUE)) as service:
        planted = await history(service.database, POLL_ARTIFACT)
    assert [kept.version for kept in planted] == [2, 1]


def test_every_gallery_page_has_a_caption_and_nothing_else_does() -> None:
    """
    The documentation site lists the gallery from `CAPTIONS`, so a page without one would be listed
    with nothing beside it and a caption without a page would name a link to nowhere.
    """
    assert set(CAPTIONS) == set(pages())
