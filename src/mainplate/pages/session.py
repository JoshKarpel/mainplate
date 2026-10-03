# The pages about one session: starting one, reading one, and forking one. Each is the shell around
# parts drawn in the other modules, so what is decided here is which parts, in what order, and why.

from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlencode

from without_html import a
from without_html import button
from without_html import div
from without_html import form
from without_html import h1
from without_html import input_
from without_html import p
from without_html import textarea

from mainplate.catalogue import Catalogue
from mainplate.forge import Fetched
from mainplate.forge import Reachable
from mainplate.pages.archive import archive_card
from mainplate.pages.composer import cache_note
from mainplate.pages.composer import composer
from mainplate.pages.composer import runs_in
from mainplate.pages.dashboard import fetch_note
from mainplate.pages.document import NEW_SESSION
from mainplate.pages.document import UNTITLED
from mainplate.pages.document import WORKSPACE_FIELD
from mainplate.pages.document import Links
from mainplate.pages.document import Shape
from mainplate.pages.document import document
from mainplate.pages.moments import Reader
from mainplate.pages.picker import BASIS_ID
from mainplate.pages.picker import BASIS_LOADING_ID
from mainplate.pages.picker import CHOOSING_ID
from mainplate.pages.picker import ONLY_SCRATCH
from mainplate.pages.picker import WHOLE_MACHINE
from mainplate.pages.picker import branch_field
from mainplate.pages.picker import naming
from mainplate.pages.picker import picker
from mainplate.pages.picker import starting_at
from mainplate.pages.rail import about_card
from mainplate.pages.rail import rail
from mainplate.pages.rail import rename_row
from mainplate.pages.setup import settling
from mainplate.pages.setup import setup_step
from mainplate.pages.shell import shell
from mainplate.pages.transcript import TRANSCRIPT_ID
from mainplate.pages.transcript import panel_element
from mainplate.pages.transcript import stalled_by
from mainplate.pages.transcript import transcript_region
from mainplate.plugins.asking import running
from mainplate.plugins.installed import Enrolled
from mainplate.reference import Reference
from mainplate.sandbox import Filesystem
from mainplate.service import Conversation
from mainplate.sessions import Session


