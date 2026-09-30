# What a session runs on, asked as groups of cards: endpoint, model, thinking, output, network,
# trust, and where in a repository it starts. The new-session page and the fork page both draw it,
# which is the reason it is a module of its own rather than part of either.

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from pydantic_ai.settings import ThinkingLevel
from without_html import Element
from without_html import button
from without_html import datalist
from without_html import div
from without_html import h2
from without_html import input_
from without_html import label
from without_html import li
from without_html import option
from without_html import section
from without_html import span
from without_html import ul

from mainplate.agent import Choice
from mainplate.agent import Listed
from mainplate.catalogue import Catalogue
from mainplate.catalogue import Offering
from mainplate.catalogue import grouped
from mainplate.conversation import BASE_FIELD
from mainplate.conversation import BRANCH_FIELD
from mainplate.conversation import NETWORK_FIELD
from mainplate.conversation import OUTPUT_OVERRIDE_FIELD
from mainplate.conversation import THINKING_FIELD
from mainplate.conversation import TRUSTED_FIELD
from mainplate.pages.document import Links
from mainplate.pages.document import Placed
from mainplate.pages.document import working
from mainplate.pages.figures import counted
from mainplate.pages.figures import dollars
from mainplate.pages.figures import tokens
from mainplate.reference import Cost
from mainplate.reference import Described
from mainplate.reference import Reference
from mainplate.reference import describe
from mainplate.reference import output_cap_of
from mainplate.sandbox import Filesystem
from mainplate.sessions import TITLE_FIELD
from mainplate.sessions import TITLE_LENGTH
from mainplate.snapshots import LONGEST_REF
from mainplate.thinking import THINKING_CHOICES

MODEL_ID: Final = "model"


# The two folds on the picker. A `<label>` reaches its control by id, so these have to be different
# strings on a page that draws both, and stable across the swap that replaces the model group.
ENDPOINT_TOGGLE_ID: Final = "open-endpoint"


MODEL_TOGGLE_ID: Final = "open-model"


THINKING_TOGGLE_ID: Final = "open-thinking"


THINKING_ID: Final = "thinking"


NETWORK_TOGGLE_ID: Final = "open-network"


NETWORK_ID: Final = "network"


TRUST_TOGGLE_ID: Final = "open-trust"


TRUST_ID: Final = "trust"


# What the two fields under the new-session page's heading are, as one thing to swap: the block
# asks for the repository's branches once it has arrived and is replaced whole by the answer. Named
# here because the asking element carries the target and the block carries the id, and the two must
# not drift.
BASIS_ID: Final = "basis"


OVERRIDE_ID: Final = "output-override"


BRANCHES_ID: Final = "branches"


FOUND_ID: Final = "branches-found"


# The dots inside that block, shown while the forge is being asked what branches it has. Named here
# for the same reason the block is: the asking element points `hx-indicator` at it and the block
# draws it.
BASIS_LOADING_ID: Final = "basis-loading"


# The form the picker's controls belong to, named rather than relied on by nesting. Both pages nest
# them inside it today, so on both the `form` attribute names the ancestor a control already had -
# but what carries it is `model_cards` and `starting_at`, which are also served as fragments, and a
# fragment is markup with no ancestor at all until the swap lands. Naming the form is what makes a
# control's association a property of the control rather than of wherever it is put, which is what
# lets one component serve the page and the swap. `TestWhatAFormPosts` is what fails when it goes,
# by asking a browser what `form.elements` holds.
CHOOSING_ID: Final = "choosing"


