# A mkdocs hook (https://www.mkdocs.org/user-guide/configuration/#hooks) that copies the prose
# living at the repository root into the site tree, so those files stay where the people and the
# harnesses that read them already look for them and are still published, and that renders the
# gallery into the site so every page of the console can be looked at without running one.
#
# `README.md` is what somebody arriving at either place wants first, so it is the site's home page
# rather than a second page saying the same things: a landing page written twice is one that comes
# to disagree with itself about what this is. `PHILOSOPHY.md` is named by `AGENTS.md` as the thing
# to read before changing anything, so moving it under `docs/` to publish it would put it somewhere
# neither a reader nor a harness looks. `CHANGELOG.md` is the same: the file a release process edits
# is the one at the root.
#
# The price is that a link in any of the three has to work from both places, which means naming the
# published site by URL rather than reaching for a relative path that resolves in only one of them.
#
# The gallery is rendered here, at build time, rather than checked in: the pages are a pure function
# of fixtures pinned to one clock, rendering them takes about a second, and a copy in the tree would
# be a copy to keep in step with `scripts/gallery.py` and the stylesheet by hand or by hook. They are
# served under `gallery/` with the assets beside them, so the pages are rendered with a relative
# asset prefix where the console's own is absolute. What they cannot do is post or stream: they are
# renders of a checkpoint and nothing answers a form on them.
#
# The nav lists these pages by hand in `mkdocs.yml`; this supplies only their content.

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Final

from mkdocs.structure.files import File

if TYPE_CHECKING:
    from mkdocs.config.defaults import MkDocsConfig
    from mkdocs.structure.files import Files
    from mkdocs.structure.pages import Page

REPO_ROOT: Final = Path(__file__).parent.parent

# The gallery script is a module under the repository root, which is on the path for the suite and
# for `just gallery` but not for a hook mkdocs loads from a file.
sys.path.insert(0, str(REPO_ROOT))

from mainplate.console import LINKS  # noqa: E402
from scripts.gallery import ASSETS  # noqa: E402
from scripts.gallery import CAPTIONS  # noqa: E402
from scripts.gallery import pages  # noqa: E402

ROOT_PAGES: Final = (
    ("README.md", "index.md"),
    ("PHILOSOPHY.md", "philosophy.md"),
    ("CHANGELOG.md", "changelog.md"),
)

GALLERY: Final = "gallery"

# Where each generated page offers to be edited: the root file it was copied from, or the script the
# gallery is rendered by.
SOURCE_OF: Final = {
    **{destination: source for source, destination in ROOT_PAGES},
    f"{GALLERY}.md": "scripts/gallery.py",
}


def gallery_index(rendered: dict[str, str]) -> str:
    """The page listing every rendered page with its caption, as Markdown the site draws."""
    lines = [
        "# Gallery",
        "",
        "Every page of the console, rendered from fixtures rather than from a running console: the",
        "same renders `just gallery` writes and `just shots` screenshots, served here so a page can be",
        "looked at, folded and searched without a provider ever being asked anything. They",
        "are renders of a checkpoint, so nothing on them posts or streams: a form goes nowhere and the",
        "live connection has nothing to connect to.",
        "",
    ]
    for name in rendered:
        title = name.removesuffix(".html")
        lines.append(f"- [{title}]({GALLERY}/{name}){{ target=_blank }}: {CAPTIONS[name]}")
    lines.append("")
    return "\n".join(lines)


def on_files(files: Files, config: MkDocsConfig) -> Files:
    for source_name, destination in ROOT_PAGES:
        text = (REPO_ROOT / source_name).read_text(encoding="utf-8")
        files.append(File.generated(config, destination, content=text))
    rendered = pages(replace(LINKS, assets="assets"))
    for name, markup in rendered.items():
        files.append(File.generated(config, f"{GALLERY}/{name}", content=markup))
    # Everything the console serves, less the guidance files that sit in the same directory for
    # whoever edits it: mkdocs would read those as pages and refuse a build for their absence from
    # the nav.
    for asset in sorted(ASSETS.iterdir()):
        if asset.is_file() and asset.suffix != ".md":
            files.append(File.generated(config, f"{GALLERY}/assets/{asset.name}", abs_src_path=str(asset)))
    files.append(File.generated(config, f"{GALLERY}.md", content=gallery_index(rendered)))
    return files


def on_pre_page(page: Page, config: MkDocsConfig, files: Files) -> Page:
    """
    Point a generated page's edit link at the file it came from.

    MkDocs builds the link from where a page sits in the site tree, so these would offer to edit a
    `docs/index.md`, `docs/philosophy.md`, `docs/changelog.md` and `docs/gallery.md` that do not
    exist. That is worst on the home page, which is the one a reader is most likely to press it from.
    """
    source_name = SOURCE_OF.get(page.file.src_uri)
    if source_name is not None and config.repo_url:
        page.edit_url = f"{config.repo_url.rstrip('/')}/edit/main/{source_name}"
    return page
