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
# Each file the server would compress is also written compressed, once per coding, under the
# suffix the inventory reads a sidecar from, so a process start reads these rather than encoding
# them. That is where the levels come from: brotli 11 takes six seconds over the diagram library
# alone, which no start could pay, and ships an eighth less than the default a start would use.
# They are derived bytes, so they carry no digest of their own; the suite decodes each one and
# compares it with the file the digest does cover.

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


def encodable(name: str) -> bool:
    """Whether the server compresses a file of this name, which a face, already compressed, is not."""
    content_type, _ = mimetypes.guess_type(name)
    return content_type is not None and is_compressible(content_type.encode())


def sidecars(name: str, data: bytes) -> dict[str, bytes]:
    """`data` in every coding the server offers, by the suffix each is read from beside `name`."""
    if not encodable(name):
        return {}
    return {suffix: compressed(data, make()) for suffix, make in SIDECARS.items()}


def compressed(data: bytes, compressor: Compressor) -> bytes:
    return compressor.compress(data) + compressor.flush()


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
    encoded = tuple((entry, data, sidecars(entry.name, data)) for entry, data in checked)
    for entry, data, beside in encoded:
        (into / entry.name).write_bytes(data)
        # After the file, so each is the newer of the two: the inventory takes a sidecar older than
        # its file to describe bytes the file no longer has, and compresses at startup instead.
        for suffix, body in beside.items():
            (into / f"{entry.name}{suffix}").write_bytes(body)
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
