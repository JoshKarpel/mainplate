from __future__ import annotations

import gzip
import io
import zipfile
from collections.abc import Callable
from compression import zstd
from pathlib import Path

import brotli  # type: ignore[import-untyped]  # the bindings ship no types
import pytest
from without_asgi.assets import Inventory

from scripts.vendor import ASSETS
from scripts.vendor import SIDECARS
from scripts.vendor import Mismatch
from scripts.vendor import Vendored
from scripts.vendor import digest
from scripts.vendor import encodable
from scripts.vendor import manifest
from scripts.vendor import unverified
from scripts.vendor import vendor
from scripts.vendor import writes

VENDORED = manifest()

# Every row with sidecars beside it, which is every row the server compresses for as long as
# `test_the_recipe_encodes_exactly_what_the_server_does` passes.
ENCODED = tuple(entry for entry in VENDORED if encodable(entry.name))

# How each sidecar is read back, and the coding the inventory serves it as.
DECODED: dict[str, tuple[Callable[[bytes], bytes], bytes]] = {
    ".br": (brotli.decompress, b"br"),
    ".zst": (zstd.decompress, b"zstd"),
    ".gz": (gzip.decompress, b"gzip"),
}

# This console's own scripts, which are the only files of these kinds under `assets/` that no
# manifest row names.
OURS = frozenset({"mainplate.js", "service-worker.js"})

# What somebody else wrote is a script or a face; everything else in `assets/` is this console's.
SOMEBODY_ELSES = ("*.js", "*.woff2")


class TestWhatIsVendored:
    """
    The copies on disk are the bytes the manifest records, with no network anywhere near it.

    That is the half of `just vendor`'s check a suite can run: the recipe refuses to write a file
    whose digest is not the recorded one, and this refuses to pass while a file on disk is not.
    """

    def test_the_copy_on_disk_is_the_bytes_the_manifest_records(self) -> None:
        """Through `unverified`, so what the suite checks is the check a test below can make fail."""
        assert unverified(VENDORED, ASSETS) == ()

    def test_every_script_and_face_that_is_not_this_console_s_own_is_in_the_manifest(self) -> None:
        """
        The rule applied uniformly: a script or a face under `assets/` that nobody here wrote came
        from somewhere, and the manifest is where that is recorded. One added by hand has no row,
        and this is what says so.
        """
        found = {path.name for pattern in SOMEBODY_ELSES for path in ASSETS.glob(pattern)} - OURS
        assert found == {entry.name for entry in VENDORED if entry.name.endswith((".js", ".woff2"))}

    @pytest.mark.parametrize("suffix", SIDECARS)
    @pytest.mark.parametrize("entry", ENCODED, ids=lambda entry: entry.name)
    def test_each_sidecar_decodes_to_the_bytes_the_manifest_records(self, entry: Vendored, suffix: str) -> None:
        """A sidecar carries no digest of its own, so what holds it to the manifest is decoding it."""
        decode, _ = DECODED[suffix]
        sidecar = ASSETS / f"{entry.name}{suffix}"
        assert sidecar.is_file(), f"{sidecar.name} is missing; `just vendor` writes it"
        assert digest(decode(sidecar.read_bytes())) == entry.sha256

    @pytest.mark.parametrize("entry", VENDORED, ids=lambda entry: entry.name)
    def test_the_recipe_encodes_exactly_what_the_server_does(self, entry: Vendored, assets: Inventory) -> None:
        """
        `encodable` restates a question the inventory answers privately, so this is what turns the
        two disagreeing into a failure: a row the recipe skips that the server encodes is paid for
        at every start, and one it encodes that the server does not is a sidecar nothing reads.
        """
        assert bool(assets.assets[entry.name].encodings) == encodable(entry.name)

    @pytest.mark.parametrize("entry", ENCODED, ids=lambda entry: entry.name)
    def test_the_server_serves_the_sidecars_rather_than_compressing_at_startup(
        self, entry: Vendored, assets: Inventory
    ) -> None:
        """
        The inventory falls back to compressing a file whose sidecar is older than it, and says so
        only in a log line, so a checkout that wrote them in the wrong order would pass everything
        above while paying for the diagram library at every start.
        """
        encodings = assets.assets[entry.name].encodings
        for suffix, (_, coding) in DECODED.items():
            assert encodings[coding].body == (ASSETS / f"{entry.name}{suffix}").read_bytes(), coding

    def test_every_vendored_file_names_a_pinned_release(self) -> None:
        """A URL with no version in it fetches whatever was published most recently, which is not a pin."""
        for entry in VENDORED:
            assert "@" in entry.url or "/releases/download/v" in entry.url, f"{entry.name}: {entry.url} pins no version"
            assert "@latest" not in entry.url, f"{entry.name} floats on whatever is latest"
            assert "@next" not in entry.url, f"{entry.name} floats on whatever is next"