def new_session_page(
    links: Links,
    reader: Reader,
    listed: tuple[Session, ...],
    catalogue: Catalogue,
    reachable: Reachable,
    reference: Reference | None,
    repository: str | None,
    filesystem: Filesystem,
    fetched: Fetched | None = None,
) -> str:
    """
    Where a session begins, in a workspace already chosen: the rest of what it is decided by, and the
    button that creates it.

    **The workspace is the page's heading rather than a question on it**, because it was answered by
    the press that got here, on the dashboard. It rides back on the form as a hidden field, so the
    post names a whole choice and is parsed exactly as it always was. Changing it is going back.

    **Where to start in the repository comes first**, above the network and the model, because on a
    repository it is the question most likely to differ from one session to the next. The branches
    that complete it are asked for once the page has arrived rather than before it is drawn, so the
    page never waits on the forge; see `starting_at`.

    **There is no message box here**, and that is the visible half of a change with two mechanical
    causes. A repository's plugins cannot be named until its checkout is planted, which the worker
    does on a pass; and none of them may be run until somebody has seen the list, because running one
    is executing a program. So this page records the choices, the settings step on the session's own
    page decides what it loads, and the message box is there once both are settled. The cost, stated:
    **creating is not fire-and-forget.** You create, wait, confirm, and come back to type.
    """
    workspace = repository if repository is not None else filesystem.value
    if repository is not None:
        heading = f"{NEW_SESSION} in {reachable.readable(repository)}"
    elif filesystem is Filesystem.EVERYTHING:
        heading = f"{NEW_SESSION}: {WHOLE_MACHINE}"
    else:
        heading = f"{NEW_SESSION}: {ONLY_SCRATCH}"
    default = catalogue.default
    chosen = replace(default, repository=repository, isolation=replace(default.isolation, filesystem=filesystem))
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
                form(
                    cls="choosing",
                    attrs={"id": CHOOSING_ID, "method": "post", "action": links.to_start()},
                    children=div(
                        cls="setup",
                        children=picker(
                            links,
                            catalogue,
                            reference,
                            chosen=chosen,
                            naming=naming(),
                            # Named for what it makes rather than for what it begins, because the
                            # page after this one is the settings step and not a conversation: a
                            # button saying `Start` promised a session you could type into.
                            acting=div(
                                cls="starting", children=button(attrs={"type": "submit"}, children="Create session")
                            ),
                            leading=div(
                                cls="arriving",
                                children=[
                                    div(
                                        cls="arriving__head",
                                        children=[
                                            h1(cls="arriving__name", children=heading),
                                            *((fetch_note(fetched, reader),) if repository is not None else ()),
                                            a(cls="arriving__back", attrs={"href": links.to_home()}, children="change"),
                                        ],
                                    ),
                                    input_(attrs={"type": "hidden", "name": WORKSPACE_FIELD, "value": workspace}),
                                    *(
                                        (
                                            # The block that asks for its own completions once it is on
                                            # the page, so the page is drawn without waiting on the
                                            # forge.
                                            div(
                                                attrs={
                                                    "hx-get": f"{links.to_workspace_branches()}?{urlencode({WORKSPACE_FIELD: repository})}",
                                                    "hx-trigger": "load",
                                                    "hx-target": f"#{BASIS_ID}",
                                                    "hx-swap": "outerHTML",
                                                    "hx-indicator": f"#{BASIS_LOADING_ID}",
                                                    "hx-status:4xx": "swap:none",
                                                    "hx-status:5xx": "swap:none",
                                                },
                                                children=starting_at(repository, None, None),
                                            ),
                                        )
                                        if repository is not None
                                        else ()
                                    ),
                                ],
                            ),
                        ),
                    ),
                )
            ],
        ),
        reader=reader,
    )


def running_plugins(showing: Conversation) -> tuple[Enrolled, ...]:
    """
    The plugins this session actually runs, which is what the rail draws a card for.

    Asked of the registration and the session's own switches together, which is `running`'s job:
    nothing here decides what a default is, and a plugin that is off contributes no card for the same
    reason it contributes no tool.

    Thinned by `running` itself, because a card is a control over something that runs: a repository
    contribution dropped for claiming a name already taken reaches no event, so a card for it would
    be a form whose `Set` changes nothing anybody can see.
    """
    if showing.plugins is None:
        return ()
    return running(showing.plugins, showing.session.tending)


