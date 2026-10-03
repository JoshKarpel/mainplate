# Artifacts as the console draws them: the catalogue, one artifact's page, and the dashboard's
# section of recent ones.
#
# The artifact itself is never drawn here. Its bytes are served by a route of their own, with a
# policy that sandboxes them, and this module only ever *frames* them: a page of the console's with
# an iframe pointed at that route. Putting the document into the console's markup, even escaped
# into `srcdoc`, would run it under the console's own policy rather than the one written for it.

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from without_html import Element
from without_html import a
from without_html import article
from without_html import details
from without_html import div
from without_html import h1
from without_html import h2
from without_html import header
from without_html import iframe
from without_html import li
from without_html import p
from without_html import section
from without_html import span
from without_html import summary
from without_html import ul

from mainplate.artifacts import LISTED
from mainplate.artifacts import Version
from mainplate.forge import Reachable
from mainplate.pages.document import UNTITLED
from mainplate.pages.document import Links
from mainplate.pages.document import document
from mainplate.pages.document import home
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


def versions(links: Links, reader: Reader, selected: Version, history: Sequence[Version]) -> Element:
    """
    Which version this is, as a disclosure whose panel is the version history.

    **Folded into the bar rather than listed under the frame**, because a list under the frame is what
    made the page scroll, and a page that scrolls has to cap the frame short of the window so the list
    can be reached. Folded, nothing is under the frame and it can take the rest of the window. The
    cost, stated: the history is one press away rather than in view, which is the right way round for
    a page somebody opened to look at one version.

    A `<details>`, so it opens with no script, as the composer's menu does. The summary says that a
    later version exists, where one does, in its title, since "2 of 3" says it only to somebody
    counting.

    `history` is one page of versions, newest first, and a link to the next is drawn where it is full,
    for the catalogue's reason.
    """
    return details(
        cls="artifact__versions",
        children=[
            summary(
                cls="artifact__which",
                attrs={"title": None if selected.is_current else "A later version has been kept since this one"},
                children=f"version {selected.version} of {selected.current}",
            ),
            div(
                cls="dashboard__card artifact__history",
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
    )


def artifact_page(
    links: Links,
    reader: Reader,
    listed: tuple[Session, ...],
    selected: Version,
    history: Sequence[Version],
) -> str:
    """
    One version of one artifact: a bar saying what it is and where it came from, and the page.

    **Not a `shell`.** The session list is navigation among conversations, and an artifact is the one
    thing the console shows that is not one; what a reader here wants is the document as large as
    the window allows. The bar keeps what a page owes a reader, which is `home`, and the two ways
    out this page has of its own: up to every artifact, and back to the turn that kept this version.
    The cost, stated: reaching another session is two presses, through the dashboard, rather than
    one. With no list on the page there is nothing for the live connection to redraw, so the page
    holds none, as a refusal does not.

    **The preview and the download are one version's bytes by two addresses**, both pinned to the
    version drawn here, so neither can change under a reader while the artifact moves on. The preview
    is a sandboxed frame and the download is the file as it was kept, and the words beside the press
    say the one thing a reader has to know about the difference: the saved file runs with whatever a
    browser gives a local file, which is not what the frame gave it. Beside it rather than in its
    title, because a title is said only to a pointer that hovers and a phone has none; the cost is a
    few words of bar on every page for a press most readers never make.

    **Where it came from links to the turn**, which is the other end of the call's own link here.
    """
    heading = selected.title
    return document(
        links,
        heading,
        div(
            cls="artifact",
            children=[
                header(
                    cls="artifact__bar",
                    children=[
                        home(links),
                        a(cls="artifact__up", attrs={"href": links.to_artifacts()}, children=ARTIFACTS),
                        h1(cls="artifact__title", children=heading),
                        versions(links, reader, selected, history),
                        p(
                            cls="artifact__from",
                            children=[
                                "kept ",
                                when_element(
                                    selected.made_at,
                                    cls="when",
                                    title=stamped(selected.made_at, reader),
                                    said=dated(selected.made_at, reader),
                                ),
                                " by ",
                                made_in(links, listed, selected),
                            ],
                        ),
                        p(
                            cls="artifact__download",
                            children=[
                                a(
                                    attrs={
                                        "href": links.to_artifact_download(selected.artifact, selected.version),
                                        "download": True,
                                    },
                                    children="Download",
                                ),
                                " - runs outside this sandbox once saved",
                            ],
                        ),
                    ],
                ),
                iframe(
                    cls="artifact__preview",
                    attrs={
                        "title": heading,
                        "sandbox": SANDBOX,
                        "src": links.to_artifact_content(selected.artifact, selected.version),
                    },
                ),
            ],
        ),
        reader=reader,
        live=False,
    )


def recent_artifacts(links: Links, reader: Reader, recent: Sequence[Version]) -> tuple[Element, ...]:
    """
    The dashboard's section of the artifacts most recently kept, with the way to every one of them.

    `recent` is drawn whole: the route asks the store for `RECENT` and no more, rather than a full
    listing's worth to cut down here.

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
                            children=[artifact_row(links, reader, kept) for kept in recent],
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
