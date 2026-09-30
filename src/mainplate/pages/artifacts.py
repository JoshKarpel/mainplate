# Artifacts as the console draws them: the catalogue, one artifact's page, the dashboard's section
# of recent ones, and the rail's card of what one session kept.
#
# The artifact itself is never drawn here. Its bytes are served by a route of their own, with a
# policy that sandboxes them, and this module only ever *frames* them: a page of the console's with
# an iframe pointed at that route. Putting the document into the console's markup, even escaped
# into `srcdoc`, would run it under the console's own policy rather than the one written for it.

from __future__ import annotations

from collections.abc import Sequence
from itertools import groupby
from typing import Final

from without_html import Element
from without_html import a
from without_html import article
from without_html import div
from without_html import dl
from without_html import h1
from without_html import h2
from without_html import iframe
from without_html import li
from without_html import p
from without_html import section
from without_html import span
from without_html import ul

from mainplate.artifacts import LISTED
from mainplate.artifacts import Version
from mainplate.forge import Reachable
from mainplate.pages.document import UNTITLED
from mainplate.pages.document import Links
from mainplate.pages.document import document
from mainplate.pages.document import fact
from mainplate.pages.moments import Reader
from mainplate.pages.moments import dated
from mainplate.pages.moments import stamped
from mainplate.pages.moments import when_element
from mainplate.pages.shell import shell
from mainplate.sessions import Session

ARTIFACTS: Final = "Artifacts"

# How many the dashboard names before pointing at the catalogue, which is the dashboard's own
# `SHOWN_PER_REPOSITORY` for the same reason: enough to find the one just made, and no more.
RECENT: Final = 5

# What the frame the preview is drawn in may do, which is run the document's own scripts and nothing
# else. No `allow-same-origin`, so the document is an opaque origin and cannot read the console's
# cookies, storage or pages; the content route's own policy says the same again for a document opened
# directly, where no frame is around it to say it.
SANDBOX: Final = "allow-scripts"


def artifact_row(links: Links, reader: Reader, kept: Version) -> Element:
    """
    One artifact as a row of a list: its title, which version it is at, and when that was kept.

    The dashboard card's row, so a list of artifacts reads like a list of sessions. Unpinned, since a
    list of artifacts is asking what each is now.
    """
    return li(
        children=a(
            attrs={"href": links.to_artifact(kept.artifact)},
            children=[
                span(cls="name", children=kept.title),
                span(cls="artifact__version", children=f"v{kept.version}"),
                when_element(
                    kept.made_at, cls="when", title=stamped(kept.made_at, reader), said=dated(kept.made_at, reader)
                ),
            ],
        )
    )


def version_row(links: Links, reader: Reader, kept: Version, selected: Version) -> Element:
    """
    One version as a row of an artifact's history: which version, whether it is current, and when.

    The same row as `artifact_row` with the title dropped, since every row of one history carries the
    same one. Pinned, and the one the page is drawn at says so to a screen reader as well as by its
    mark, since a list of links where one is the page you are on is otherwise a list of equals.
    """
    return li(
        children=a(
            attrs={
                "href": links.to_artifact(kept.artifact, kept.version),
                "aria-current": "page" if kept.version == selected.version else None,
            },
            children=[
                span(cls="name", children=f"version {kept.version}"),
                *((span(cls="artifact__version", children="current"),) if kept.is_current else ()),
                when_element(
                    kept.made_at, cls="when", title=stamped(kept.made_at, reader), said=dated(kept.made_at, reader)
                ),
            ],
        )
    )


def made_in(links: Links, listed: Sequence[Session], kept: Version) -> Element:
    """
    Where a version came from, as a link to the rule of the turn whose call kept it.

    The session is named from the list every page already holds, so naming it asks nothing. One no
    longer in the list is named by its id rather than dropped, since the version still came from
    somewhere and the id is what its address is.
    """
    titles = {session.id: session.title or UNTITLED for session in listed}
    return a(
        attrs={"href": links.to_turn(kept.made_by.session, kept.made_by.turn)},
        children=f"{titles.get(kept.made_by.session, kept.made_by.session)}, turn {kept.made_by.turn}",
    )


def older(href: str | None, saying: str) -> tuple[Element, ...]:
    """The link to the next page of a listing, or nothing where this page held all there is."""
    return () if href is None else (p(cls="dashboard__more", children=a(attrs={"href": href}, children=saying)),)


def catalogue_page(
    links: Links, reader: Reader, listed: tuple[Session, ...], reachable: Reachable, shown: Sequence[Version]
) -> str:
    """
    Every artifact, newest first, one row apiece at its current version.

    **One card of rows, the dashboard's**, since this is the dashboard's section of recent artifacts
    with the rest of the list under it. A full page is what says there may be more: the store hands
    back `LISTED` at a time, and the last row's `seq` is where the next page starts, so a link to it is
    drawn exactly when this page is full. The cost, stated: a list of exactly `LISTED` has a link to an
    empty page, which is one press to find out rather than a second query on every page to rule out.
    """
    return document(
        links,
        ARTIFACTS,
        shell(
            links,
            reader,
            listed,
            showing=None,
            reachable=reachable,
            pane=[
                div(
                    cls="dashboard",
                    children=[
                        section(
                            cls="dashboard__places",
                            attrs={"aria-label": ARTIFACTS},
                            children=[
                                h1(cls="picker__legend", children=ARTIFACTS),
                                article(
                                    cls="dashboard__card",
                                    children=[
                                        ul(
                                            cls="dashboard__sessions",
                                            children=[artifact_row(links, reader, kept) for kept in shown],
                                        )
                                        if shown
                                        else p(cls="dashboard__none", children="No artifacts yet."),
                                        *older(
                                            links.to_artifacts(shown[-1].seq) if len(shown) == LISTED else None,
                                            "older artifacts",
                                        ),
                                    ],
                                ),
                            ],
                        )
                    ],
                )
            ],
        ),
        reader=reader,
    )