def model_card(described: Described, chosen: bool) -> Element:
    """
    One model as something you pick rather than something you scroll past.

    A `<label>` around a radio, so it works with no script at all: the whole card is the hit target,
    the browser does the selecting, and the form posts the same field a select posted. That is the
    same bargain the rest of this console makes - the page works without JavaScript and is nicer
    with it - and it is why this is not a grid of buttons driven by a handler.

    Every fact on it comes from the reference database, so a card says the same kinds of things
    whichever endpoint serves the model. What it says nothing about it simply omits: an absent price
    is a blank rather than a zero, because a model shown as costing nothing is worse than a model
    shown as costing something nobody wrote down.
    """
    facts = described.listed
    return label(
        cls="model",
        # The name this card answers to, which is the same string the group's completion list is
        # built from: naming one exactly is how the reader picks it without reaching for the card.
        # And what the override box under the picker should say while this card is the pick, carried
        # here as the sentence itself, so the script that copies it across on a pick holds no wording
        # of its own and the two cannot drift. The number is the one the wire would send, which is
        # the listing's where it states one and the record's otherwise; the card's own figure is the
        # record's alone, since a card says facts from one source.
        attrs={
            "data-provider": facts.provider,
            "data-name": facts.label,
            "data-override": override_placeholder(facts.output if facts.output is not None else described.output),
        },
        children=[
            input_(
                cls="model__pick",
                attrs={
                    "type": "radio",
                    "name": "model",
                    "value": facts.id,
                    "checked": chosen,
                    "form": CHOOSING_ID,
                },
            ),
            span(cls="model__name", children=facts.label),
            span(cls="model__id", children=facts.id),
            *(
                (span(cls="model__about", attrs={"title": about}, children=about),)
                if (about := described.about)
                else ()
            ),
            div(
                cls="model__facts",
                children=[
                    *measured("context", described.context, "of context"),
                    *measured("output", described.output, "of output"),
                    *priced(described.cost),
                ],
            ),
            div(
                cls="model__traits",
                children=[span(cls="model__trait", children=trait) for trait in described.traits],
            ),
            # Only where a reference was configured and had nothing to say. With the setting absent
            # nothing is missing, because nothing was ever looked up, and a marker there would be
            # reporting the absence of a feature nobody turned on.
            *((span(cls="model__unknown", children="no reference record"),) if described.unreferenced else ()),
        ],
    )


def measured(what: str, count: int | None, saying: str) -> tuple[Element, ...]:
    """One token figure, or nothing at all where the database does not carry it."""
    if count is None:
        return ()
    return (
        span(
            cls=("model__fact", f"model__fact--{what}"),
            attrs={"title": f"{count:,} tokens {saying}"},
            children=[span(cls="model__figure", children=tokens(count)), span(cls="model__unit", children=what)],
        ),
    )


def priced(cost: Cost | None) -> tuple[Element, ...]:
    """
    What a million tokens in and a million out cost, as one figure pair.

    Both together because neither is a price on its own: a model that reads cheaply and writes
    expensively is the common shape, and showing one number would rank the list wrongly.
    """
    if cost is None:
        return ()
    return (
        span(
            cls=("model__fact", "model__fact--cost"),
            attrs={"title": f"{dollars(cost.input)} in and {dollars(cost.output)} out, per million tokens"},
            children=[
                span(cls="model__figure", children=f"{dollars(cost.input)}/{dollars(cost.output)}"),
                span(cls="model__unit", children="per Mtok"),
            ],
        ),
    )


def choosing(
    legend: str,
    toggle: str,
    names: Sequence[str],
    body: Element,
    *,
    identified: str | None = None,
    extra: str | None = None,
) -> Element:
    """
    One group of cards, folded down to the one that is picked.

    A wall of cards is what the start page used to be: seventy of them on a real gateway, so the
    choice already made was somewhere in a list you had to scroll, and everything after that list
    was past the end of it. Shut, a group is the card you picked and nothing else; open, it is
    everything on offer, in place.

    **The fold is a checkbox and the folding is `:has()`, so no script decides any of this.** That
    is what keeps a shut group honest: what it draws is the card whose radio is actually checked,
    read off the radio itself, so there is no second copy of the choice to go stale and nothing to
    keep in step. A summary line naming the model would have been that second copy, and with
    scripting off it would have named the wrong one the moment somebody picked.

    The count on the control is there rather than left to be inferred, because a shut group is a
    single card with nothing about it saying others exist. It is what makes the group read as a
    picker, and it has to be rendered *inside* whatever the endpoint swap replaces or it keeps
    saying 27 after the list under it became 46.

    `names` is what an open group can be narrowed by, and it is the same list the cards are drawn
    from rather than a second one: one argument gives the count, the datalist and the filter, so
    none of the three can disagree about what is on offer.
    """
    listed = f"{toggle}-names"
    return section(
        cls=("picker__part", extra),
        attrs={"id": identified},
        children=[
            # A real checkbox, so opening a group is the browser's own behaviour. No `name`, so it
            # is never submitted, and deliberately no `form`, so it is not associated with one
            # either: this is a fold, not part of what a session is decided by.
            input_(cls="picker__toggle", attrs={"type": "checkbox", "id": toggle}),
            div(
                cls="picker__head",
                children=[
                    h2(cls="picker__legend", children=legend),
                    label(
                        cls="picker__more",
                        attrs={"for": toggle},
                        children=[
                            span(cls="picker__more--shut", children=counted(len(names), "option")),
                            span(cls="picker__more--open", children="done"),
                        ],
                    ),
                ],
            ),
            narrowing(listed, names),
            body,
        ],
    )


