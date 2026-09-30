# The rail: the column of cards beside a conversation.
#
# The chrome that navigates the conversation (the search, the key, the dock) is deliberately
# *outside* the transcript region, so a swap cannot take a control away mid-press and nothing has to
# be rebuilt under a reader's finger. What the chrome projects back onto the transcript (search
# marks, the panel a reader landed on, which kinds are set aside) is reapplied after each swap by the
# script, which holds that state as values rather than reading it back out of the markup.

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Final

from without_html import Element
from without_html import button
from without_html import div
from without_html import dl
from without_html import form
from without_html import input_
from without_html import label
from without_html import p
from without_html import section
from without_html import span
from without_html import title
from without_html import ul

from mainplate.agent import Choice
from mainplate.pages.archive import archive_card
from mainplate.pages.document import Links
from mainplate.pages.document import Placed
from mainplate.pages.document import fact
from mainplate.pages.document import fragment
from mainplate.pages.figures import footprint_note
from mainplate.pages.figures import sized
from mainplate.pages.moments import Reader
from mainplate.pages.picker import UNNAMED
from mainplate.pages.transcript import NAMES
from mainplate.plugins.installed import Enrolled
from mainplate.plugins.protocol import Number
from mainplate.plugins.protocol import Setting
from mainplate.plugins.protocol import Switch
from mainplate.plugins.protocol import settings_of
from mainplate.sessions import TITLE_FIELD
from mainplate.sessions import TITLE_LENGTH
from mainplate.sessions import Footprint
from mainplate.tending import PLUGIN_FIELD
from mainplate.tending import Tending
from mainplate.thinking import name_of_thinking


def about_card(
    reader: Reader,
    chosen: Choice | None,
    repository: str | None = None,
    checkout: Path | None = None,
    footprint: Footprint | None = None,
) -> Element | None:
    """
    What an existing session is on and where it works, as facts rather than controls: none can change.

    **A card in the rail rather than a line under the message box**, and the move is a grouping
    rather than a saving. Whatever sits against the box is read as being about the act of sending,
    and nothing here is: the model and the repository were settled when the session was made, and
    the disk moves on a timer. Beside the box they took a row on every window and three on a phone,
    saying the same thing every turn; the rail is where the things that are about this session as a
    whole already stand, and on a phone it is behind the clasp, which is right for facts that never
    change. What the box keeps above it is what the next press actually depends on.

    The session's total is not here, because it is already on the page: the running total on the
    last rule is the same figure, and that one moves with the transcript where a card in the rail
    would sit stale until a reload. Nor is anything the sidebar's row already says, save the disk,
    which is the one figure here a person can act on.

    The thinking level is named only when there is one to name. A session that said nothing about
    thinking is not a session set to some level called "default"; it is one that never raised the
    question, and printing a word for that would invent a setting nobody chose. The checkout is
    named on the same terms, and its absence means the same thing: no snapshots are being kept, so
    there is nothing a later fork could put back on disk.

    Nothing at all for a session with no choice recorded, which is the window between its row and
    its first message; the card these stand on, drawn by `rail`, still carries archiving then.

    **A row per fact, each with its key**, rather than a list of bare values. Bare, the card was five
    lines in one ink that a reader had to already know the order of, and a model id longer than the
    column broke mid-word. A key says what the value is, and a value is one thing on one line, cut
    with an ellipsis where it does not fit and whole in the row's title, which is what keeps the
    card the same shape at the rail's width and the phone's. The model is its id after the last
    slash, since the row above already says which endpoint routes it, and the whole id is the title.
    """
    if chosen is None:
        return None
    return dl(
        cls="facts",
        children=[
            *fact("on", chosen.endpoint),
            *fact("model", chosen.model.rpartition("/")[2], cls="about__model", title=chosen.model),
            *(
                fact("thinking", name_of_thinking(chosen.thinking), cls="about__thinking")
                if chosen.thinking is not None
                else ()
            ),
            # On the thinking level's terms: only where somebody typed one, since a session that left
            # the box empty sends what the console knows and has no number of its own to name.
            *(
                fact("output override", str(chosen.output_override), cls="about__override")
                if chosen.output_override is not None
                else ()
            ),
            *(
                (
                    *fact(
                        "repo",
                        repository,
                        cls="checkout",
                        # The repository is what a reader recognises and the checkout is
                        # where to point an editor, so one is shown and the other is there
                        # to be read. No checkout yet is an ordinary state rather than a
                        # missing one: the first pass makes it, so a session says where it
                        # works before it has worked.
                        title=f"This session's checkout: {checkout}"
                        if checkout is not None
                        else "This session works here once its first turn runs",
                    ),
                    # The branch is what somebody about to push needs, and the one
                    # part they cannot work out from the repository's name; conditional
                    # for the sessions recorded before every one had a branch.
                    *(fact("branch", chosen.branch) if chosen.branch is not None else ()),
                )
                if repository is not None
                else ()
            ),
            *(
                # On the same terms as the sidebar's figure, which is the same sentence
                # behind it, so a row and its page agree.
                fact("disk", sized(footprint.allocated), cls="footprint", title=footprint_note(footprint, reader))
                if footprint is not None and footprint.allocated
                else ()
            ),
        ],
    )