def session_page(
    links: Links, reader: Reader, listed: tuple[Session, ...], showing: Conversation, reachable: Reachable
) -> str:
    """
    One session, in whichever of its two shapes it is in.

    **Settling is the step alone**, in the transcript's own place because that is what it stands in
    for: a message box would be pointed at a harness nobody has chosen yet, and every control in the
    rail belongs to a session whose tools are settled. Settled is the page this console is otherwise
    about. `settling` is what decides, and `streaming.watching` and the route answering the step read
    the same predicate, so the page, the connection driving it and the press cannot disagree about
    which shape is on screen.

    A branch has turns and still takes the first shape, which is the one place these two are not
    "before the conversation" and "after it" - see `setup_step`.
    """
    if settling(showing):
        return document(
            links,
            showing.session.title or UNTITLED,
            shell(
                links,
                reader,
                listed,
                showing=showing.session.id,
                reachable=reachable,
                # The step has no rail, and a session stuck on it is exactly one somebody may want
                # to close, so the card stands under the step rather than being reachable only once
                # the step is answered.
                pane=[
                    setup_step(links, showing),
                    rename_row(links, showing.session.id, showing.session.title),
                    archive_card(links, reader, showing.session.id, showing.session.archived),
                ],
            ),
            reader=reader,
            session=showing.session.id,
            forked_from=showing.session.forked.session if showing.session.forked is not None else None,
            shape=Shape.SETTLING,
        )
    stalled = stalled_by(showing, reader)
    # Once for both readers, so the composer's menu and the rail's cards cannot be built from two
    # answers to the same question, and the thinning behind it is done once per render.
    plugins = running_plugins(showing)
    return document(
        links,
        showing.session.title or UNTITLED,
        shell(
            links,
            reader,
            listed,
            showing=showing.session.id,
            reachable=reachable,
            pane=[
                transcript_region(links, reader, showing),
                # No box at all on an archived session, rather than one that refuses. A disabled
                # control is honest only where something on the page could enable it, and nothing
                # un-archives a session: the box would be a promise the page cannot keep, and the
                # cache note over it would price a request nobody can make. What the transcript ends
                # in says why, and the rule under the last turn is the way on. A session stalled on a
                # missing endpoint keeps its refusing box, because a configuration put back *does*
                # enable it, and that is the difference between the two.
                *(
                    (
                        composer(
                            links.to_say(showing.session.id),
                            refusing=stalled is not None,
                            # Any fork can send back to what it came out of; an aside is the case it
                            # is for.
                            returning=showing.session.forked is not None,
                            # Only while something is actually being answered: a steer into a turn
                            # nobody is running would sit in the database unread, which is a message
                            # on the floor.
                            answering=showing.said.answering is not None,
                            # Only where there are files to run in. A session with no repository has
                            # no checkout, so `Run` would be an offer with nowhere to honour it.
                            runs_in=runs_in(showing),
                            connected=showing.chosen is not None and showing.chosen.isolation.network,
                            # Above the box, where the mode sentence already is, and above that
                            # sentence: this is a standing fact about the conversation and that is
                            # what the next press does, so the transient one sits closest to the
                            # thing it describes.
                            above=cache_note(showing, reader),
                            # What this session's own plugins offer in the composer, each under the
                            # leader somebody types. Declared once by the plugin and rendered by the
                            # console, so a menu row, the button the box shows in that mode, and the
                            # sentence above it cannot disagree about what is on offer.
                            plugins=plugins,
                            context=showing.context,
                        ),
                    )
                    if showing.session.archived is None
                    else ()
                ),
            ],
            aside_rail=[
                rail(
                    links,
                    reader,
                    showing.session.id,
                    showing.session.title,
                    showing.session.tending,
                    plugins,
                    about=about_card(
                        reader,
                        showing.chosen,
                        showing.repository,
                        showing.checkout,
                        showing.session.footprint,
                    ),
                    archived=showing.session.archived,
                )
            ],
        ),
        reader=reader,
        session=showing.session.id,
        forked_from=showing.session.forked.session if showing.session.forked is not None else None,
    )


def inheriting(at: int, asking: bool) -> str:
    """
    What a fork from `at` would carry, said in turns.

    Stated rather than left to the transcript below, because "fork at turn 3" has two readings and
    the wrong one silently throws away the turn somebody meant to keep. The cases are written out
    because a single sentence with a range in it reads as nonsense at both ends: forking at turn 1
    would say "turns 0 to 0", and at turn 0 there is no range at all.

    `asking` is whether there is a turn to re-ask. Forking one of a conversation's turns offers
    that turn's message back, so the fork *asks it again* on the new model; forking the end has
    nothing to re-ask and simply waits.
    """
    carried = "Carries nothing" if at == 0 else "Carries turn 0" if at == 1 else f"Carries turns 0 to {at - 1}"
    return f"{carried}, then asks turn {at} again." if asking else f"{carried}, then waits for turn {at}."


def branching_files(at: int, repository: str) -> str:
    """
    What a fork does to the files, said before somebody finds out afterwards.

    The turns a fork carries are visible on the page below it; what happens to the working files is
    not visible anywhere, and it is the half with work in it. A branch gets *its own* checkout at the
    commit the forked turn started on, with that turn's uncommitted changes on top, so anything
    committed or edited after that turn is simply not in it, and its scratch directory starts empty.

    Nothing is destroyed and the sentence says so, because "restores the checkout" reads as an
    action on the conversation you are looking at. The parent keeps its checkout and its scratch
    exactly as they are: a fork is a new session beside this one, never this one moved backwards.
    """
    return (
        f"Gets its own checkout of {repository} at the commit turn {at} started on, with that turn's "
        f"uncommitted changes, and an empty scratch directory. This conversation's own files are left "
        f"as they are."
    )


