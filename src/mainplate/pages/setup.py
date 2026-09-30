# The settings step: which of a session's plugins run, answered before its first turn and drawn in
# the transcript's place until it is. `settling` is the predicate the page, the stream and the
# route answering the step all read, so the three cannot disagree about which shape is on screen.

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from without_html import Element
from without_html import Node
from without_html import button
from without_html import div
from without_html import form
from without_html import input_
from without_html import label
from without_html import p
from without_html import span

from mainplate.pages.document import Links
from mainplate.pages.document import working
from mainplate.pages.rail import SWITCH_CLASS
from mainplate.pages.rail import TIER_SWITCH_CLASS
from mainplate.plugins.installed import ON
from mainplate.plugins.installed import Installed
from mainplate.plugins.installed import Tier
from mainplate.plugins.installed import grouped as by_tier
from mainplate.service import Conversation
from mainplate.tending import AGAIN
from mainplate.tending import ENABLED_FIELD
from mainplate.tending import SETTLE_FIELD
from mainplate.tending import SETTLED
from mainplate.tending import Tending

TIER_NAMES: Final[dict[Tier, tuple[str, str]]] = {
    Tier.BUNDLED: ("bundled", "Shipped with this console."),
    Tier.USER: ("yours", "Installed by whoever runs this console, in its config.yaml."),
    Tier.REPOSITORY: (
        "this repository's",
        "Carried by the repository this session works in. They run unattended at every turn boundary, "
        "and what they write is said to the model.",
    ),
}
"""
What each tier is called on the settings step, and the one line under the heading.

**Grouping by where a plugin came from rather than by what it does is the whole point**: the three
are not equally trusted, and a reader deciding what to leave on is deciding about provenance.

The repository's line is the exposure in plain terms rather than a warning that something may be
unsafe. Behind the confinement one of those plugins can read and write the checkout and run what is
in it, which is what that session's `bash` could already do; the two things it adds are that it runs
unattended rather than because a model asked, and that it puts text into the conversation, which is a
delivery channel for prompt injection with a guaranteed slot on every turn. A reader's next question
is always *what happens*, and this answers it.
"""


SETUP_ID: Final = "setup"
"""The step's own id, because it is what its own form swaps and what the live connection replaces."""


def plugin_switch(plugin: Installed, on: bool, form: str | None = None) -> Element:
    """
    One plugin's own on-and-off, which is the whole of what the settings step decides.

    **Drawn from what was declared and never from what a plugin said**, because none of them has
    been asked anything yet: this is the control that decides which of these programs is run at all,
    so it is made of the name somebody installed the plugin under and the file it is. What a plugin
    calls itself is on its card, and its card comes back from having asked it.

    **Live here because nothing has been run yet, and gone afterwards**, which is not a
    preference: a tool definition leaving the cached prefix invalidates everything under it exactly
    as one arriving late does, so what plugins a session runs is settled the moment they are loaded.
    The rail then draws each running plugin's card and does not draw these, and forking is how a
    conversation changes its mind, as it is for the model and the repository.
    """
    return label(
        cls=SWITCH_CLASS,
        children=[
            # The same name, `off`, ahead of the box: an unchecked checkbox posts no field at all, so
            # without this a plugin somebody turned off is indistinguishable from one this form never
            # carried, and a step with every switch off posts nothing whatsoever. The box wins where
            # it is checked because both values arrive and the reader takes the last.
            input_(
                attrs={"type": "hidden", "name": f"{ENABLED_FIELD}:{plugin.qualified}", "value": "off", "form": form}
            ),
            input_(
                attrs={
                    "type": "checkbox",
                    "name": f"{ENABLED_FIELD}:{plugin.qualified}",
                    "checked": on,
                    "form": form,
                }
            ),
            span(cls="plugin__name", children=plugin.name),
            # And the file it is, for the two tiers where a reader is deciding about a program
            # somebody else wrote: the path is the whole of what there is to go on before it has been
            # asked anything, and it is what tells two plugins with the same name apart. A bundled
            # one's path is inside this package and says nothing a reader can act on.
            *(() if plugin.tier is Tier.BUNDLED else (span(cls="plugin__says", children=str(plugin.path)),)),
        ],
    )