def navigate_card() -> Element:
    """
    Everything that reads the conversation, on one card: the search, the key, and the dock.

    One card with three sections rather than three cards, so the rail is three kinds of card, this
    one, a plugin's, and the session's, rather than a column of things with the same edge. The three
    read one another: the search and the dock both step what the key leaves in play, so a reader
    says once what they are looking through, and one card is where that is said.
    """
    return div(
        cls="navigate",
        attrs={"aria-label": "Reading the conversation"},
        children=[search_card(), key_card(), dock_card()],
    )


def search_card() -> Element:
    """
    A field that marks every match in the conversation and steps through them.

    Its own section, and no `hx-` attribute anywhere on it: searching what is already on the page is
    not a question for the server, and asking one would mean a round trip per keystroke to render
    a conversation the browser is already holding.
    """
    return div(
        cls="search",
        children=[
            input_(
                cls="search__input",
                attrs={"type": "search", "placeholder": "find", "aria-label": "Find in conversation"},
            ),
            div(
                cls="search__bar",
                children=[
                    span(cls="search__count", attrs={"role": "status"}),
                    button(
                        cls="search__nav",
                        attrs={
                            "type": "button",
                            "data-search": "prev",
                            "aria-label": "Previous match",
                            "disabled": True,
                        },
                        children="\N{UPWARDS ARROW}",
                    ),
                    button(
                        cls="search__nav",
                        attrs={"type": "button", "data-search": "next", "aria-label": "Next match", "disabled": True},
                        children="\N{DOWNWARDS ARROW}",
                    ),
                ],
            ),
        ],
    )


def key_card() -> Element:
    """
    Which kinds are in play, and the legend for every edge in the margin at the same time.

    Every kind is listed whether or not the conversation currently holds one, because this is a
    legend before it is a filter: a key that grew a row the first time the model reasoned would be
    a control that moved under the reader's hand. The search and the dock both read it, so a
    reader says once what they are looking through rather than once per control that looks.
    """
    return div(
        cls="key",
        children=[
            button(
                cls="key__chip",
                attrs={"type": "button", "data-kind": kind, "aria-pressed": "true"},
                children=name,
            )
            for kind, name in NAMES
        ],
    )


def shelf_card() -> Element:
    """
    Text written and not sent, kept for this conversation and pulled back into the box on demand.

    What the shelf *holds* is here, in the rail, for the reason everything else there is: the rail is
    outside the region that swaps, so a slot is never rebuilt under the reader's hand while a turn
    records. What *fills* it is not here - `Keep` sits beside Send, because it acts on the box and a
    control belongs next to the thing it acts on, which is the same reasoning that keeps a fork link
    on the rule it forks at.

    Its list is rendered by the script and by nothing else, because what it holds is a reader's own
    decision and the server is never told any of it. That is the rail's standing bargain rather than
    an exception - the search field does nothing without the script either - and the page still
    renders, posts and folds with the file absent.

    An empty state rather than a hidden card, so somewhere to put an unsent paragraph is discoverable
    before there is one in it.
    """
    return div(
        cls="shelf",
        attrs={"aria-label": "Shelf"},
        children=[
            span(cls="shelf__title", children="shelf"),
            ul(cls="shelf__list", attrs={"data-shelf": "list"}),
            p(cls="shelf__empty", attrs={"data-shelf": "empty"}, children="Nothing kept yet."),
        ],
    )


def dock_button(cls: str | None, label: str, glyph: str, attrs: dict[str, str]) -> Element:
    return button(
        cls=("dock__btn", cls),
        attrs={"type": "button", "aria-label": label, "title": label, **attrs},
        children=glyph,
    )