def narrowing(listed: str, names: Sequence[str]) -> Element:
    """
    A box that narrows an open group to the cards matching what is typed.

    The `<datalist>` is what makes typing worth anything with no script: the browser completes a
    name from the same list the cards are drawn from, so a long model id is a few keystrokes either
    way and the completion menu is itself a way of reading what is on offer. The *narrowing* is the
    script's, which is why the cards remain the thing that actually answers the question - with the
    file absent this is a box that suggests and does not filter, and every card is still there to
    be picked.

    Taking an entry from that menu names one exactly, and the script reads that as the choice: the
    card is checked and the group shuts. So the box is two things at once - a filter while a name is
    partial, and a way of picking once it is whole - which is what the completion menu already
    implies it should be. Matching a whole name and never a prefix is what keeps the two apart, and
    what stops the keystrokes spelling one name choosing a shorter one on the way past.

    Drawn on every group rather than only the long one. Searching by name is the same question
    whether a list holds four endpoints or seventy models, and a control that appeared once a list
    passed some length would be one nobody learns to expect.
    """
    return div(
        cls="picker__filter",
        children=[
            input_(
                cls="picker__filter-field",
                attrs={
                    "type": "search",
                    "list": listed,
                    "placeholder": "find",
                    "aria-label": f"Find in {listed}",
                    "autocomplete": "off",
                },
            ),
            datalist(attrs={"id": listed}, children=[option(attrs={"value": name}) for name in names]),
        ],
    )


def model_cards(models: Sequence[Listed], reference: Reference | None, chosen: str | None = None) -> Element:
    """
    The models one endpoint offers, as the cards the form submits one of.

    The whole group carries the stable id, head and fold included, because changing the endpoint
    replaces exactly this: the count beside the legend is a fact about the list below it, so it has
    to travel with it. Replacing the group also resets the fold, which is the right state to arrive
    in - a new endpoint means a new default model, already picked and worth seeing shut.

    An endpoint always offers at least one model (discovery refuses one that lists none), so this is
    never an empty group nobody can submit.

    Grouped by provider, because a gateway fronting several vendors answers with seventy entries and
    an ungrouped wall of seventy cards is worse than the ungrouped list of seventy it replaced. The
    value is the id the request will name; the name is whatever the endpoint calls it.
    """
    picked = chosen if any(chosen == model.id for model in models) else models[0].id
    return choosing(
        "Model",
        MODEL_TOGGLE_ID,
        # One name per card, which is what keeps the count honest: the datalist offering the label
        # *and* the routed id would be two entries per model and a group announcing 54 options over
        # 27 cards. Searching by id still works, because the filter matches a card's whole text and
        # the id is printed on it; the completion list is the readable half, and a list of names
        # interleaved with `anthropic/claude-sonnet-4-6` is not the readable half.
        [model.label for model in models],
        div(
            cls="models",
            attrs={"role": "radiogroup", "aria-label": "Model"},
            children=[
                section(
                    cls="models__provider",
                    children=[
                        h2(cls="models__heading", children=provider),
                        div(
                            cls="models__grid",
                            children=[
                                model_card(describe(model, reference), chosen=model.id == picked) for model in found
                            ],
                        ),
                    ],
                )
                for provider, found in grouped(models)
            ],
        ),
        identified=MODEL_ID,
        extra="picker__part--models",
    )


def endpoint_card(links: Links, offering: Offering, chosen: bool) -> Element:
    """
    One endpoint as where it actually points, rather than as a name somebody chose for it.

    The endpoint and the wire are on it because they are what distinguishes two endpoints that
    otherwise read alike, and on this machine that is the ordinary case rather than the exotic one:
    one gateway answers both wires, so a VM declares the same host twice and the *only* thing
    telling those two rows apart is the word `anthropic` or `openai` and the `/v1` on the end.

    htmx sends a triggering input's own value, so the `hx-get` needs no interpolation: choosing an
    endpoint asks for that endpoint's models and replaces the whole model group with them, head and
    fold included, so the count beside the legend is the new list's rather than the old one's.
    Without a browser the form still posts, carrying whatever models the page was rendered with, and
    the handler refuses a pair nothing offers.

    `outerHTML` and deliberately not the `outerMorph` the transcript uses. Morphing preserves what a
    control already holds, which is exactly right for a conversation being reread and exactly wrong
    here: the whole point of this swap is that the model list is now a *different* list, and a merge
    would keep a card the new endpoint may not even offer.
    """
    return label(
        cls="endpoint",
        attrs={"data-name": offering.endpoint},
        children=[
            input_(
                cls="endpoint__pick",
                attrs={
                    "type": "radio",
                    "name": "endpoint",
                    "value": offering.endpoint,
                    "checked": chosen,
                    "form": CHOOSING_ID,
                    "hx-get": links.to_endpoint_models(),
                    "hx-target": f"#{MODEL_ID}",
                    "hx-swap": "outerHTML",
                    "hx-status:4xx": "swap:none",
                    "hx-status:5xx": "swap:none",
                },
            ),
            span(cls="endpoint__name", children=offering.endpoint),
            span(cls="endpoint__format", children=f"{offering.format} format"),
            span(cls="endpoint__url", children=offering.where),
            span(
                cls="endpoint__count",
                children=f"{len(offering.models)} model{'' if len(offering.models) == 1 else 's'}",
            ),
        ],
    )