class TestWhatTheRecipeRefuses:
    def test_a_file_whose_bytes_differ_is_refused_and_nothing_is_written(self, tmp_path: Path) -> None:
        """Every row is checked before any is written, so a bad digest on the second leaves no first."""
        good = Vendored(name="good.js", url="https://example.test/good", sha256=digest(b"good"))
        bad = Vendored(name="bad.js", url="https://example.test/bad", sha256=digest(b"what was recorded"))
        served = {good.url: b"good", bad.url: b"what was published"}
        with pytest.raises(Mismatch, match=r"bad\.js"):
            vendor((good, bad), tmp_path, fetch=lambda url: served[url])
        assert list(tmp_path.iterdir()) == []

    def test_matching_bytes_are_written_under_the_manifest_s_name(self, tmp_path: Path) -> None:
        entry = Vendored(name="lib.min.js", url="https://example.test/lib", sha256=digest(b"var lib = 1;"))
        vendor((entry,), tmp_path, fetch=lambda url: b"var lib = 1;")
        assert (tmp_path / "lib.min.js").read_bytes() == b"var lib = 1;"

    def test_a_member_of_a_zip_is_checked_and_written_as_itself_with_the_zip_fetched_once(self, tmp_path: Path) -> None:
        """A face ships inside a release archive, so the digest is the face's and never the archive's."""
        packed = io.BytesIO()
        with zipfile.ZipFile(packed, "w") as archive:
            archive.writestr("fonts/Face-Regular.woff2", b"regular face")
            archive.writestr("fonts/Face-Bold.woff2", b"bold face")
        release = "https://example.test/release.zip"
        regular = Vendored(
            name="Face-Regular.woff2", url=release, member="fonts/Face-Regular.woff2", sha256=digest(b"regular face")
        )
        bold = Vendored(
            name="Face-Bold.woff2", url=release, member="fonts/Face-Bold.woff2", sha256=digest(b"bold face")
        )
        fetches: list[str] = []

        def fetch(url: str) -> bytes:
            fetches.append(url)
            return packed.getvalue()

        vendor((regular, bold), tmp_path, fetch=fetch)
        assert fetches == [release]
        assert (tmp_path / "Face-Regular.woff2").read_bytes() == b"regular face"
        assert (tmp_path / "Face-Bold.woff2").read_bytes() == b"bold face"

    @pytest.mark.parametrize("suffix", SIDECARS)
    def test_a_script_is_written_with_a_sidecar_that_decodes_to_it(self, tmp_path: Path, suffix: str) -> None:
        """One sidecar per coding the server offers, under the suffix the inventory reads it from."""
        script = b"var lib = 1;\n" * 64
        entry = Vendored(name="lib.min.js", url="https://example.test/lib", sha256=digest(script))
        vendor((entry,), tmp_path, fetch=lambda url: script)
        decode, _ = DECODED[suffix]
        assert decode((tmp_path / f"lib.min.js{suffix}").read_bytes()) == script

    @pytest.mark.parametrize("suffix", SIDECARS)
    def test_each_sidecar_is_written_after_the_file_it_encodes(self, suffix: str) -> None:
        """
        The inventory ignores a sidecar older than its file, so the order is the claim. It is read
        off `writes` rather than off the files' timestamps, which a filesystem rounds to one tick
        often enough that the wrong order would still pass most runs.
        """
        first = Vendored(name="first.min.js", url="https://example.test/first", sha256=digest(b"var first;"))
        second = Vendored(name="second.min.js", url="https://example.test/second", sha256=digest(b"var second;"))
        names = [name for name, _ in writes(((first, b"var first;"), (second, b"var second;")))]
        for entry in (first, second):
            assert names.index(f"{entry.name}{suffix}") > names.index(entry.name), entry.name

    def test_a_face_is_written_with_no_sidecar(self, tmp_path: Path) -> None:
        """A face is compressed already, so the server never encodes one and a sidecar would be dead weight."""
        entry = Vendored(name="Face.woff2", url="https://example.test/face", sha256=digest(b"wOF2 face"))
        vendor((entry,), tmp_path, fetch=lambda url: b"wOF2 face")
        assert [path.name for path in tmp_path.iterdir()] == ["Face.woff2"]

    @pytest.mark.parametrize("field", ["url", "sha256", "member"])
    def test_a_field_that_is_not_a_string_is_refused_rather_than_coerced(self, tmp_path: Path, field: str) -> None:
        """A row TOML reads as anything but strings is one nobody meant, so it is never fetched."""
        row = {"url": '"https://example.test/lib"', "sha256": f'"{digest(b"lib")}"', "member": '"lib.js"'}
        row[field] = "[2, 3]"
        table = tmp_path / "vendored.toml"
        table.write_text('["lib.js"]\n' + "".join(f"{key} = {value}\n" for key, value in row.items()))
        with pytest.raises(ValueError, match=rf"lib\.js: {field} is \[2, 3\]"):
            manifest(table)

    def test_a_copy_that_drifted_from_the_manifest_is_named(self, tmp_path: Path) -> None:
        kept = Vendored(name="kept.js", url="https://example.test/kept", sha256=digest(b"kept"))
        edited = Vendored(name="edited.js", url="https://example.test/edited", sha256=digest(b"as published"))
        missing = Vendored(name="missing.js", url="https://example.test/missing", sha256=digest(b"never fetched"))
        (tmp_path / "kept.js").write_bytes(b"kept")
        (tmp_path / "edited.js").write_bytes(b"as published, then edited")
        assert unverified((kept, edited, missing), tmp_path) == ("edited.js", "missing.js")
