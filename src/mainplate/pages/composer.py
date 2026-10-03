# The message box and what can happen to what was typed into it: the sending menu, each plugin's
# leader, and the note above the box saying what putting the conversation to the model again costs.

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from without_html import Element
from without_html import button
from without_html import details
from without_html import div
from without_html import form
from without_html import label
from without_html import p
from without_html import span
from without_html import summary
from without_html import textarea

from mainplate.context import Entry
from mainplate.context import leaders as context_leaders
from mainplate.conversation import DISPOSITION_FIELD
from mainplate.conversation import KEEP
from mainplate.conversation import LEADERS
from mainplate.conversation import Disposition
from mainplate.pages.document import Placed
from mainplate.pages.figures import charged
from mainplate.pages.figures import tokens
from mainplate.pages.moments import Reader
from mainplate.pages.moments import stamped
from mainplate.pages.moments import timed
from mainplate.pages.picker import where_it_works
from mainplate.pages.transcript import TRANSCRIPT_ID
from mainplate.plugins.installed import Enrolled
from mainplate.plugins.protocol import AnswerInput
from mainplate.service import Conversation

# What a send does, which is the same merge plus a scroll: a message just typed is the one thing a
# reader definitely wants to be looking at, and unlike an update arriving on its own this cannot
# fight somebody reading further up, because they were typing. What the stream sends carries no
# scroll at all; following the end is the dock's to offer and the reader's to switch off.
SEND_SWAP: Final = "outerMorph scroll:bottom"


SENDING_ID: Final = "sending"


# The box you type in, named so the row of tools under it can be its label: a press on the row where
# no tool is then puts the cursor in the box, with no script, and a press on a tool is the tool's.
MESSAGE_ID: Final = "message"


# Whether the provider still holds this conversation's prefix, and what the next request pays if it
# does not. A region of its own with an id, because it sits in the composer and the composer is not
# what the stream replaces: what it says goes stale on every turn, since the context it prices grows
# with each one. So the page's one connection carries it as a second partial, which is the shape
# `streaming.py` was built for. See `cache_note`.
CACHE_ID: Final = "cache"