def endpoint_cards(links: Links, catalogue: Catalogue, chosen: str | None = None) -> Element:
    picked = chosen if chosen in catalogue.offered else catalogue.default.endpoint
    return div(
        cls="endpoints",
        attrs={"role": "radiogroup", "aria-label": "Endpoint"},
        children=[endpoint_card(links, catalogue.offered[name], chosen=name == picked) for name in catalogue.endpoints],
    )


def thinking_card(naming: str, chosen: bool) -> Element:
    return label(
        cls="think",
        attrs={"data-name": naming},
        children=[
            input_(
                cls="think__pick",
                attrs={
                    "type": "radio",
                    "name": THINKING_FIELD,
                    "value": naming,
                    "checked": chosen,
                    "form": CHOOSING_ID,
                },
            ),
            span(cls="think__name", children=naming),
        ],
    )


def thinking_cards(chosen: ThinkingLevel | None) -> Element:
    """
    How hard to think, with no cascade behind it.

    Unlike the model list this is the same everywhere, because it is a property of the request
    rather than of the endpoint: every level is offered against every endpoint, and a model that
    cannot reason refuses or ignores it on the turn. That is the same stance the model id gets, and
    for the same reason - the provider's own answer about what it supports is the authoritative
    one, and gating here would hide a level that in fact works.

    Cards like the other three, which is worth more here than the control it replaced: eight levels
    is enough that a shut group saying `high` is a better answer than a select showing it, and
    naming them in one vocabulary means the same fold, the same count and the same narrowing serve
    every question this page asks.
    """
    picked = next((name for name, level in THINKING_CHOICES if level == chosen), None)
    return choosing(
        "Thinking",
        THINKING_TOGGLE_ID,
        [name for name, _ in THINKING_CHOICES],
        div(
            cls="thinks",
            attrs={"id": THINKING_ID, "role": "radiogroup", "aria-label": "Thinking"},
            children=[
                div(
                    cls="thinks__grid",
                    children=[thinking_card(name, chosen=name == picked) for name, _ in THINKING_CHOICES],
                )
            ],
        ),
    )


def override_placeholder(cap: int | None) -> str:
    """
    What the override box says while it is empty, for a model the console knows this much about.

    What leaving the box empty *sends*, said as the number where there is one, because that is the
    question somebody looking at the box is asking and "the model's limit" made them go and find it.
    Where nothing knows the number the sentence says so and says what happens, which is the adapter's
    own default going out on the wire: that is the one case the box exists for, so it is the case the
    placeholder has to make plain rather than the one it may leave vague.
    """
    return f"max ({tokens(cap)})" if cap is not None else "unknown, so the wire's default applies unless you set one"


def output_override_field(chosen: int | None, cap: int | None) -> Element:
    """
    The most one request may generate, as a box somebody may type a number into and usually will not.

    Not a `choosing` group, because there is no set to draw: it is one number or nothing, like a base.
    The placeholder says what leaving it does, which is the whole of what keeps one more field from
    being one more step. It is rendered here for the starting model and rewritten by the script from
    whichever card is picked, each card carrying its own sentence (`data-override`), so with the
    script absent it is right for the model the page opened on and with it present it follows the
    pick. `cap` is that starting model's number, or nothing where nothing knows it.

    "Override" is on the label rather than in a sentence under it, because the word is the precedence
    rule: a number here beats what the console knows, and empty is not an override rather than a zero
    or an unknown.
    """
    return div(
        cls="override",
        children=label(
            cls="override__field",
            children=[
                span(cls="override__label", children="Max output tokens override"),
                input_(
                    attrs={
                        "type": "text",
                        "id": OVERRIDE_ID,
                        "name": OUTPUT_OVERRIDE_FIELD,
                        "value": None if chosen is None else str(chosen),
                        "form": CHOOSING_ID,
                        "inputmode": "numeric",
                        "autocomplete": "off",
                        "placeholder": override_placeholder(cap),
                    }
                ),
            ],
        ),
    )