def dock_card() -> Element:
    """
    Stepping, leaping, folding, and following: everything that moves a reader through a session.

    Four columns of arrows, widest first, which is the picker's own ordering: the left one steps the
    points where the model's history starts again, the next steps whole turns, landing on the rule
    that opens each, the third steps every panel the key leaves in play, and the right one steps only
    what the model produced.

    The forget column is drawn in every session and steps nothing in most of them, which is right
    rather than a gap: the rail lives outside the region that swaps, so a column that appeared with
    the first forget would not appear until a reload. Its stops are found in the live transcript the
    way every other column's are, so one added mid-session is reachable at once, and its upper
    terminus is the top - which is what "before any forget" means.

    Every arrow says what it steps over rather than being told apart by what it lacks. A button
    identified as "the one with no side" stops being identifiable the instant a second kind of stop
    exists, which is markup becoming ambiguous with nothing failing to say so.

    The left column used to seek the person's own panels, and stepping turns is what that *was*:
    there is exactly one message per turn, so the two columns would visit the same positions and
    differ only in where they stopped. Two controls answering one question is the thing this console
    removes wherever it finds it, so the coarse move now lands on the boundary, where the fork link
    and what the turn cost are, rather than a few lines below it.
    """
    return div(
        cls="dock",
        children=[
            div(
                cls="dock__nav",
                children=[
                    dock_button(
                        "dock__btn--forget",
                        "Previous forget",
                        "\N{UPWARDS ARROW}",
                        {"data-step": "-1", "data-stop": "forget"},
                    ),
                    dock_button(
                        "dock__btn--turn",
                        "Previous turn",
                        "\N{UPWARDS ARROW}",
                        {"data-step": "-1", "data-stop": "turn"},
                    ),
                    dock_button(None, "Previous panel", "\N{UPWARDS ARROW}", {"data-step": "-1", "data-stop": "panel"}),
                    dock_button(
                        "dock__btn--assistant",
                        "Previous panel from the model",
                        "\N{UPWARDS ARROW}",
                        {"data-step": "-1", "data-stop": "panel", "data-side": "model"},
                    ),
                    dock_button(
                        "dock__btn--forget",
                        "Next forget",
                        "\N{DOWNWARDS ARROW}",
                        {"data-step": "1", "data-stop": "forget"},
                    ),
                    dock_button(
                        "dock__btn--turn",
                        "Next turn",
                        "\N{DOWNWARDS ARROW}",
                        {"data-step": "1", "data-stop": "turn"},
                    ),
                    dock_button(None, "Next panel", "\N{DOWNWARDS ARROW}", {"data-step": "1", "data-stop": "panel"}),
                    dock_button(
                        "dock__btn--assistant",
                        "Next panel from the model",
                        "\N{DOWNWARDS ARROW}",
                        {"data-step": "1", "data-stop": "panel", "data-side": "model"},
                    ),
                ],
            ),
            div(
                cls="dock__leap",
                children=[
                    # The start is the rule that opens the first turn rather than the first panel
                    # under it, which is where the turn's own facts and its fork link are, and above
                    # whatever the stretch was told. See `wireDock`.
                    dock_button(None, "To the start", "\N{UPWARDS ARROW TO BAR}", {"data-leap": "start"}),
                    dock_button(None, "To the end", "\N{DOWNWARDS ARROW TO BAR}", {"data-leap": "end"}),
                    # A mode rather than a jump, so it says whether it is on: while it is, the end
                    # stays pinned as answers arrive, and scrolling away is what switches it off.
                    dock_button(
                        "dock__btn--follow",
                        "Follow the end",
                        "\N{BLACK DOWN-POINTING TRIANGLE}",
                        {"data-follow": "toggle", "aria-pressed": "true"},
                    ),
                ],
            ),
            div(
                cls="dock__fold",
                children=[
                    # Every fold on the page, which since a panel is one means the whole conversation
                    # rather than only the calls in it: shut, the transcript is its own outline, one
                    # row per panel saying what is in it, and one press puts it all back.
                    dock_button(None, "Unfold everything", "\N{DOWNWARDS DOUBLE ARROW}", {"data-fold": "open"}),
                    dock_button(None, "Fold everything", "\N{UPWARDS DOUBLE ARROW}", {"data-fold": "shut"}),
                    # The third answer, and it is not a midpoint between the two beside it: those set
                    # every fold one way, and this hands the question back, so what a reader gets is
                    # a call folded, a command open and a system prompt away - the shape the console
                    # renders rather than any single state. It is the way back from either of the
                    # others, which without it are one-way presses over a whole conversation.
                    #
                    # Where each fold started is `data-opens`, which the server puts on every one of
                    # them because nothing else on the page still holds it: a turn being watched is
                    # morphed constantly, and every morph records the state it delivered. See `opens`.
                    dock_button(
                        None,
                        "Fold as the console does",
                        "\N{ANTICLOCKWISE OPEN CIRCLE ARROW}",
                        {"data-fold": "default"},
                    ),
                ],
            ),
        ],
    )