def tier_group(tier: Tier, plugins: Sequence[Installed], tending: Tending, form: str) -> Element:
    """
    One tier's plugins, under a heading whose switch sets every switch below it.

    **The tier switch is one control that moves the ones under it, and never a second answer.** What
    a session records is a switch per plugin, so turning a tier off is turning each of its plugins
    off; a tier that recorded an answer of its own would be a second place the same question is
    answered, which is what this console removes wherever it finds it.

    Every tier is drawn, empty ones included, so the step is the same shape on every session and the
    flow can be learned and tested as one thing rather than as however many lists a repository
    happens to produce.
    """
    named, saying = TIER_NAMES[tier]
    states = [tending.on(plugin.qualified, ON) for plugin in plugins]
    switches = [plugin_switch(plugin, on, form=form) for plugin, on in zip(plugins, states, strict=True)]
    on = sum(states)
    return div(
        cls="tier",
        attrs={"data-tier": tier.value},
        children=[
            label(
                cls=TIER_SWITCH_CLASS,
                children=[
                    input_(
                        attrs={
                            # No `name`, because it posts nothing: it is a control over the controls
                            # below it, which is why it works only with the script present and why
                            # every plugin's own switch works without it.
                            "type": "checkbox",
                            "checked": bool(states) and on == len(states),
                            "indeterminate": 0 < on < len(states),
                            "disabled": not states,
                        }
                    ),
                    span(cls="tier__head", children=named),
                ],
            ),
            p(cls="tier__says", children=saying),
            *(
                (p(cls="tier__none", children="none"),)
                if not switches
                else (div(cls="tier__plugins", children=switches),)
            ),
        ],
    )


SETUP_SAYS: Final = (
    "None of these has been run. Setting up executes each one you leave on, once, to install "
    "whatever it needs and ask it what it contributes; that set is then fixed for this "
    "conversation, and forking is how it changes."
)
"""
The line above the switches, which says what the button does rather than what the list is.

**A reader deciding here is deciding whether to execute somebody else's program**, and nothing else
on the page says so: the tier lines say who wrote each one, and the names say what they are called.
Three halves of the sentence are load-bearing - that nothing has run yet is why the step is worth
stopping at, that a setup *installs* is why it is the one moment with a network, and that the set is
then fixed is why it cannot be left until later.
"""


SETUP_WORKING: Final = "Setting up: each plugin is installing whatever it needs and saying what it contributes."
"""
What the page says while the pass that answers the press is out.

It names the slow half rather than the press, because that is what somebody is waiting on and what
can take minutes: a repository plugin fetching a toolchain is a session sitting here, and a line
saying only "loading" would read as this console being slow.
"""