# The two answers to "what files does this session have" that are not a repository. Their values are
# the `Filesystem` members they mean, and that is unambiguous rather than lucky: a repository's id is
# `forge:key`, so it always holds a colon and can never be either of these.
ONLY_SCRATCH: Final = "only scratch"


WHOLE_MACHINE: Final = "whole machine"


WITHOUT_A_REPOSITORY: Final[tuple[tuple[str, Filesystem, str], ...]] = (
    (ONLY_SCRATCH, Filesystem.NOTHING, "a scratch directory of its own, and nothing else on this machine"),
    (WHOLE_MACHINE, Filesystem.EVERYTHING, "every file this console can reach, including its own"),
)


NETWORK_CHOICES: Final[tuple[tuple[str, bool, str], ...]] = (
    ("off", False, "commands cannot dial out"),
    ("on", True, "commands can reach anything this machine can"),
)


def network_card(naming: str, saying: str, chosen: bool) -> Element:
    return label(
        cls="network",
        attrs={"data-name": naming},
        children=[
            input_(
                cls="network__pick",
                attrs={
                    "type": "radio",
                    "name": NETWORK_FIELD,
                    # `on` and nothing, because a radio that is not checked posts no field at all and
                    # an absent field has to mean the safe answer. Spelling the off card's value as
                    # anything else would make "no field" and "the off card" two different strings
                    # meaning one thing, which is the shape that goes wrong when one of them is
                    # forgotten.
                    "value": "on" if naming == NETWORK_CHOICES[1][0] else "",
                    "checked": chosen,
                    "form": CHOOSING_ID,
                },
            ),
            span(cls="network__name", children=naming),
            span(cls="network__note", children=saying),
        ],
    )


def network_cards(chosen: bool) -> Element:
    """
    Whether a session's commands may dial out, offered wherever there are commands to run.

    On or off, and off rather than a list of hosts. An allowlist containing a code forge contains
    every gist on it and one containing a package registry contains a package anybody can publish,
    so what it would buy is a defence against a repository's own build script and very little
    against anything deliberate - at the price of a proxy in front of every command.
    """
    return choosing(
        "Network",
        NETWORK_TOGGLE_ID,
        [naming for naming, _, _ in NETWORK_CHOICES],
        div(
            cls="networks",
            attrs={"id": NETWORK_ID, "role": "radiogroup", "aria-label": "Network"},
            children=[
                div(
                    cls="networks__grid",
                    children=[
                        network_card(naming, saying, chosen=reaching is chosen)
                        for naming, reaching, saying in NETWORK_CHOICES
                    ],
                )
            ],
        ),
    )


TRUST_CHOICES: Final[tuple[tuple[str, bool, str], ...]] = (
    ("trusted", True, "Its own plugins run, confined to the checkout"),
    ("read only", False, "None of its own code runs unattended"),
)
"""
The two answers to whether this session runs code the repository carries, and what each means.

**Trusted comes first because it is the default**, and the default is the honest reading of what
picking a repository already means: a session with a shell runs its build, its tests, its hooks and
whatever those shell out to, every one of them unread. A plugin is one more caller of that.

What the second answer is for is the session where that reading does not hold - a stranger's pull
request being read rather than worked in, or a session on `only scratch` that picked a repository and
hands the model no shell at all. That is why it is drawn here rather than inferred from the
isolation: the two are near enough to look like one question and are not.
"""


def trust_card(naming: str, holds: bool, saying: str, chosen: bool) -> Element:
    """
    One answer to whether a repository's own code runs, as a card in the group.

    The network group's own classes rather than a set of its own, because it is the same control
    asking the same shape of question one row down: two answers, one word and a phrase apiece. A
    second set would be a second thing to restyle the day either moves.

    `holds` is the answer this card *is* and `chosen` is whether it is the one picked, which are two
    things a boolean each and easy to run together: the value posted is the card's own, and only the
    picked one carries `checked`.
    """
    return label(
        cls="network",
        attrs={"data-name": naming},
        children=[
            input_(
                cls="network__pick",
                attrs={
                    # `on` and nothing, by `network_card`'s rule inverted: a radio that is not checked
                    # posts no field, so an absent field has to mean the *default*, which here is
                    # trusted. Refusing is the thing somebody has to have actually said.
                    "type": "radio",
                    "name": TRUSTED_FIELD,
                    "value": "on" if holds else "",
                    "checked": chosen,
                    "form": CHOOSING_ID,
                },
            ),
            span(cls="network__name", children=naming),
            span(cls="network__note", children=saying),
        ],
    )


