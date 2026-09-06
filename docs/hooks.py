# A mkdocs hook (https://www.mkdocs.org/user-guide/configuration/#hooks) that copies the prose
# living at the repository root into the site tree, so those files stay where the people and the
# harnesses that read them already look for them and are still published.
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
# The nav lists these pages by hand in `mkdocs.yml`; this supplies only their content.

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from typing import Final

from mkdocs.structure.files import File

if TYPE_CHECKING:
    from mkdocs.config.defaults import MkDocsConfig
    from mkdocs.structure.files import Files
    from mkdocs.structure.pages import Page

REPO_ROOT: Final = Path(__file__).parent.parent

ROOT_PAGES: Final = (
    ("README.md", "index.md"),
    ("PHILOSOPHY.md", "philosophy.md"),
    ("CHANGELOG.md", "changelog.md"),
)

SOURCE_OF: Final = {destination: source for source, destination in ROOT_PAGES}


def on_files(files: Files, config: MkDocsConfig) -> Files:
    for source_name, destination in ROOT_PAGES:
        text = (REPO_ROOT / source_name).read_text(encoding="utf-8")
        files.append(File.generated(config, destination, content=text))
    return files


def on_pre_page(page: Page, config: MkDocsConfig, files: Files) -> Page:
    """
    Point a generated page's edit link at the root file it was copied from.

    MkDocs builds the link from where a page sits in the site tree, so these three would offer to
    edit a `docs/index.md`, `docs/philosophy.md` and `docs/changelog.md` that do not exist. That is
    worst on the home page, which is the one a reader is most likely to press it from.
    """
    source_name = SOURCE_OF.get(page.file.src_uri)
    if source_name is not None and config.repo_url:
        page.edit_url = f"{config.repo_url.rstrip('/')}/edit/main/{source_name}"
    return page