def setup_step(links: Links, showing: Conversation) -> Element:
    """
    Which of the plugins a session declares to load, drawn between creating one and typing into it.

    **The step is the confirmation before anything is executed.** A plugin is a program, so the pass
    that plants a session's checkout reads only what each tier *declares* - a directory listing and
    two YAML mappings - and the switches here are drawn from that. Pressing the button is what runs
    them, in the request that answers this form, and only the ones left on. So a session that never
    gets past this screen has invoked nothing at all.

    **Always drawn while it applies, and there is always something in it.** Skipping it when nothing
    is declared would make the number of steps depend on what a repository happens to carry, so the
    flow could not be described, learned or tested as one thing - and the empty version is not a case
    worth designing around anyway, because a console ships bundled plugins and so the step always has
    at least a heading and a switch in it.

    **It is a state of the session page and not a route of its own.** The session id exists from the
    moment the choices are posted, so the URL is stable and bookmarkable while the clone runs, and
    the page already has the live connection that fills this in when the declaration lands. A second
    address would be a page somebody can be sitting on when the thing it is waiting for arrives
    somewhere else.

    **It stands alone on that page rather than above the conversation.** A message box drawn beside
    this is pointed at a harness nobody has chosen yet, and the rail draws a card per *running
    plugin*, which is a plugin's own surface standing on the screen that exists to decide whether to
    run it. Which shape the page takes is `settling`, and the live connection sends whichever regions
    that shape has.

    **A branch is the case where there is something to stand in front of**, since a fork carries its
    parent's turns and none of its plugins. Its transcript is withheld until the step is answered
    rather than drawn under it: what a reader can act on there is the press, and every control the
    conversation would offer - sending, forking, going back to a parent - wants a session whose set
    of tools is settled. The cost, stated: a fork made only to re-read what its parent said has to be
    set up before it will show it.

    Four states, and each says the one thing a reader can act on. Nothing declared yet is the clone
    and the checkout. A refusal names what could not be read and offers another pass. A press that
    has been answered and not yet finished is the setup itself, which is the other slow moment in a
    session's life and the one a repository's own plugin decides the length of. Otherwise it is the
    tiers, with a switch apiece, under the button that sets them up - carrying the reason the last
    attempt stopped, where one did.

    Every button here is a plain submit and none of them swaps, because what each one leads to is a
    differently shaped page: settling and loaded are the two halves of `settling`'s own condition, so
    answering with a fragment would leave a reader on the half they had just left.
    """
    # The id and the wrapper once rather than once per arm, because the id is what the live
    # connection replaces: three spellings of it is three chances for a state to stop being the thing
    # that gets swapped in, and only one of them would be visible.
    inside: Node
    if showing.declared is None:
        inside = [
            p(cls="setup__working", children=working()),
            p(
                cls="setup__says",
                children=(
                    "Setting up: planting this session's checkout and reading what it declares."
                    if showing.refused_plugins is None
                    else showing.refused_plugins.why
                ),
            ),
            *(
                (
                    form(
                        cls="setup__again",
                        attrs={"method": "post", "action": links.to_setup(showing.session.id)},
                        children=button(
                            attrs={
                                "type": "submit",
                                "name": SETTLE_FIELD,
                                "value": AGAIN,
                                "disabled": showing.session.archived is not None,
                            },
                            children="Try again",
                        ),
                    ),
                )
                if showing.refused_plugins is not None
                else ()
            ),
        ]
    elif showing.settling_up and showing.refused_setup is None:
        inside = [p(cls="setup__working", children=working()), p(cls="setup__says", children=SETUP_WORKING)]
    else:
        # Named rather than spelled at both ends: the switches sit outside the form and are bound to
        # it by this id, so the two coming to differ is every control posting nothing.
        settling = SETUP_ID + "-form"
        inside = form(
            cls="setup__plugins",
            attrs={
                "id": settling,
                "method": "post",
                "action": links.to_setup(showing.session.id),
                "aria-label": "Which plugins this session loads",
            },
            children=[
                p(cls="setup__says", children=SETUP_SAYS),
                # Why the last attempt did not get anywhere, above the switches rather than beside
                # the plugin it names: what a reader does about a plugin that will not set up is turn
                # it off, and the switch is one line down. It is recorded against that attempt, so
                # pressing again is a new one and this sentence goes.
                *(
                    ()
                    if showing.refused_setup is None
                    else (p(cls="setup__failed", children=showing.refused_setup.why),)
                ),
                *(
                    tier_group(tier, plugins, showing.session.tending, form=settling)
                    for tier, plugins in by_tier(showing.declared)
                ),
                # Disabled for real on an archived session, as the composer's box is: the route
                # refuses the press either way, and a button that posts to a refusal is a control
                # that lies about what the page does.
                button(
                    cls="setup__set",
                    attrs={
                        "type": "submit",
                        "name": SETTLE_FIELD,
                        "value": SETTLED,
                        "disabled": showing.session.archived is not None,
                    },
                    children="Load plugins",
                ),
            ],
        )
    return div(cls="setup", attrs={"id": SETUP_ID}, children=div(cls="settling", children=inside))


def settling(showing: Conversation) -> bool:
    """
    Whether this session is still on its settings step, which is what shape its page takes.

    **The registration alone, and the turn count deliberately not.** A session that has set nothing up
    is on the step, whether nobody has pressed the button yet or a pass is out answering the press,
    because the thing that takes a session past the step is a registration and nothing else writes one.

    A fork is why the turn count is not read here. It carries its parent's turns and none of its
    plugins, so it is a session holding a conversation and still owing an answer to the step - which is
    the point, since it plants a fresh checkout whose toolchain nothing has installed yet and may be
    planted at a tree where `.mainplate/` says something new. What the cached prefix cannot survive is
    a plugin set changing under a request already made, and a fork has made none.

    Two shapes of one page rather than one page with a banner: settling is the step alone, and loaded
    is the transcript, the message box and the rail. The live connection sends whichever regions the
    page's shape has and says so when the checkpoint's stops matching it, and the route answering the
    step refuses anything this says is past it, so this is the one predicate all three read.
    """
    return showing.plugins is None
