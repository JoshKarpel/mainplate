# What this console serves that somebody else wrote, brought in from where it was published.
#
# One table, `vendored.toml` beside this, and one rule applied to every row of it: a file lands in
# `assets/` only when its bytes hash to what the table records. The digest is the whole of the
# safety, so it is applied uniformly rather than per file - a version is in the URL and the digest
# is beside it, and there is no row a fetch can bypass. Every row is fetched and checked before any
# is written, so a bump with one wrong digest writes nothing rather than half.
#
# A row names either a file at its URL or a `member` of the zip at its URL, which is how a face
# published inside a release archive is one row like a script published as itself. The digest is
# of the member's bytes either way, since those are what land in `assets/`. Only zip, because that
# is what has been needed; a release published as a tarball is a second reader here, deliberately.
#
# `tests/test_vendored.py` holds the copies on disk against the same table, with no network, which
# is what makes an edit to a vendored file by hand a failing test rather than a quiet drift.

from __future__ import annotations

import hashlib
import io
import sys
import tomllib
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

MANIFEST: Final = Path(__file__).with_name("vendored.toml")
ASSETS: Final = Path(__file__).resolve().parent.parent / "src" / "mainplate" / "assets"
TIMEOUT_SECONDS: Final = 300


class Mismatch(RuntimeError):
    """A fetched file is not the bytes the manifest records for it, naming both digests."""


@dataclass(frozen=True, slots=True)
class Vendored:
    """
    One file this console serves that somebody else wrote: what it is called here, and where it is from.

    `member` is its path inside the zip at `url`, or nothing where `url` is the file itself.
    """

    name: str
    url: str
    sha256: str
    member: str | None = None


def manifest(path: Path = MANIFEST) -> tuple[Vendored, ...]:
    with path.open("rb") as file:
        table = tomllib.load(file)
    return tuple(
        Vendored(
            name=name,
            url=str(entry["url"]),
            sha256=str(entry["sha256"]),
            member=None if "member" not in entry else str(entry["member"]),
        )
        for name, entry in table.items()
    )


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verified(entry: Vendored, data: bytes) -> bytes:
    """`data`, where it is what `entry` records, and a refusal naming both digests otherwise."""
    found = digest(data)
    if found != entry.sha256:
        raise Mismatch(f"{entry.name} from {entry.url} hashes to {found}, and {entry.sha256} is recorded")
    return data


def fetched(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
        return bytes(response.read())


def extracted(entry: Vendored, published: bytes) -> bytes:
    """The bytes `entry` names: the member out of the zip, or what was published as itself."""
    if entry.member is None:
        return published
    with zipfile.ZipFile(io.BytesIO(published)) as archive:
        return archive.read(entry.member)


def vendor(entries: tuple[Vendored, ...], into: Path, fetch: Callable[[str], bytes] = fetched) -> None:
    """
    Every entry fetched and checked, then every one written: a wrong digest anywhere writes nothing.

    One fetch per URL rather than per row, since the rows naming members of one release archive
    are several files and one download.
    """
    published: dict[str, bytes] = {}
    for entry in entries:
        if entry.url not in published:
            published[entry.url] = fetch(entry.url)
    checked = tuple((entry, verified(entry, extracted(entry, published[entry.url]))) for entry in entries)
    for entry, data in checked:
        (into / entry.name).write_bytes(data)
        where = entry.url if entry.member is None else f"{entry.member} in {entry.url}"
        print(f"{entry.name}  {entry.sha256}  {where}")


def unverified(entries: tuple[Vendored, ...], within: Path) -> tuple[str, ...]:
    """The names whose copy under `within` is missing or is not the bytes recorded, in manifest order."""
    return tuple(
        entry.name
        for entry in entries
        if not (within / entry.name).is_file() or digest((within / entry.name).read_bytes()) != entry.sha256
    )


def main() -> int:
    try:
        vendor(manifest(), ASSETS)
    except Mismatch as refused:
        print(f"refused, and nothing was written: {refused}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