def fork_page(
    links: Links,
    reader: Reader,
    listed: tuple[Session, ...],
    showing: Conversation,
    at: int,
    catalogue: Catalogue,
    reachable: Reachable,
    reference: Reference | None = None,
    carrying: str | None = None,
) -> str:
    """
    What a branch from one turn would be, and the one control that may answer differently.

    It shows what carries over rather than only asking a question, because "fork at turn 3" is a
    sentence with two readings and the wrong one silently discards work. What is kept is stated in
    turns, and the transcript beneath is the same one the session page draws, cut to the prefix
    the branch inherits.

    The picker is the *session's* choice rather than the configured default, so the common branch -
    go back and try that turn again on the same model - is the one that needs nothing changed. A
    fork is the one moment a choice may differ, and it is deliberately not the moment it must.

    **The git branch is asked the same way**, above everything else because it is the one question
    about the files: `carrying` is the branch the parent was on when the forked turn began, already
    in the box, so carrying the work on under its own name needs nothing typed. Emptied, the fork
    gets a branch of its own. Only for a session in a repository, since one in none has no branch.

    **What files the fork works in is not asked at all**, because it is not the fork's to change:
    it works in what its parent worked in, and `Service.fork` holds it to that.
    """
    kept = tuple(panel for panel in showing.said.panels if panel.turn < at)
    asked = showing.said.asked_at(at)
    return document(
        links,
        f"Fork {showing.session.title or UNTITLED}",
        shell(
            links,
            reader,
            listed,
            showing=showing.session.id,
            reachable=reachable,
            pane=[
                form(
                    cls="forking",
                    attrs={"method": "post", "action": links.to_fork(showing.session.id), "id": CHOOSING_ID},
                    children=[
                        h1(children="Fork this conversation"),
                        p(cls="forking__kept", children=inheriting(at, asked is not None)),
                        # Only where there is a repository to say it about. A session working in no
                        # files has no checkout and no scratch, so the sentence would be describing
                        # something that does not exist for it.
                        *(
                            (p(cls="forking__files", children=branching_files(at, showing.repository)),)
                            if showing.repository is not None
                            else ()
                        ),
                        input_(attrs={"type": "hidden", "name": "at", "value": str(at)}),
                        # The turn's own message, back in a box you can edit. Without it a fork is
                        # a conversation that stops where you wanted it to continue, and seeing the
                        # same turn answered differently would mean retyping the question first,
                        # which is a different question by the time you have retyped it.
                        textarea(
                            attrs={
                                "name": "prompt",
                                "rows": 3,
                                "autofocus": True,
                                "placeholder": "Say something" if asked is None else None,
                                "aria-label": "Message",
                            },
                            children=asked or "",
                        ),
                        picker(
                            links,
                            catalogue,
                            reference,
                            showing.chosen,
                            # No `naming`: a fork begins with its parent's current title, and it can
                            # be renamed later.
                            leading=(
                                div(
                                    cls="basis",
                                    children=branch_field(
                                        "Branch", carrying, "leave it empty and this fork gets its own"
                                    ),
                                )
                                if showing.repository is not None
                                else None
                            ),
                        ),
                        div(
                            cls="forking__act",
                            children=[
                                button(attrs={"type": "submit"}, children="Fork" if asked is None else "Fork and ask"),
                                a(
                                    cls="forking__back",
                                    attrs={"href": links.to_session(showing.session.id)},
                                    children="Back to the conversation",
                                ),
                            ],
                        ),
                    ],
                ),
                div(
                    cls="transcript",
                    attrs={"id": TRANSCRIPT_ID},
                    children=[panel_element(links, "", panel) for panel in kept]
                    or p(cls="empty", children="Nothing carries over."),
                ),
            ],
        ),
        reader=reader,
        session=showing.session.id,
    )