def cache_note(showing: Conversation, reader: Reader) -> Element:
    """
    Whether the provider still holds this conversation's prefix, and what the next request pays if not.

    Meaningless before there was a cache, and worth a line now that there is one: a conversation picked
    up after lunch pays full input price for everything said in it, and nothing about the request looks
    any different. On a long conversation that is most of the bill.

    **A figure rather than a warning, in the family of the gauge and the `▣` count.** It does not tell
    anybody to `forget`: at low utilization the right move is to carry on, and choosing which figure
    matters is the reader's. So it says what is true and stops.

    **One-sided, always.** Past the retention a prefix is cold and this says so; under it nothing can
    be asserted, because eviction is unobservable from here, so what it says is `warm as of 12m` - a
    claim about when the prefix was last *written*, which is what a response landing is, and which is
    true on any wire whatever that wire's own TTL.

    **The threshold is the answering wire's own**, which `Conversation.retention` carries, rather than
    one duration for every session. A single constant has to be the longest of them or `cold` stops
    being sound, and the shorter-retaining wire then spends the difference drawing a prefix it has
    certainly dropped as one written a little while ago - half an hour of it, on the OpenAI wire.
    Without a retention to read, nothing is asserted at all: the state is the write time and
    `data-retention` is left off, since an unknown duration is one nothing has outlasted.

    **The server renders an absolute time and the script renders the relative one.** Nothing here is
    re-rendered on the clock - the stream sends this when the session *records* something, and the
    interval that matters is exactly the one where nothing is recorded - so a server-rendered `warm`
    would sit there while the retention rolled past it. `cached at 15:09` is a fact that cannot rot,
    which is what a reader with no script gets; `data-since` and `data-retention` are what the script
    needs to say `cold` or `warm as of 12m`, and it measures the rest against its own clock from the
    moment it first saw them, so no two machines' clocks are ever subtracted. See `wireCache`.

    That absolute time is the reader's own clock rather than the console's, which is the same `Reader`
    every other moment on the page is drawn against and arrives the same way; see `ZONE_COOKIE`.

    Cold is the one state the server *can* assert, since it was already true when this was rendered
    and nothing makes a cold prefix warm again.

    **The money is a floor and says so.** What it prices is the input of the next turn's *first*
    request - re-sending what has already been said - and not the answer, the tools that turn runs, or
    the further requests it makes, any of which can dwarf it. A bare figure would read as what the
    next turn costs and understate it by however much work that turn turns out to be, so it carries a
    `+` and the title spells out what sits on top. That is the one thing about a turn nobody has
    started that can be stated exactly rather than guessed at.

    **The money is absent where the price is**, exactly as the gauge's fraction is: no reference
    database, an endpoint that no longer lists the recorded id, a model with no record, or a record
    with no price. What is left is when the prefix was written, which is worth saying on its own.

    Empty before anything has been answered, and empty rather than absent because it is what the
    stream's partial targets - the same reason `starting_at` leaves a block behind with no repository.
    """
    if showing.said.answered_at is None or showing.since is None:
        return p(cls="cache", attrs={"id": CACHE_ID})
    context = showing.said.total.context
    cold = showing.retention is not None and showing.since >= showing.retention
    figures: list[Element] = [
        span(
            cls="cache__state",
            attrs={
                "title": (f"This conversation's prefix was last written at {stamped(showing.said.answered_at, reader)}")
            },
            children="cold" if cold else f"cached at {timed(showing.said.answered_at, reader)}",
        )
    ]
    if showing.resending is not None:
        held = showing.resending
        # `▣` for the cached end, which is the mark the rule already uses for the part of an input a
        # provider read from its cache. Labelling them `warm` and `cold` instead would put those two
        # words on the line twice over, since the state beside them is already one of the two.
        said = (
            charged(held.cold)
            if held.warm is None
            else f"\N{WHITE SQUARE CONTAINING BLACK SMALL SQUARE}{charged(held.warm)} / {charged(held.cold)}"
        )
        spread = (
            f"${held.cold:f} with none of it read from a cache"
            if held.warm is None
            else f"between ${held.warm:f} with all of it read from a cache and ${held.cold:f} with none of it"
        )
        figures.append(
            span(
                cls="cache__cost",
                attrs={
                    "title": (
                        f"Re-sending the {context:,} tokens already said costs {spread}, estimated from "
                        f"published rates and not billed. That is where the next turn *starts*: what it "
                        f"answers with, the tools it runs and any further requests it makes are all on top."
                    )
                },
                children=[
                    "\N{MIDDLE DOT} ",
                    # The `+` is the whole of what keeps this honest on the line, and it applies to both
                    # ends. What they price is the *input of the next turn's first request* and nothing
                    # else - not the answer, not the tool calls, not the further requests a turn of any
                    # size makes - so a bare figure would read as what the next turn costs and understate
                    # it by however much work the turn turns out to be. A floor is what can be said
                    # exactly, and the pair is what says what waiting costs.
                    f"\N{UPWARDS ARROW}{tokens(context)} at {said}+",
                ],
            )
        )
    return p(
        cls="cache",
        attrs={
            "id": CACHE_ID,
            # Seconds rather than the absolute time, so the script adds to a duration the server
            # measured instead of subtracting one clock from another. See the docstring.
            "data-since": str(int(showing.since.total_seconds())),
            # Absent where no retention is known, which drops the attribute and leaves the script
            # with nothing to call cold against - the same one-sidedness the server keeps.
            "data-retention": None if showing.retention is None else str(int(showing.retention.total_seconds())),
        },
        children=figures,
    )