def theme_card() -> Element:
    """
    What the console is read by. Three states, because "follow the machine" is a choice too and a
    two-way toggle silently takes it away from anybody who had it.
    """
    return div(
        cls="theme",
        attrs={"role": "group", "aria-label": "Theme"},
        children=[
            button(attrs={"type": "button", "data-theme-choice": choice}, children=label)
            for choice, label in (("light", "day"), ("system", "auto"), ("dark", "night"))
        ],
    )


SWITCH_CLASS: Final = "plugin__switch"
"""
What a control that takes effect on the press is called, named once because three things reach it.

The card's `hx-trigger` listens for a `change` from it, the stylesheet draws it, and the script's
tier switch sets every one under a heading. A class a trigger or the script depends on is a name to
write down rather than to spell three times.
"""


TIER_SWITCH_CLASS: Final = "tier__switch"
"""
What the switch on a tier's heading is called, which sets the switches under it and nothing else.

**It is not a third kind of answer.** What a session records is a switch per plugin; this is one
control that moves all of them in its group, which is why it posts no field of its own and works only
with the script present. Without `mainplate.js` every plugin's own switch still works, which is the
standing bargain every other scripted control here takes.
"""


def plugin_control(control: Switch | Number, held: Setting, plugin: str, form: str | None = None) -> Element:
    """
    One row of a plugin's card, drawn by the console from what the plugin declared.

    **A card is declared, not rendered.** A render executes nothing, and only a press runs the
    script, which is what lets a card come from a repository at all: rendering is constant and an
    action is rare, so the one place a spawn is affordable is exactly where it lands. It is also what
    keeps a plugin's card in step with the console's own controls, where a card a plugin drew would
    drift the first time anything was restyled.

    The declared card is also the settings schema, so `held` is a value read back under the same name
    the control declares: there is no second schema and no way for the two to disagree about a type.

    `form` is what associates a control with a form it is not nested inside, which the settings step
    needs and the rail's card does not.
    """
    named = f"{PLUGIN_FIELD}:{plugin}:{control.name}"
    if isinstance(control, Switch):
        return label(
            cls=SWITCH_CLASS,
            children=[
                input_(
                    attrs={
                        # The state, not a default: an unchecked box posts no field, so what comes
                        # back is exactly what the box shows.
                        "type": "checkbox",
                        "name": named,
                        "checked": bool(held),
                        "form": form,
                    }
                ),
                span(children=control.label),
            ],
        )
    return label(
        # `typed` is the shape and the mark every row somebody types into shares; `plugin__number`
        # is what is particular to a number, its width and its lack of a spinner.
        cls="typed plugin__number",
        children=[
            span(cls="typed__key", children=control.label),
            span(
                cls="typed__value",
                children=[
                    input_(
                        attrs={
                            "type": "number",
                            "name": named,
                            "value": str(int(held)),
                            # The bounds the plugin declared, said to the browser as well so the
                            # refusal usually happens before the post rather than only after it.
                            # Re-checked at the boundary regardless, because a `min` on an input is
                            # a suggestion.
                            "min": None if control.least is None else str(control.least),
                            "max": None if control.most is None else str(control.most),
                            "form": form,
                            "aria-label": control.label,
                        }
                    ),
                    # The unit, which is what lets a box hold two digits instead of six. Not part of
                    # the label's own words, because it belongs after the number rather than before
                    # it.
                    *((span(cls="plugin__unit", children=control.unit),) if control.unit else ()),
                    # The number's own `Set`, against its box: a switch takes effect on the press
                    # and a number does not, so this is the press that records it, and the
                    # stylesheet draws it only while there is something in the box to record.
                    button(
                        cls="typed__set",
                        attrs={"type": "submit", "form": form, "aria-label": f"Set {control.label}", "title": "Set"},
                        children="\N{CHECK MARK}",
                    ),
                ],
            ),
        ],
    )