def trust_cards(trusted: bool) -> Element:
    """
    Whether this session runs code the repository carries, which today means the plugins it declares.

    **Per session and never per repository**, which is the whole shape of it: a repository changes,
    so an answer recorded against one covers a branch somebody pushed this morning as readily as the
    one you reviewed last year. Recorded on the `Choice` it is a decision about this conversation,
    settled before its first message and fixed for its life, and changing your mind is `fork`.

    Drawn only where a repository is picked, by the same rule the base and the branch follow: with no
    checkout there is nothing whose code could be trusted or not.

    The cost, stated: a repository's plugin runs unattended at every turn boundary and puts text into
    the conversation, which is a delivery channel for prompt injection with a guaranteed slot. The
    settings step is where every session then shows what it actually loaded, in those terms, before
    anything has been said to it - which is the part somebody can act on, since the grant is coarse
    and the exposure is what is made visible instead.
    """
    return choosing(
        "Repository code",
        TRUST_TOGGLE_ID,
        [named for named, _, _ in TRUST_CHOICES],
        div(
            cls="networks",
            attrs={"id": TRUST_ID, "role": "radiogroup", "aria-label": "Repository code"},
            children=[
                div(
                    cls="networks__grid",
                    children=[
                        trust_card(named, holds, saying, holds is trusted) for named, holds, saying in TRUST_CHOICES
                    ],
                )
            ],
        ),
    )


def starting_at(repository: str | None, base: str | None, branch: str | None, branches: Sequence[str] = ()) -> Element:
    """
    Where in the repository the checkout starts, and what branch it starts there.

    Free text and not cards, because neither has a closed set to draw: a commit-ish is anything `git
    rev-parse` resolves, and a branch is a name that does not exist yet. `choosing` is the component
    for a question with answers to show, and putting an unbounded one behind it would mean either
    drawing a card per ref a repository has or drawing a card that is really a text box.

    What the branches do instead is **narrow it**: they are drawn under the box and cut to what
    matches as it is typed, so the field is a search over what the repository has and still takes a
    tag, a hash or `main~3`. One box rather than the cards every other question here gets, because
    those cards *are* the answer where these only fill one in - and a card posting `base` beside a
    field posting `base` would be two places one value could come from.

    It is drawn twice on purpose and that is not a copy to keep in step: a `<datalist>` for the
    browser's own completion, and a list the script narrows. Both come from `branches` in this one
    call, and exactly one is ever live, because enhancing the field removes the `list` attribute that
    makes the first one work.

    **With no repository there are no fields, and the block is an empty anchor.** A base and a branch
    are answers *about* a repository, so with `only scratch` or `whole machine` they are two boxes
    asking a question the session does not have - and `Choice.settled` drops whatever they hold
    anyway, which is a form saying one thing and a record keeping another. The new-session page
    draws no block for such a workspace, so this arm is what `/fragments/branches` answers for one.

    With htmx absent the fields are still there, drawn with no completions by the page itself, so
    what is given up is only the list of what the repository has.

    Both are optional and the placeholders say what leaving them does, which is the whole of what
    keeps two more fields from becoming two more steps: blank is the repository's default branch as
    it stands now, on a branch this console names after the session.

    **Two fields and not one, because naming a base cannot check that branch out.** Git refuses a
    branch another checkout already holds, so a session started at `main` that was left *on* `main`
    would stop the next one planting at all. So one says where to begin and the other says what to
    begin, and the placeholder on this one has to say so rather than implying the first answers both.
    """
    # The branches come off a forge, so the completions *arrive* after the page does: on a cold clone
    # that is seconds of a field with nothing under it, which reads as a repository with no branches.
    # The dots are drawn in both shapes because both are what the swap can land on. They take no room
    # until the request starts; see `.basis__loading`.
    loading = working(saying="loading branches", extra="basis__loading", identified=BASIS_LOADING_ID)
    if repository is None:
        return div(attrs={"id": BASIS_ID}, children=loading)
    return div(
        cls="basis",
        attrs={"id": BASIS_ID},
        children=[
            loading,
            label(
                cls="basis__field",
                children=[
                    span(
                        cls="basis__label",
                        children=[
                            "Start at",
                            # Said only where there is something to say it about, because "0
                            # branches" on a repository nobody could reach reads as a fact about the
                            # repository rather than about this console not having asked.
                            *(
                                (span(cls="basis__count", children=counted(len(branches), "branch", "branches")),)
                                if branches
                                else ()
                            ),
                        ],
                    ),
                    input_(
                        cls="basis__box",
                        attrs={
                            "type": "text",
                            "name": BASE_FIELD,
                            "value": base,
                            "form": CHOOSING_ID,
                            # The browser's own completion, which is the whole of what this field has
                            # with the script absent. `mainplate.js` takes this attribute *off* at
                            # the moment it takes the narrowing over, because two dropdowns over one
                            # box is one more than a reader can use. See `paintBranches`.
                            "list": BRANCHES_ID,
                            "maxlength": str(LONGEST_REF),
                            "autocapitalize": "off",
                            "autocomplete": "off",
                            "spellcheck": "false",
                            "placeholder": "a branch, tag or commit (or leave it at the default branch)",
                        },
                    ),
                    datalist(attrs={"id": BRANCHES_ID}, children=[option(attrs={"value": name}) for name in branches]),
                    # The same names again, as the list the script narrows and shows under the box.
                    # Not a second copy to keep in step: both are rendered from `branches` in this one
                    # call, and exactly one of them is ever live, because enhancing removes the
                    # `list` above.
                    #
                    # `hidden` from the server and shown by the script, so with the file absent it
                    # stays out of the flow rather than being a wall of fifty branches under a field
                    # nobody has typed in.
                    ul(
                        cls="basis__found",
                        attrs={"id": FOUND_ID, "hidden": True},
                        children=[
                            li(
                                children=button(
                                    cls="basis__found-one",
                                    # A `button` and not the `<label>` with a radio in it that every
                                    # card in this picker is. Those *are* the answer; these only fill
                                    # in the one answer beside them, and a second control posting
                                    # `base` would be two places one value could come from.
                                    attrs={"type": "button", "data-branch": name},
                                    children=name,
                                )
                            )
                            for name in branches
                        ],
                    ),
                ],
            ),
            branch_field("New branch", branch, "name one (or leave it off and this session gets its own)"),
        ],
    )