@dataclass(frozen=True, slots=True)
class Answer:
    """
    One thing that can happen to what you typed.

    **One word used three times.** `leader` is what the menu row is called, what is typed after `/`
    to reach it from the keyboard, and what the form carries in `data-leading` while the box is in
    its mode. Where it names a disposition it *is* that disposition's recorded value, so the word on
    the page, the word on the keyboard and the word in the database cannot come apart; `keep` is the
    one answer with no disposition behind it, because it sends the text nowhere.

    That is also why there is no separate label. A button reading `Fork` and a leader spelled
    `/branch` would be a synonym to keep in step for ever, so what a control says is the word itself.

    `staying` is whether the box is still in this mode once what was typed has gone, and it is each
    answer's own answer rather than one rule over all of them. A run of commands is what `Run` is for,
    so it stays; everything else is a thing you meant once, so the box comes back to `Send` and the
    next message goes where a message normally goes. It is carried on the button rather than kept in a
    list in the script, for the reason every other fact about a mode is: there is one place that
    decides what an answer is, and it is here.
    """

    leader: str
    saying: str
    posts: Mapping[str, str | int | bool | None]
    staying: bool
    input: AnswerInput = "required"
    """Required text, an optional note, or no text with immediate submission."""

    @property
    def named(self) -> str:
        """What it is called, which is its own word: there is no second name to drift from."""
        return self.leader.capitalize()


def dispatched(
    disposition: Disposition, saying: str, *, staying: bool = False, input_policy: AnswerInput = "required"
) -> Answer:
    """One answer that posts a disposition, named after the value it posts."""
    return Answer(
        leader=disposition.value,
        saying=saying,
        posts={"type": "submit", "name": DISPOSITION_FIELD, "value": disposition.value},
        staying=staying,
        input=input_policy,
    )


PLUGIN_LEADER: Final = "plugin:"
"""
What a plugin's own answer posts in the disposition field, ahead of the leader it owns.

A prefix rather than a field of its own, because a submit button carries one name and one value and
the menu row, the mode button and the sentence are all that one button. The boundary reads the prefix
first and everything after it is the leader somebody typed, which is a word the session's own plugins
answer to rather than one this console has a list of.
"""


def plugin_answers(plugins: Sequence[Enrolled]) -> tuple[Answer, ...]:
    """
    Everything this session's plugins offer to do with what you typed, each under its own leader.

    **Declared once by the plugin and rendered three times by the console**, exactly as the console's
    own answers are: a row in the menu, the button the box shows in that mode, and the sentence above
    it. That is what stops a plugin's control drifting from the console's the first time anything is
    restyled, and it is the whole argument for a card being declared rather than drawn.

    `input` is the plugin's one choice: required message, optional note, or no text at all.
    The last submits the menu row as soon as the leader is chosen; the route refuses text if a
    caller posts directly. The optional answer keeps its mode for a note.
    """
    return tuple(
        Answer(
            leader=leader,
            saying=declared.saying,
            posts={"type": "submit", "name": DISPOSITION_FIELD, "value": f"{PLUGIN_LEADER}{leader}"},
            # Never, because a plugin's answer is a thing somebody meant once. `Run` is the one mode
            # the box stays in, and it stays because a session that reaches for it reaches again a
            # line later; nothing here can claim that of somebody else's answer.
            staying=False,
            input=declared.input,
        )
        for plugin in plugins
        for leader, declared in plugin.answers()
    )


CONTEXT_LEADER: Final = "context:"
"""A skill or command selected visibly in the composer, not parsed off message text."""


def context_answers(entries: Sequence[Entry], plugins: Sequence[Enrolled]) -> tuple[Answer, ...]:
    """One menu row and mode per manual skill or command invocation."""
    taken = frozenset(LEADERS) | frozenset(name for plugin in plugins for name, _ in plugin.answers())
    offered = context_leaders(tuple(entries), taken)
    bare = frozenset(entry.name for entry in entries if entry.name in offered)
    visible = {name: entry for name, entry in offered.items() if name in bare or entry.name not in bare}
    return tuple(
        Answer(
            leader=leader,
            saying=f"Load {entry.kind} {entry.qualified} and send it with what you typed",
            posts={"type": "submit", "name": DISPOSITION_FIELD, "value": f"{CONTEXT_LEADER}{leader}"},
            staying=False,
        )
        for leader, entry in visible.items()
    )