def plugin_card(links: Links, session: str, plugin: Enrolled, settings: Mapping[str, Setting]) -> Element:
    """
    One running plugin's own card, drawn from what it declared and posting back to it.

    A form and not a scripted control, so it works with `mainplate.js` absent, and it swaps *itself*
    rather than the transcript: nothing about the conversation changed, and the only thing that did
    is inside this card.

    **The switch takes effect on the press and the number does not**, which is the difference between
    a control you set and one you type into. A checkbox says the whole of what it means the moment it
    moves, so waiting for `Set` leaves a console that looks switched and is not; a number is
    half-written for as long as somebody is writing it, so a `change` on the box would post whatever
    was in it when they tabbed away.

    A plugin that declared no card gets none here, rather than an empty frame with its name on it: a
    heading over nothing reports a feature rather than a fact.

    There is no `Set` for the card. Each number carries its own, against its box, drawn only while
    the box holds something unrecorded; see `plugin_control`. A card-wide button on a row of its
    own was tried and spent a line on every card whether anything was pending or not, and beside
    the last control it read as that control's alone.
    """
    if plugin.described.card is None:
        return div(cls="plugin", attrs={"hidden": True})
    card = plugin.described.card
    return div(
        cls="plugin",
        attrs={"id": plugin_id(plugin.qualified)},
        children=[
            div(cls="plugin__head", children=card.heading),
            form(
                cls="plugin__settings",
                attrs={
                    "method": "post",
                    "action": links.to_press(session),
                    "hx-post": links.to_press(session),
                    "hx-target": f"#{plugin_id(plugin.qualified)}",
                    "hx-swap": "outerHTML",
                    "hx-status:4xx": "swap:none",
                    "hx-status:5xx": "swap:none",
                    "hx-trigger": f"submit, change from:.{SWITCH_CLASS}",
                    "aria-label": card.heading,
                },
                children=[
                    *(
                        plugin_control(
                            row.control, settings.get(row.control.name, row.control.default), plugin.qualified
                        )
                        for row in card.rows
                    ),
                ],
            ),
        ],
    )


def plugin_id(qualified: str) -> str:
    """
    A card's own id, which is what its settings form swaps.

    The qualified name with the colon taken out, because a colon in an id is a selector somebody has
    to escape and `hx-target` is a selector. One rule, here, so the element and everything pointing at
    it are built from one call.
    """
    return f"plugin-{qualified.replace(':', '-')}"


RENAME_ID: Final = "rename"
"""The one rename row a page draws, named so its own form can swap it."""


def rename_row(links: Links, session: str, name: str) -> Element:
    """
    What this session is called, as a box holding it and the press that records a new one.

    **A typed row, the same one a plugin's number is**: a key at the left, the box at the right, and
    `✓` against the box drawn only while it holds something unrecorded, because a name is half-written
    for as long as somebody is writing it and so takes effect on the press rather than on a keystroke.
    A `Rename` button drawn at rest was tried and spent a line of the card on a press nobody had
    anything to make. The key is in the facts' own chrome, since the row stands among them and a name
    is the first fact about a session.

    A form swapping itself, as a plugin's card does: nothing about the conversation changed, so a
    reload would redraw the transcript to show a box that already says the new name. What else draws
    the name follows without this row's help - the tab from the `<title>` in the answer, see
    `renamed`, and the list's row from its own connection, whose token counts renames.

    `required`, so an empty box is refused before the post; the route refuses it again regardless,
    since `required` is a suggestion.
    """
    return form(
        cls="rename",
        attrs={
            "id": RENAME_ID,
            "method": "post",
            "action": links.to_rename(session),
            "hx-post": links.to_rename(session),
            "hx-target": "this",
            "hx-swap": "outerHTML",
            "hx-status:4xx": "swap:none",
            "hx-status:5xx": "swap:none",
        },
        children=label(
            cls="typed",
            children=[
                span(cls="typed__key", children="name"),
                span(
                    cls="typed__value",
                    children=[
                        input_(
                            attrs={
                                "type": "text",
                                "name": TITLE_FIELD,
                                "value": name,
                                "maxlength": str(TITLE_LENGTH),
                                "required": True,
                                # A session nobody has named yet, before its first message, as the
                                # box on the new-session page says it.
                                "placeholder": UNNAMED,
                                "aria-label": "Session name",
                            }
                        ),
                        button(
                            cls="typed__set",
                            attrs={"type": "submit", "aria-label": "Rename", "title": "Rename"},
                            children="\N{CHECK MARK}",
                        ),
                    ],
                ),
            ],
        ),
    )


