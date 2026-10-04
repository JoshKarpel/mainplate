# The console's front page: what wants attention, then a card per place a session can work.

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from typing import Final

from without_html import Child
from without_html import Element
from without_html import a
from without_html import article
from without_html import div
from without_html import h2
from without_html import h3
from without_html import li
from without_html import p
from without_html import section
from without_html import span
from without_html import ul
from without_html import wbr

from mainplate.artifacts import Version
from mainplate.forge import Fetched
from mainplate.forge import Reachable
from mainplate.pages.artifacts import recent_artifacts
from mainplate.pages.debug import debug_section
from mainplate.pages.document import DASHBOARD
from mainplate.pages.document import NEW_SESSION
from mainplate.pages.document import UNTITLED
from mainplate.pages.document import Links
from mainplate.pages.document import Placed
from mainplate.pages.document import Shape
from mainplate.pages.document import document
from mainplate.pages.moments import Reader
from mainplate.pages.moments import dated
from mainplate.pages.moments import moments
from mainplate.pages.moments import stamped
from mainplate.pages.moments import when_element
from mainplate.pages.picker import WITHOUT_A_REPOSITORY
from mainplate.pages.shell import UNREAD
from mainplate.pages.shell import session_row
from mainplate.pages.shell import shell
from mainplate.pages.shell import working_mark
from mainplate.sandbox import Filesystem
from mainplate.sessions import Session

# The sessions the dashboard says want attention, which the live connection redraws beside the list
# because both are read off the same rows. See `Shape.DASHBOARD`.
WANTING_ID: Final = "wanting"


# How many of a repository's sessions its card on the dashboard names before saying how many more.
# The list beside it has all of them; the card is for seeing where work is, not for finding one.
SHOWN_PER_REPOSITORY: Final = 5


def wanting(listed: Sequence[Session]) -> tuple[tuple[Session, ...], tuple[Session, ...]]:
    """
    The sessions the dashboard says want attention: what is unread, then what is still working and
    is not unread.

    Read off the same two facts the list's own marks are, so a row is on the dashboard for exactly
    the reason the list marks it. A session both unread and working is unread, because what arrived
    is worth reading now and what is coming will make it unread again.
    """
    live = [session for session in listed if session.archived is None]
    unread = tuple(session for session in live if session.unseen)
    working = tuple(session for session in live if not session.unseen and working_mark(session.attention))
    return unread, working


def wanting_region(links: Links, reader: Reader, listed: Sequence[Session], reachable: Reachable) -> Element:
    """
    What on this console wants you, which is the region the live connection redraws on the dashboard.

    The same rows the list draws, through `session_row`, so a session reads the same here as in the
    column beside it and there is one drawing of a row to keep right. Nothing where there is nothing:
    a dashboard with an empty heading is one that looks as if it failed to load.
    """
    unread, working = wanting(listed)
    groups = [(heading, sessions) for heading, sessions in ((UNREAD, unread), ("working", working)) if sessions]
    return section(
        cls="dashboard__attention",
        attrs={"id": WANTING_ID, "aria-label": "Sessions wanting attention"},
        children=[
            *(
                div(
                    cls="dashboard__group",
                    children=[
                        h2(cls="picker__legend", children=heading),
                        ul(
                            cls="dashboard__rows",
                            children=[
                                li(children=session_row(links, reader, session, None, 0, reachable))
                                for session in sessions
                            ],
                        ),
                    ],
                )
                for heading, sessions in groups
            ),
            *(() if groups else (p(cls="dashboard__quiet", children="Nothing unread, and nothing working."),)),
        ],
    )


def fetch_note(fetched: Fetched | None, reader: Reader) -> Element:
    """
    When this console's copy of a repository last asked the forge for its branches, and whether it worked.

    Said because a session's own `git fetch` reads that copy and not the forge, so this is how far
    behind the remote every session on the repository can be. A failure is drawn to be spotted and
    carries what git said in its title; the copy keeps the refs it last fetched, so the sessions go on
    working against those.
    """
    if fetched is None:
        return span(cls="fetch", children="not fetched since the console started")
    if fetched.failed is not None:
        return when_element(
            fetched.at,
            cls="fetch fetch--failed",
            title=f"The fetch at {stamped(fetched.at, reader)} failed: {fetched.failed}",
            said=f"fetch failed {dated(fetched.at, reader)}",
        )
    return when_element(
        fetched.at,
        cls="fetch",
        title=f"Fetched {stamped(fetched.at, reader)}",
        said=f"fetched {dated(fetched.at, reader)}",
    )