def sending_answers(
    returning: bool, answering: bool, runs_in: str | None, connected: bool = False
) -> tuple[Answer, ...]:
    """
    Everything that can happen to what you typed, other than the thing Send already does.

    `runs_in` is where a command would run, said in the sentence over the box because the box is the
    one place the branch has to be legible: a commit typed there lands on it, and `Push` sends it.
    Nothing where the session has nowhere to run one, which is also what leaves `Run` and `Push` out
    of the menu. `connected` is whether the session's commands already have the network, which is
    what leaves `Online` out: with it on, `Run` is already that.

    **Declared once and rendered from the same answer**: the menu row posts directly, and the
    button and sentence of a mode use that answer too. `Push` needs no mode: the answer says to
    submit it when its leader is chosen. No list of words in the script decides that.

    Ordered by how far the text travels: waiting for the next turn keeps it here and merely later,
    `Forget` keeps it here and drops what the model was told, a `Handoff` keeps it here and has the
    session write down what the model should be told instead, `Parent` reaches the conversation this
    one came out of, `Run` is not a message at all, `Online` is `Run` that can reach past the machine,
    `Commit` is a `Run` whose command is written for you and whose message is what you typed, `Push`
    takes nothing from the box and goes at once to the repository, and `Keep` sends it nowhere.
    `Commit` and `Push` are shortcuts for what `Run` and the store would do anyway, and neither
    stages anything: what goes in a commit is the person's, the model's, or a plugin's to decide.

    **Nothing here forks.** A fork happens through the link on a boundary's rule, where its
    conversation prefix and checkout state are settled. The rule at a completed live end or an
    archived end carries the whole conversation and asks nothing again; the composer only sends
    what is typed into the chosen conversation.

    **A plugin's own answers are not here**, and they are appended by `composer` rather than merged
    into this list: what a session offers depends on what it loaded, where these are the
    console's own and are the same on every session. `handoff` used to be one of these and is now the
    bundled handoff plugin's leader, which is what makes the pair worth keeping apart - this list is
    a constant, and that one is read off a session.
    """
    return (
        *(
            (
                dispatched(
                    Disposition.NEXT,
                    "Queue it behind the reply that is coming instead of putting it to the model now",
                ),
            )
            if answering
            else ()
        ),
        dispatched(
            Disposition.FORGET,
            "Ask it with the model's context cleared, leaving the whole conversation on the page",
        ),
        *(
            (dispatched(Disposition.PARENT, "Send it to the conversation this one was forked out of"),)
            if returning
            else ()
        ),
        *(
            (
                dispatched(
                    Disposition.RUN,
                    f"Run it in {runs_in}, in the session's sandbox, without telling the model",
                    # The one answer the box stays in, because a command is rarely the only one: a
                    # session that reaches for `Run` reaches for it again a line later, where every
                    # other answer here is a thing somebody meant once.
                    staying=True,
                ),
            )
            if runs_in is not None
            else ()
        ),
        *(
            (
                dispatched(
                    Disposition.ONLINE,
                    f"Run it in {runs_in} with the network on, where it can reach the repository as you",
                    staying=True,
                ),
            )
            if runs_in is not None and not connected
            else ()
        ),
        *(
            (dispatched(Disposition.COMMIT, f"Commit what is staged in {runs_in}, with this as the message"),)
            if runs_in is not None
            else ()
        ),
        *(
            (
                dispatched(
                    Disposition.PUSH,
                    f"Push the session's branch in {runs_in} to its repository, as you",
                    input_policy="none",
                ),
            )
            if runs_in is not None
            else ()
        ),
        Answer(
            leader=KEEP,
            saying="Put it on the shelf, unsent, and clear the box",
            posts={"type": "button", "data-shelf": KEEP},
            staying=False,
        ),
    )