def branch_field(saying: str, branch: str | None, placeholder: str) -> Element:
    """
    The box that names the branch a checkout is left on, as the new-session page and the fork page ask it.

    One field in two places rather than two fields, because it is one question with one parse behind
    it: whatever is typed goes through `parse_branch` on the way in, and empty means a branch this
    console names after the session. What differs is only what the box is called and what leaving it
    empty says, since a new session starts one and a fork is offered the one its parent was on.
    """
    return label(
        cls="basis__field",
        children=[
            span(cls="basis__label", children=saying),
            input_(
                attrs={
                    "type": "text",
                    "name": BRANCH_FIELD,
                    "value": branch,
                    "form": CHOOSING_ID,
                    "maxlength": str(LONGEST_REF),
                    "autocapitalize": "off",
                    "autocomplete": "off",
                    "spellcheck": "false",
                    "placeholder": placeholder,
                }
            ),
        ],
    )


def picker(
    links: Links,
    catalogue: Catalogue,
    reference: Reference | None,
    chosen: Choice | None = None,
    naming: Placed = None,
    acting: Placed = None,
    leading: Placed = None,
) -> Element:
    """
    Everything a session is decided by, laid out as the question it actually is.

    **What files the session has is never asked here.** A new session's was answered by the press on
    the dashboard, and a fork's is its parent's, so on both pages it is settled before this is drawn,
    and a control for it would be a lie about what the page does.

    `leading` is what the page puts above every question, which on the new-session page is the
    workspace already answered and where in it to start, and on the fork page is the branch the fork
    carries on. Handed in for the reason `naming` is: the two pages ask the same questions and have
    different things to say before them, and a fork of a session in no repository has nothing.

    One block rather than a row of selects, because choosing a model is the one real decision on
    this page and a row of selects made it look like a footnote to the message box.

    **The order is what a session is decided by, widest first: what it may do with its files, what
    answers it, which model, how hard that model thinks, and how much it may say.** The network and
    the repository's code come first because they are the broadest of what is left and decide what
    the agent can do at all; the endpoint and the model are next and are adjacent because they are a pair, the list
    being whatever the endpoint above it offers; the thinking level and the output override are last
    because they are settings on the model rather than choices beside it, and the override after the
    level because it is the one almost nobody touches.

    Both card groups are folded down to what is picked (see `choosing`), so the order above is what
    a reader sees rather than what they would reach after scrolling: four labelled lines and the two
    cards that are the current choice. Opened, the models are the one part with no bound on their
    length - the list is as long as whatever gateway you are pointed at makes it, where everything
    else here is a fixed handful of rows - and nothing gives up height for it: the choosing scrolls
    as one box, and a pick shuts the list again.

    `chosen` is what the controls start on, defaulting to the configured default for a new session.
    A fork passes the parent's own choice instead, so continuing on the same model is the path that
    needs nothing touched: the fork exists to let the choice change, not to require it.

    A choice naming an endpoint the catalogue no longer has falls back to the default rather than
    rendering a picker with nothing selected. That is the same case `stalled_by` explains on the
    session page, and here there is a sensible thing to show.
    """
    starting = chosen if chosen is not None and chosen.endpoint in catalogue.offered else catalogue.default
    return div(
        cls="picker",
        children=[
            leading,
            # The network sits under what the page leads with and above the endpoint, because that is
            # the order of breadth: what a session's files are decides what it can touch, whether it
            # can dial out decides what it can do with them, and the endpoint and model only decide
            # who answers.
            network_cards(starting.isolation.network),
            # Whether the repository's own code runs, directly under what the session can reach and
            # whether it can dial out, because it is the third question about the same subject: what
            # this session's files are, what may be done with them, and whose code runs in them.
            # Drawn only where a repository is picked, since with none there is nothing to trust.
            *((trust_cards(starting.trusted),) if starting.repository is not None else ()),
            choosing(
                "Endpoint",
                ENDPOINT_TOGGLE_ID,
                list(catalogue.endpoints),
                endpoint_cards(links, catalogue, starting.endpoint),
            ),
            model_cards(catalogue.offered[starting.endpoint].models, reference, starting.model),
            thinking_cards(starting.thinking),
            output_override_field(starting.output_override, output_cap_of(catalogue, reference, starting)),
            # What to call it, last, because it is the one question here that decides nothing about
            # how the session runs: everything above it is what the session *is*, and this is what a
            # reader will call it. Handed in rather than drawn here, because the fork page asks the
            # same five questions and has nothing to name.
            naming,
            # And what acts on all of it, under the last question rather than pinned below the
            # picker. Pinned it needed a row of its own that `main`'s grid had to hold open, and a
            # reader who has answered the last question is already looking at the bottom of the list.
            acting,
        ],
    )