def dashboard_card(
    links: Links,
    reader: Reader,
    naming: str,
    sessions: Sequence[Session],
    starting: str | None,
    saying: Placed = None,
    footing: Placed = None,
    web: str | None = None,
) -> Element:
    """
    One place sessions work, with the sessions working there and the press that starts another.

    **The press is beside the name**, in the card's top corner, so it is in the same place on every
    card however many sessions are listed under it, and a reader scanning down for a repository finds
    the button on the line they found it on. `starting` is the workspace it starts a session in, or
    nothing for a repository no forge reaches any more, which has no press at all.

    `saying` is a line under the name, which is what the two places with no repository say they are;
    `footing` is the corner under everything, which is where a repository says when it was last
    fetched: a fact about the card rather than the thing a reader came to it for. `web` is where the
    repository is read in a browser, which the name links to where the forge said.
    """
    shown = sessions[:SHOWN_PER_REPOSITORY]
    # Somewhere the name may break that is not mid-word: after each `/`, so a narrow card puts `owner/`
    # on one line and the repository on the next rather than cutting either.
    parts = naming.split("/")
    broken: tuple[Child, ...] = (*(piece for part in parts[:-1] for piece in (f"{part}/", wbr())), parts[-1])
    return article(
        cls="dashboard__card",
        attrs={"data-name": naming},
        children=[
            div(
                cls="dashboard__head",
                children=[
                    h3(
                        cls="dashboard__name",
                        children=broken
                        if web is None
                        else a(attrs={"href": web, "referrerpolicy": "no-referrer"}, children=broken),
                    ),
                    *(
                        (
                            a(
                                cls="dashboard__start",
                                attrs={"href": links.to_new_session(starting)},
                                children=NEW_SESSION,
                            ),
                        )
                        if starting is not None
                        else ()
                    ),
                ],
            ),
            saying,
            *(
                (
                    ul(
                        cls="dashboard__sessions",
                        children=[
                            li(
                                children=a(
                                    attrs={"href": links.to_session(session.id)},
                                    children=[
                                        span(cls="name", children=session.title or UNTITLED),
                                        *((span(cls="unseen", children=UNREAD),) if session.unseen else ()),
                                        *working_mark(session.attention),
                                        when_element(
                                            session.latest,
                                            cls="when",
                                            title=moments(session, reader),
                                            said=dated(session.latest, reader),
                                        ),
                                    ],
                                )
                            )
                            for session in shown
                        ],
                    ),
                )
                if shown
                else (p(cls="dashboard__none", children="No sessions yet."),)
            ),
            *(
                (p(cls="dashboard__more", children=f"and {len(sessions) - len(shown)} more in the list"),)
                if len(sessions) > len(shown)
                else ()
            ),
            *((div(cls="dashboard__foot", children=footing),) if footing is not None else ()),
        ],
    )


def dashboard_page(
    links: Links,
    reader: Reader,
    listed: tuple[Session, ...],
    reachable: Reachable,
    fetches: Mapping[str, Fetched],
    recent: Sequence[Version] = (),
) -> str:
    """
    The console's front page: what wants attention, and every place a session can work.

    **Attention first**, because it is what changes: sessions with something new since anybody looked,
    and sessions a pass is on. The list beside it already has every session in the order they were
    last written to, so this does not repeat it; it picks out the rows the list marks.

    **Then a card per repository**, each with its sessions not yet archived, when this console's copy
    of it was last fetched, and the press that starts a session there. A repository is the thing a
    person comes here to work *in*, so starting a session is a press on its card rather than a card
    picked out of a form. A repository sessions still work in that no forge reaches any more gets a
    card too, with no press on it, since its sessions are still there and a new one could not be
    planted. The two answers that are not a repository come first, as a section of their own: they
    are always there, where the repositories are whatever the forges reach. One card to a row in both
    sections, since a card is as tall as the sessions under it and two side by side would not match.

    **Then the artifacts most recently kept**, after the places because they are what sessions made
    rather than where one starts, and a reader who came to start one should not scroll past them to do
    it. This is where the catalogue is reached from, since the dashboard is where a reader with nothing
    open lands. **The way to the debug page is last of all**, since it is about the console rather than
    about anything worked on in it.

    Nothing here asks the forge anything: the fetch state is what the background loop last left in
    memory, and the repositories are the catalogue a forge answered at startup.
    """
    live = [session for session in listed if session.archived is None]
    reached = {repository.id for repository in reachable.repositories}
    stranded = sorted(
        {session.repository for session in live if session.repository is not None and session.repository not in reached}
    )
    return document(
        links,
        DASHBOARD,
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
                        wanting_region(links, reader, listed, reachable),
                        section(
                            cls="dashboard__places",
                            attrs={"aria-label": "Sessions with no repository"},
                            children=[
                                h2(cls="picker__legend", children="No repository"),
                                div(
                                    cls="dashboard__grid",
                                    children=[
                                        dashboard_card(
                                            links,
                                            reader,
                                            named,
                                            [
                                                session
                                                for session in live
                                                if session.repository is None
                                                and (session.filesystem or Filesystem.NOTHING) is level
                                            ],
                                            level.value,
                                            saying=p(cls="dashboard__note", children=saying),
                                        )
                                        for named, level, saying in WITHOUT_A_REPOSITORY
                                    ],
                                ),
                            ],
                        ),
                        section(
                            cls="dashboard__places",
                            attrs={"aria-label": "Repositories"},
                            children=[
                                h2(cls="picker__legend", children="Repositories"),
                                div(
                                    cls="dashboard__grid",
                                    children=[
                                        *(
                                            dashboard_card(
                                                links,
                                                reader,
                                                naming,
                                                [session for session in live if session.repository == reached.id],
                                                reached.id,
                                                footing=fetch_note(fetches.get(reached.id), reader),
                                                web=reached.web,
                                            )
                                            for reached, naming in reachable.labelled()
                                        ),
                                        *(
                                            dashboard_card(
                                                links,
                                                reader,
                                                repository,
                                                [session for session in live if session.repository == repository],
                                                None,
                                                footing=span(cls="fetch fetch--failed", children="no forge reaches it"),
                                            )
                                            for repository in stranded
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        *recent_artifacts(links, reader, recent),
                        debug_section(links),
                    ],
                )
            ],
        ),
        reader=reader,
        shape=Shape.DASHBOARD,
    )