def sending_option(answer: Answer, refusing: bool) -> Element:
    """
    One answer as a row in the menu.

    What it does is written under its name rather than left to a `title`, because a control somebody
    opened a menu to find is one they have not used before, and a tooltip is not where anybody looks
    first. The leader is printed beside the name for the same reason the Send button names
    Shift-Enter: a shortcut nothing on the page mentions is one nobody uses.
    """
    return button(
        cls="sender__option",
        attrs={
            **answer.posts,
            "disabled": refusing,
            "data-leader": answer.leader,
            "formnovalidate": answer.input != "required",
            "data-immediate": answer.input == "none",
        },
        children=[
            span(
                cls="sender__option-head",
                children=[
                    span(cls="sender__option-name", children=answer.named),
                    span(cls="sender__option-leader", children=f"/{answer.leader}"),
                ],
            ),
            span(cls="sender__option-said", children=answer.saying),
        ],
    )


def sending_leader(answer: Answer, refusing: bool) -> Element:
    """
    One answer as the button the box shows while it is in that answer's mode.

    The *same* control as the menu row, rendered beside Send rather than made out of it by the
    script. One button per answer and one of them visible, because what makes a leader safe is that a
    reader can see which one they are about to press: a single button whose name, value and label the
    script rewrote would be exactly the `Send` that forks this console refuses everywhere else.

    `data-staying` is how the script learns whether the mode outlives what was just sent, which is the
    same bargain: the answer decides, the button carries it, and there is no second list to keep in
    step with this one.
    """
    return button(
        cls="sender__leader",
        attrs={
            **answer.posts,
            "disabled": refusing,
            "data-leader": answer.leader,
            "data-staying": answer.staying,
            "formnovalidate": answer.input != "required",
            "title": "Shift-Enter \N{MIDDLE DOT} Escape to go back to a message",
        },
        children=answer.named,
    )


def sending_control(refusing: bool, answers: Sequence[Answer]) -> Element:
    """
    What happens to what you typed: send it, and everything else folded behind a caret beside it.

    **One question, so one control.** Sending it here, asking it in a new session, and setting it
    aside unsent are answers to "what do I do with this", and answering one question in two places is
    what this console removes wherever it finds it - which is exactly what a `Keep` button standing
    beside `Send` had become. It is also the only shape that stays affordable: each further answer
    costs a line in a menu nobody has to open, where each further button costs a slot in the row under
    the message box, which is the row a phone has least of.

    **`Send` does not say whether it steers, because it cannot know and neither can the reader.** This
    page was rendered from a checkpoint that has moved since, so a `Steer` button beside `Send` asked
    somebody to choose between two moments against a state that no longer held, and the server then
    honoured a decision about the wrong turn. `Service.send` decides instead, reading the record and
    writing to it in one breath.

    What is left for the menu is the one thing the record cannot settle: wanting to be answered
    *after* the reply that is coming. It is offered only while something is being answered, since
    with nothing running it is what `Send` already does.

    **Everything that *sends* works with no script.** The fold is a `<details>`, which is how
    everything else here folds, and each destination posts its own `name`/`value` the way the browser
    has always submitted a named button. `Keep` is the exception and is honestly the odd one out: the
    shelf is `localStorage`, so that row does nothing with `mainplate.js` absent, exactly as the
    shelf's own card in the rail shows nothing then.

    It deliberately does *not* remember what was chosen last, which is where GitHub's version of this
    control goes further. That would mean a button labelled `Send` that forgets, which is the one
    failure a control like this can have that nobody notices until after it has happened; here what a
    button says is always what it does, and a mode is only ever entered by asking for it by name.

    **A leader is a shortcut to a row and never a second way to say it.** With `mainplate.js`
    present, typing `/forget ` into an empty box - or `! `, which is `/run`'s own key - turns the box
    into that answer's box: the button beside it says `Forget`, and a sentence above it says what
    will happen. **The space is what commits it**, and until it is pressed the word is ordinary text with
    the menu open beside it, so nothing happens on a keystroke somebody was in the middle of. The
    answers taking no text post on that press instead of showing an empty mode. The
    server parses no leader out of what was posted, so a paragraph that opens with `/` is a
    paragraph, and the menu is what works with the file absent.
    """
    send = button(
        # Classed rather than found by position, because the script has to name it: it is the
        # submitter `requestSubmit` is handed, so that the keyboard posts the same pair the button
        # would. `form.querySelector('button[type=submit]')` matched it today and would have matched
        # a menu row the moment one was rendered above it.
        cls="sender__send",
        # The key is named on the button because otherwise nothing on the page says it exists, and a
        # shortcut nobody can find is one nobody uses.
        attrs={"type": "submit", "disabled": refusing, "title": "Shift-Enter"},
        children="Send",
    )
    return div(
        cls="sender",
        children=[
            send,
            *(sending_leader(answer, refusing) for answer in answers),
            details(
                cls="sender__more",
                children=[
                    summary(
                        cls="sender__caret",
                        attrs={"aria-label": "What else to do with this", "title": "What else to do with this"},
                        children="\N{BLACK DOWN-POINTING SMALL TRIANGLE}",
                    ),
                    div(
                        cls="sender__menu",
                        children=[sending_option(answer, refusing) for answer in answers],
                    ),
                ],
            ),
        ],
    )