def where_it_works(repository: str, branch: str | None) -> str:
    """
    Where a session's files are, as a person reads it: the repository, and the branch where one is recorded.

    The branch, because that is what somebody about to push needs to know and the one part
    they cannot work out from the repository's name - a generated one especially, since it is named
    after the session rather than after anything they typed. Where the session *began* is settled
    and on the first turn's own rule; this is where it is now. Conditional for the sessions recorded
    before every one had a branch.

    The card in the rail says the same two things as two rows rather than through this, because a
    row holds one value and a sentence holds a phrase; what the two share is the branch itself.
    """
    return f"{repository} @ {branch}" if branch is not None else repository


UNNAMED: Final = "Named after the first thing said in it"
"""What an empty name box says will happen, wherever a session can be named before it is."""


def naming() -> Element:
    """
    What to call this session, as one more of the picker's questions rather than a stray box.

    **Drawn like every other control on the page**, which is what its own class used to prevent: it
    was a field above a message box, and there is no message box here any more, so a full-width input
    in the composer's own idiom read as the thing somebody came to type rather than as the optional
    half of a choice. Legend, then field, in the shape `starting_at` already draws a base and a
    branch in.

    Optional, and the placeholder says what happens if you leave it: a session with no name given is
    named after its first message, exactly as every session was before this existed.

    A plain input with no `hx-` attribute on it, because it is submitted with the choices rather than
    being a question of its own: nothing exists to name until the form posts.
    """
    return div(
        cls="naming",
        children=[
            div(
                cls="basis__field",
                children=[
                    span(cls="basis__label", children=span(cls="picker__legend", children="Name")),
                    input_(
                        attrs={
                            "type": "text",
                            "name": TITLE_FIELD,
                            "maxlength": str(TITLE_LENGTH),
                            "placeholder": UNNAMED,
                            "aria-label": "Session name",
                        }
                    ),
                ],
            )
        ],
    )