def artifact_page(
    links: Links,
    reader: Reader,
    listed: tuple[Session, ...],
    reachable: Reachable,
    selected: Version,
    history: Sequence[Version],
) -> str:
    """
    One version of one artifact: what it is, where it came from, the version history, and the page.

    **The preview and the download are one version's bytes by two addresses**, both pinned to the
    version drawn here, so neither can change under a reader while the artifact moves on. The preview
    is a sandboxed frame and the download is the file as it was kept, and the sentence beside the
    press says the one thing a reader has to know about the difference: the saved file runs with
    whatever a browser gives a local file, which is not what the frame gave it.

    **Where it came from links to the turn**, which is the other end of the call's own link here.

    `history` is one page of versions, newest first, and a link to the next is drawn where it is full,
    for the catalogue's reason.
    """
    heading = selected.title
    return document(
        links,
        heading,
        shell(
            links,
            reader,
            listed,
            showing=None,
            reachable=reachable,
            pane=[
                div(
                    cls="artifact",
                    children=[
                        div(
                            cls="artifact__head",
                            children=[
                                h1(cls="artifact__title", children=heading),
                                a(
                                    cls="artifact__back",
                                    attrs={"href": links.to_artifacts()},
                                    children="every artifact",
                                ),
                            ],
                        ),
                        dl(
                            cls="facts",
                            children=[
                                *fact(
                                    "version",
                                    f"{selected.version} of {selected.current}",
                                    title=None
                                    if selected.is_current
                                    else "A later version has been kept since this one",
                                ),
                                *fact(
                                    "kept",
                                    dated(selected.made_at, reader),
                                    title=stamped(selected.made_at, reader),
                                ),
                            ],
                        ),
                        p(
                            cls="artifact__from",
                            children=["Kept by ", made_in(links, listed, selected), "."],
                        ),
                        iframe(
                            cls="artifact__preview",
                            attrs={
                                "title": heading,
                                "sandbox": SANDBOX,
                                "src": links.to_artifact_content(selected.artifact, selected.version),
                            },
                        ),
                        p(
                            cls="artifact__download",
                            children=[
                                a(
                                    attrs={
                                        "href": links.to_artifact_download(selected.artifact, selected.version),
                                        "download": True,
                                    },
                                    children="Download this version",
                                ),
                                " - the same file, which runs outside this sandbox once it is saved.",
                            ],
                        ),
                        section(
                            cls="dashboard__places",
                            attrs={"aria-label": "Versions"},
                            children=[
                                h2(cls="picker__legend", children="Versions"),
                                article(
                                    cls="dashboard__card",
                                    children=[
                                        ul(
                                            cls="dashboard__sessions",
                                            children=[version_row(links, reader, kept, selected) for kept in history],
                                        ),
                                        *older(
                                            links.to_artifact(selected.artifact, selected.version, history[-1].version)
                                            if len(history) == LISTED
                                            else None,
                                            "older versions",
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                )
            ],
        ),
        reader=reader,
    )


def recent_artifacts(links: Links, reader: Reader, recent: Sequence[Version]) -> tuple[Element, ...]:
    """
    The dashboard's section of the artifacts most recently kept, with the way to every one of them.

    Nothing at all on a console that has kept none, for `wanting_region`'s reason: a heading over
    nothing reads as a page that failed to load. The catalogue is still at its address.
    """
    if not recent:
        return ()
    return (
        section(
            cls="dashboard__places",
            attrs={"aria-label": ARTIFACTS},
            children=[
                h2(cls="picker__legend", children=ARTIFACTS),
                article(
                    cls="dashboard__card",
                    children=[
                        ul(
                            cls="dashboard__sessions",
                            children=[artifact_row(links, reader, kept) for kept in recent[:RECENT]],
                        ),
                        div(
                            cls="dashboard__foot",
                            children=a(attrs={"href": links.to_artifacts()}, children="every artifact"),
                        ),
                    ],
                ),
            ],
        ),
    )


def kept_card(links: Links, kept: Sequence[Version]) -> tuple[Element, ...]:
    """
    The rail's card of what this session kept: an artifact to a row, each version it kept a link.

    Grouped by artifact, in the order the session first kept each, because a session that keeps a
    page and then three revisions of it made one thing four times rather than four things. Each version
    link is pinned to what this session kept; the title links to the artifact as it is now, which may
    be a version some other session kept since.

    Nothing where the session kept nothing, which is most sessions, so the rail is not a card longer
    for them.
    """
    if not kept:
        return ()
    first: dict[str, int] = {}
    for at, each in enumerate(kept):
        first.setdefault(each.artifact, at)
    ordered = sorted(kept, key=lambda each: (first[each.artifact], each.version))
    return (
        div(
            cls="about kept",
            attrs={"aria-label": "Artifacts this session kept"},
            children=[
                div(cls="about__head", children="artifacts"),
                ul(
                    cls="kept__rows",
                    children=[
                        li(
                            children=[
                                a(
                                    cls="kept__title",
                                    attrs={"href": links.to_artifact(artifact)},
                                    children=versions[0].title,
                                ),
                                *(
                                    a(
                                        cls="kept__version",
                                        attrs={"href": links.to_artifact(artifact, each.version)},
                                        children=f"v{each.version}",
                                    )
                                    for each in versions
                                ),
                            ]
                        )
                        for artifact, grouped in groupby(ordered, key=lambda each: each.artifact)
                        for versions in (tuple(grouped),)
                    ],
                ),
            ],
        ),
    )
