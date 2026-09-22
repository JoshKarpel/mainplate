from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from scripts.vendor import ASSETS
from scripts.vendor import Mismatch
from scripts.vendor import Vendored
from scripts.vendor import digest
from scripts.vendor import manifest
from scripts.vendor import unverified
from scripts.vendor import vendor

VENDORED = manifest()

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

    @pytest.mark.parametrize("entry", VENDORED, ids=lambda entry: entry.name)
    def test_the_copy_on_disk_is_the_bytes_the_manifest_records(self, entry: Vendored) -> None:
        assert (ASSETS / entry.name).is_file(), f"{entry.name} is in the manifest and not in assets/"
        assert digest((ASSETS / entry.name).read_bytes()) == entry.sha256

    def test_every_script_and_face_that_is_not_this_console_s_own_is_in_the_manifest(self) -> None:
        """
        The rule applied uniformly: a script or a face under `assets/` that nobody here wrote came
        from somewhere, and the manifest is where that is recorded. One added by hand has no row,
        and this is what says so.
        """
        found = {path.name for pattern in SOMEBODY_ELSES for path in ASSETS.glob(pattern)} - OURS
        assert found == {entry.name for entry in VENDORED if entry.name.endswith((".js", ".woff2"))}

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

    def test_a_copy_that_drifted_from_the_manifest_is_named(self, tmp_path: Path) -> None:
        kept = Vendored(name="kept.js", url="https://example.test/kept", sha256=digest(b"kept"))
        edited = Vendored(name="edited.js", url="https://example.test/edited", sha256=digest(b"as published"))
        missing = Vendored(name="missing.js", url="https://example.test/missing", sha256=digest(b"never fetched"))
        (tmp_path / "kept.js").write_bytes(b"kept")
        (tmp_path / "edited.js").write_bytes(b"as published, then edited")
        assert unverified((kept, edited, missing), tmp_path) == ("edited.js", "missing.js")