def renamed(links: Links, session: str, name: str) -> str:
    """
    The answer to a rename: the row as it now stands, and the tab's new title.

    The `<title>` is not swapped anywhere. htmx lifts a `<title>` out of any answer it swaps and sets
    the document's from it, which is what lets a fragment rename the tab without a second region or
    a reload.
    """
    return fragment(rename_row(links, session, name)) + fragment(title(children=name))


def rail(
    links: Links,
    reader: Reader,
    session: str,
    name: str,
    tended: Tending,
    plugins: Sequence[Enrolled] = (),
    *,
    about: Placed = None,
    archived: datetime | None = None,
) -> Element:
    """
    Everything that navigates the conversation, in one column outside the region that swaps.

    Outside deliberately. The transcript is replaced whenever an answer arrives, and a control
    living inside it would be rebuilt under a reader's finger, lose its focus, and forget what
    they had typed into it. What the rail *projects* onto the transcript survives instead by being
    reapplied after each swap, which is the script's job.

    The clasp comes first so that on a window too narrow to stand the rail beside the conversation
    it is left where the cards' head was, and the cards slide off. Which width that is stays the
    stylesheet's to say. It says its word, as the session list's does, because wherever it is drawn
    at all it stands in a row the page clears for the two of them. The button that puts the rail
    away on a wide window is not drawn here, for the session list's reason.

    The cards are in one sheet, and the sheet is what slides, for the session list's reason: a
    finger between two cards lands on the sheet and scrolls it, rather than falling through to the
    conversation under it. Wide, the sheet is the column and scrolls at every width.

    **What it holds is conversation controls, which is wider than navigating and always was**: the
    `aria-label` has said so since there was a rail, and a plugin's card is about a session rather
    than about moving around inside one. They sit under the shelf, which is the boundary: everything
    above it reads the conversation, and these are the first things that change how it is run.

    **A card per running plugin, in enrolment order, and none for a plugin that declared none.**
    Which plugins those are is settled for the session, so the rail draws the cards a session has
    rather than every card this console could draw; a plugin that is off contributes nothing here for
    the same reason it contributes no tool.

    **The switches that turn plugins on and off are not here**, and that is the one asymmetry worth
    naming: those are live only before the first message, because a tool definition leaving the
    cached prefix invalidates everything under it exactly as one arriving late does. So the settings
    step draws them and the rail draws what a running plugin's card says. A plugin's *settings* stay
    live where its being loaded does not, and the two are different questions rather than an
    inconsistency: a setting is a value the plugin reads when it runs, and being loaded decides what
    is in the prefix.

    **What the session is, and then archiving it, are one card about this session**, under the
    plugins' cards. `about` is its facts, the part that is read rather than pressed - which model,
    where its files are, what it holds on disk - and they stand with the control that ends the
    conversation rather than at the top, because a reader opens the rail to search, fold and move,
    and a card of facts ahead of those would push every control down for the sake of things that
    never change. Archiving is the last section of that card, under a rule: it is the one control
    here that ends the conversation rather than steering it, so everything above it is something to
    do while the session runs, and this is what to do when it is over. The card is drawn whether or
    not there are facts yet, since archiving is offered before the first message.

    **The theme goes last, pinned to the bottom by the stylesheet**, because it is the one card here
    that is not about this conversation at all - it is the reader's, across every session - so it is
    the one thing a reader scanning the rail for something about *this* session can skip.
    """
    return section(
        cls="rail",
        attrs={"aria-label": "Conversation controls"},
        children=[
            button(
                cls="rail__clasp",
                attrs={"type": "button", "aria-expanded": "false", "aria-label": "Conversation controls"},
                children="Controls",
            ),
            div(
                cls="rail__sheet",
                children=[
                    navigate_card(),
                    shelf_card(),
                    *(
                        plugin_card(links, session, plugin, settings_of(plugin.described, tended.of(plugin.qualified)))
                        for plugin in plugins
                        if plugin.described.card is not None
                    ),
                    div(
                        cls="about",
                        attrs={"aria-label": "About this session"},
                        children=[
                            div(cls="about__head", children="session"),
                            rename_row(links, session, name),
                            about,
                            archive_card(links, reader, session, archived),
                        ],
                    ),
                    theme_card(),
                ],
            ),
        ],
    )
