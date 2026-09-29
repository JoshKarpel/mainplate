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
#
# Each file the server would compress is also written compressed, once per coding, beside it and
# after it; why, and at what levels, is `docs/design/assets.md`.

from __future__ import annotations

import hashlib
import io
import mimetypes
import sys
import tomllib
import urllib.request
import zipfile
import zlib
from collections.abc import Callable
from collections.abc import Mapping
from compression.zstd import CompressionParameter
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

from without_asgi.compression import Compressor
from without_asgi.compression import brotli_compressor
from without_asgi.compression import gzip_compressor
from without_asgi.compression import is_compressible
from without_asgi.compression import zstd_compressor

MANIFEST: Final = Path(__file__).with_name("vendored.toml")
ASSETS: Final = Path(__file__).resolve().parent.parent / "src" / "mainplate" / "assets"
TIMEOUT_SECONDS: Final = 300

# The suffix the inventory looks for beside a file, and the compressor that writes it, each at the
# top of its range. This runs only when somebody bumps a row, so the time a level costs is paid once
# and never weighed against what the next release of a library makes it worth. Brotli names no
# constant for its ceiling; 11 is it.
SIDECARS: Final[Mapping[str, Callable[[], Compressor]]] = MappingProxyType(
    {
        ".br": lambda: brotli_compressor(11),
        ".zst": lambda: zstd_compressor(CompressionParameter.compression_level.bounds()[1]),
        ".gz": lambda: gzip_compressor(zlib.Z_BEST_COMPRESSION),
    }
)


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
    """
    Every row of the table at `path`, in the order it is written.

    A field that is not a string is refused rather than coerced: a `url` written as a table or a
    `member` as a list is a row nobody meant, and `str()` of it would be fetched, or looked for in
    an archive, as though somebody had.
    """
    with path.open("rb") as file:
        table = tomllib.load(file)
    return tuple(
        Vendored(
            name=name,
            url=text(name, entry, "url"),
            sha256=text(name, entry, "sha256"),
            member=None if "member" not in entry else text(name, entry, "member"),
        )
        for name, entry in table.items()
    )


def text(name: str, entry: Mapping[str, object], field: str) -> str:
    """The string `field` of the row `name`, and a refusal naming both where it is anything else."""
    value = entry[field]
    if not isinstance(value, str):
        raise ValueError(f"{name}: {field} is {value!r}, and only a string is read there")
    return value


def digest(data: bytes) -> str:
    """The SHA-256 of `data` in hex, which is the form `vendored.toml` records it in."""
    return hashlib.sha256(data).hexdigest()


def verified(entry: Vendored, data: bytes) -> bytes:
    """`data`, where it is what `entry` records, and a refusal naming both digests otherwise."""
    found = digest(data)
    if found != entry.sha256:
        raise Mismatch(f"{entry.name} from {entry.url} hashes to {found}, and {entry.sha256} is recorded")
    return data


def fetched(url: str) -> bytes:
    """The bytes published at `url`, unchecked: `vendor` holds them to a digest before any lands."""
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
        return bytes(response.read())


def extracted(entry: Vendored, published: bytes) -> bytes:
    """The bytes `entry` names: the member out of the zip, or what was published as itself."""
    if entry.member is None:
        return published
    with zipfile.ZipFile(io.BytesIO(published)) as archive:
        return archive.read(entry.member)


def encodable(name: str) -> bool:
    """
    Whether the server compresses a file of this name, which a face, already compressed, is not.

    One fact in two places: `without_asgi`'s inventory answers the same question for itself, from
    the same `is_compressible`, and keeps the answer private. `tests/test_vendored.py` asks the
    inventory about every row and fails where the two disagree.
    """
    content_type, stored = mimetypes.guess_type(name)
    return content_type is not None and stored is None and is_compressible(content_type.encode())


def sidecars(name: str, data: bytes) -> dict[str, bytes]:
    """`data` in every coding the server offers, by the suffix each is read from beside `name`."""
    if not encodable(name):
        return {}
    return {suffix: compressed(data, make()) for suffix, make in SIDECARS.items()}


def compressed(data: bytes, compressor: Compressor) -> bytes:
    """`data` through one fresh `compressor`, flushed, as one complete body in its coding."""
    return compressor.compress(data) + compressor.flush()


def writes(checked: tuple[tuple[Vendored, bytes], ...]) -> tuple[tuple[str, bytes], ...]:
    """
    Every file to land in `assets/`, by its name there, in the order it is to be written.

    Each sidecar comes after its file, so it is the newer of the two: the inventory takes a sidecar
    older than its file to describe bytes the file no longer has, and compresses at startup
    instead. The order is a value here rather than only a loop, because two timestamps a
    filesystem rounds to one tick cannot show which was written first.
    """
    return tuple(
        written
        for entry, data in checked
        for written in (
            (entry.name, data),
            *((f"{entry.name}{suffix}", body) for suffix, body in sidecars(entry.name, data).items()),
        )
    )


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
    for name, body in writes(checked):
        (into / name).write_bytes(body)
    for entry, _ in checked:
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
    """`just vendor`: every row of `vendored.toml` into `assets/`, or a refusal and nothing written."""
    try:
        vendor(manifest(), ASSETS)
    except Mismatch as refused:
        print(f"refused, and nothing was written: {refused}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