def composer(
    action: str,
    *,
    context: Sequence[Entry] = (),
    refusing: bool = False,
    returning: bool = False,
    answering: bool = False,
    runs_in: str | None = None,
    connected: bool = False,
    above: Placed = None,
    identified: str | None = None,
    plugins: Sequence[Enrolled] = (),
) -> Element:
    """
    The box you type in, which posts to `action`.

    Sending swaps the transcript and leaves the address bar alone, since the box is only ever on a
    session's own page and the session already exists.

    `refusing` disables the whole thing, for a session nothing can answer. The disabling is real
    rather than styling: a box that still submitted would record a message into a session whose
    endpoint is gone, which is one more thing to explain and nothing gained.

    The reset is on `after:swap` rather than on `after:request`, so the box empties when the
    conversation on screen has actually taken the message rather than when the request left.

    `hx-indicator` names what is shown while the post is in flight, which is a different thing
    from the working dots in the transcript: this one says *your message has not landed yet*, and
    it is over in a round trip. The one in the transcript says the model has not answered yet, and
    is read off the checkpoint rather than off a request.

    `identified` is what the picker's controls name to reach this form from outside it, and is given
    only on the page that has one: an id nothing points at would say there is something here to
    associate with.

    **The menu is drawn before the first message as well as after it.** The composer is only ever on
    a session's own page, so there is always a session for the shelf to belong to, and `Run`,
    `Commit` and `Push` wherever `runs_in` names somewhere, and nothing among the answers forks. It is also the only chance: the first
    message swaps the transcript and never the composer, so a menu left off an empty conversation
    stays off until the page is loaded again.

    `runs_in` names where a command the person types would run, and is what puts `Run` among the
    answers: nothing where this session has nowhere to run one. The name is in the argument rather
    than looked up by the mode, because the sentence over a command box is where somebody about to
    commit or push reads which branch it lands on, now that nothing under the box says. Nothing else
    is needed to gate the mode: the script can only put the box into a mode the server drew a button
    for, so a page for a session with no repository has no run mode to enter and the two cannot
    drift. `connected` is whether those commands already have the network; see `sending_answers`.

    **Everything that is not the box sits above it, and nothing sits under it.** The composer is the
    bottom of the page, so a row appearing anywhere in its column pushes everything above that row
    upward - and with the mode's sentence under the box, entering a mode moved the box itself out
    from under the cursor. Above it, what grows is the composer's top edge and the box stays exactly
    where it was. The same holds for `sending`, which appears for the length of a round trip, and it
    is why the box is the last thing on the page at all: on a phone the keyboard comes up under
    whatever is focused, and a row under the box is a row the keyboard covers or the browser has to
    scroll past. What is above it is only what the next press depends on - what re-sending costs,
    and what the press will do - and what the session *is* stands in the rail; see `about_card`.
    """
    answers = (
        *sending_answers(returning, answering, runs_in, connected),
        *plugin_answers(plugins),
        *context_answers(context, plugins),
    )
    driving = {
        "hx-post": action,
        "hx-target": f"#{TRANSCRIPT_ID}",
        "hx-swap": SEND_SWAP,
        "hx-indicator": f"#{SENDING_ID}",
        "hx-on:htmx:after:swap": "this.reset()",
        "hx-disable": "find button, find textarea",
        # A refusal is not a transcript, so it must not become one. The box is `required`, so the
        # only way to reach this is a caller that is not this page; leaving the conversation on
        # screen is the honest answer to that.
        "hx-status:4xx": "swap:none",
        "hx-status:5xx": "swap:none",
    }
    return form(
        cls="composer",
        attrs={"method": "post", "action": action, "id": identified, **driving},
        children=[
            above,
            # Shown by `display` rather than by the opacity htmx's own indicator rules toggle, so
            # it holds no row while nothing is in flight; see `.sending` in the stylesheet.
            span(
                cls=("sending", "htmx-indicator"),
                attrs={"id": SENDING_ID, "role": "status"},
                children="sending\N{HORIZONTAL ELLIPSIS}",
            ),
            # What the box will do with what is in it, said in words and only while that is not what
            # the box normally does. One per answer, drawn by the stylesheet off the form's own
            # `data-leading`, so the words live here and the script sets one attribute; a `title`
            # would not do, because a mode nobody can see is the whole failure these exist to
            # prevent.
            #
            # A row of the composer's own column rather than an item beside something, because this
            # is a whole sentence: sharing a row would squeeze it to half the width on every window
            # to make room for something that is usually not there.
            *(
                p(
                    cls="leading",
                    attrs={"data-leader": answer.leader, "role": "status"},
                    children=f"{answer.saying}. Escape to go back to a message.",
                )
                for answer in answers
            ),
            # One card: the text, and under it a row of what to do with it. The edge is the card's
            # rather than the textarea's, so the row reads as the box's own tools and not as buttons
            # that fell off the end of it, on a phone and a wide window alike; see `.composer__box`
            # in the stylesheet.
            div(
                cls="composer__box",
                children=[
                    # `rows` is the floor only where `field-sizing` is not supported: the box sizes
                    # itself from what is typed, and a browser that can do that ignores `rows`
                    # entirely. See the growth rule in `mainplate.css`.
                    # Deliberately no `autofocus`: whether the box takes the cursor on arrival is a
                    # touch-screen question the server cannot answer, so the script gives it focus on a
                    # pointer and leaves a phone alone. The fork page keeps the attribute, since there
                    # somebody is already there to edit.
                    textarea(
                        attrs={
                            "id": MESSAGE_ID,
                            "name": "prompt",
                            "rows": 3,
                            "required": True,
                            "disabled": refusing,
                            "placeholder": "Say something",
                            "aria-label": "Message",
                        }
                    ),
                    # A label and not a div, so the empty part of the row focuses the box: a label
                    # hands a click to the control it names unless the click landed on something
                    # interactive inside it, which is exactly the split wanted, and it is the
                    # browser's own rule rather than a listener. The `aria-label` above outranks a
                    # label's text in naming the box, so Send does not become the box's name.
                    label(
                        cls="composer__tools",
                        attrs={"for": MESSAGE_ID},
                        children=[sending_control(refusing, answers)],
                    ),
                ],
            ),
        ],
    )


def runs_in(showing: Conversation) -> str | None:
    """
    Where a command typed into this session's box would run, as a person reads it, or nothing at all.

    Nothing where the session cannot run one, which is `runnable`'s question and not this function's:
    a session with files in a console built without `Commands` has somewhere and no way, and the
    offer goes with the way. The repository is named where a forge still reaches it and the recorded
    id where none does, which is what `Conversation.repository` already holds.
    """
    if not showing.runnable:
        return None
    if showing.repository is None:
        return "this session's checkout"
    return where_it_works(showing.repository, showing.chosen.branch if showing.chosen is not None else None)
