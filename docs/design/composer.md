# The composer

Everything the box at the bottom of a conversation can do. The composer grows more than one way to
act on what is in it, and **those ways are not variants of one thing**. They are told apart by what
each one writes, which is also the order of how much they can break:

- **The shelf** writes nothing recorded at all. It is unsent text.
- **A disposition** decides which session's inbox the message goes into, and which of the two kinds
  of message it is. Nothing new is written that was not already written by `say`, `send` or `fork`.
- **A steer** is not a disposition and not a thing anybody asks for: it is what happens to an
  ordinary message that a pass finds while it is working. Nothing about the write differs.
- **A command** is the one that is not a message at all. It goes in the same queue, is read out of
  it by nobody, runs a process outside the sandbox everything else here runs behind, and is never
  told to a model.

Sorting them this way is what keeps the cheap ones cheap. Three of the four need no new mechanism.

## The disposition

**One field on the composer's form, not one button per endpoint**, because every disposition takes
the same input and differs only in where it goes. Parsed at the boundary into an enum, the way
`posted_workspace` turns one posted value into the two it records.

- `here` is `Service.send`, and it is **the one nobody decides**: it delivers a `Steer`, which is a
  message the pass may fold into the turn it is working on and will otherwise open the next turn
  with. Neither the page nor the server can settle that, and neither tries. A page is rendered from
  a checkpoint that has moved by the time somebody has typed a paragraph into it, so a `Steer`
  button beside `Send` asked a reader to choose between two moments against a state that no longer
  held; and so did the server, when it read the record and chose between two calls, because a turn
  can end between the read and the write. The pass is the only party reading at the moment the
  answer is true, so the answer is its.
- `next` is `Service.say`, which delivers a `Prompt`: a message a draining pass stops at rather than
  folds in. It is kept as an explicit answer because wanting to be taken up *after* the reply that
  is coming is an intent no record carries and so nothing can decide it for you. It is offered only
  while something is being answered, since otherwise it is what Send already does.
- `forget` is `Service.say` with the boundary set, so it shares an arm with `next` the way `fork`
  shares one with `aside`. It is the only answer that changes what the *model* is handed rather than
  where the message goes, and being a `Prompt` is what makes it possible: a boundary between turns
  is the only place one can be, so it must never be folded into a turn already running. See
  [Forget](#forget).
- `handoff` is the bundled handoff plugin's own leader, and it is **the one answer whose box may be
  empty**. What it does
  with the text is point the handoff at something rather than send it anywhere, and the ordinary
  handoff has nothing typed into it, so the button carries `formnovalidate` and the boundary allows
  an empty message for this disposition alone. It shares the family `forget` is in, both ending a
  stretch of context where they stand, and differs in who writes what the next one opens on. See
  [Handoff](#handoff).
- `fork` is `Service.fork(at=turns, said=...)`, which is pi's `/clone` and needed a control rather
  than a mechanism: the fork route already accepts `at == said.turns`, so forking the end has always
  been reachable by URL and offered by nothing. It is called `fork` and not `branch` because it is
  the same call the rule above every turn makes, with a different `at`; see [the
  words](../philosophy.md#the-words).
- `aside` is the same call with `Origin.aside` set. **Nothing mechanical differs**, since the copy,
  the worktree and the choice are identical, so what it records is what somebody *meant*, which
  nothing else could recover and which the sidebar cannot draw otherwise. Saying that plainly is
  better than inventing a difference to justify the flag.
- `parent` sends into the session this one was forked from, which is how an aside comes back. It is
  offered from **any** fork rather than only an aside, because what it needs is `Origin.session` and
  every fork has one; gating it on the flag would be a restriction invented to make the flag look
  load-bearing. The destination is read off the row and never posted, so a form cannot put a message
  in a conversation nobody was looking at. It is drawn once the branch is past [its own settings
  step](plugins.md#setup), since this control is in the composer and a settling page has none: an
  aside is a fork, so the round trip is step aside, confirm, read, send back. That falls out of how
  forks work rather than being a rule about asides, and it is left that way rather than given a
  second page shape to keep working.
- `run` is `Service.run`, and it is the one answer here that is not a message going somewhere. It is
  in the same field all the same, because the question the menu asks is what happens to what you
  typed; a control of its own would spend a slot in the row above the box. It is offered only where
  the session has a worktree to run a command in, and posting it to one that has none is a `422`
  rather than a silence, since a command that vanished is indistinguishable from one that did
  nothing. See [Run](#run).
- A **steer** is [below](#steer). It is not one of these and never was a choice a form makes: it is
  what becomes of a `here` message that a pass finds while it is working.

**`Origin.aside` is the one column added for presentation**, and it earns that only because the
sidebar draws the two marks differently: a fork gets `→2` in the mark ink and an aside gets `↩2` in
the faint one, because what a reader scanning a tree wants to pick out is where the conversation
actually went. It arrives through `ADDED` like the two columns before it, and `parse_origin`
*defaults* it where the pair beside it is demanded: every fork written before asides existed has
`NULL` there and was a plain fork, so reading it as one is ordinary parsing of an optional rather
than a guess.

**Send and everywhere else are one split control**, because a destination per button spends a slot
in the row above the message box, which is the row a phone has least of. `sending_control` is Send
plus a caret opening a `<details>` whose items are submit buttons, so the whole thing needs no
script: the fold is how everything else here folds, and a named button has always posted its own
pair.

**Every answer is one `Answer` value, rendered three times**: as a row in that menu, as the button
the box shows once a leader has put it in that answer's mode, and as the sentence above the box
saying what will happen. `sending_answers` is the list and the three renderings are functions of it,
so what is on offer, what it is called and what it posts cannot come apart between them. That is the
same bargain the branch field takes in rendering one `branches` argument as a `<datalist>` and as
the list the script narrows.

**One word per answer, and `Answer.named` is `leader.capitalize()` rather than a second field.** The
word is the menu row's name, the leader typed after `/`, and the value in `data-leading`; where it
names a disposition it *is* `Disposition.value`, so the word on the page, the word on the keyboard
and the word in the store are one string. That is what took `Wait for the next turn` back to `Next`
and `Back to where this came from` back to `Parent`: a sentence cannot be typed, so a leader would
have needed a second name, and a second name is a synonym to keep in step for ever. What each one
*does* is the `saying` under it, which is where an explanation belongs anyway.

It deliberately does **not** switch what the primary button does *by remembering*, which is where
GitHub's version of this control goes further, and is [the failure a control like this can
have](../philosophy.md#controls). A mode is different because it is only ever entered by asking for
it by name, and the button then says `Fork` rather than `Send`. What closing the menu on an outside
click and on Escape adds is an enhancement over a control that already opens, chooses and submits
with the file absent.

**Send not saying whether it steers is not an exception to that.** What a button says is still what
it does: `Send` means "into this conversation, now", and steering is *how* that is carried out when
a turn is running rather than a second thing the button might secretly be. The failure the rule
guards against is a control whose meaning depends on something the reader cannot see; here the
reader could not see it either way, which is the whole reason the decision moved off the page.

**`Keep` is an answer in that menu and not a button beside it**, because "put this on the shelf" is
one more answer to what happens to what you typed, and answering one question in two places is the
thing this console removes wherever it finds it. It sits under a rule in the menu, since it is the
only answer that sends the text nowhere. The shelf's *list* stays in the rail, where nothing
rebuilds it mid-turn.

That row is the one thing in the menu that needs the script, because the shelf is `localStorage`;
everything that *sends* works without it. That is the shelf's standing bargain rather than a new
exception, since its card in the rail shows nothing without the script either.

**It is posted as the submit button's own `name`/`value`**, which is the browser's mechanism rather
than anything scripted, so it works with `mainplate.js` absent and htmx appends the submitter's pair
like any other field. Shift-Enter reaches whichever button the *mode* leaves standing and no other:
`requestSubmit()` with no submitter posts no disposition at all, which parses as `HERE`, so the
keyboard shortcut means the one thing the button beside the box says rather than whichever row was
pressed last.

An **absent** field is `HERE` and an unrecognised one is a **refusal**, which is the one place a
default would be wrong: guessing puts a message in a conversation nobody addressed it to, and it is
sent by the time anybody could notice.

**A branch answers `HX-Redirect`, never a `303`.** htmx follows a redirect itself and swaps what
comes back into the target, so a `303` would put the branch's transcript inside the parent's page
and leave the address bar naming the parent. `elsewhere` in `console.py` is that, and
`TestWhereTheComposerSendsTo` pins it in a real Chromium, because both halves, that htmx sends the
submitter's value and that it navigates on this header, are htmx's behaviour rather than ours and
look identical in markup either way.

## Leaders

**`/fork ` in an empty box is a shortcut to a row of that menu, never a second way of saying it.**
Typed, it puts the composer into that answer's mode: the button beside the box says `Fork`, a
sentence above it says what will happen, and what is then written and sent goes there. `! ` is the
same thing for `/run`, which earns a key of its own by being the mode reached oftenest.

**The space is what commits it, and that is what makes a leader something a reader *finishes*.**
Until it is pressed the word is ordinary text sitting in the box with the menu open beside it, so
`!` is a character, `/fo` is two, and nothing has happened on a keystroke somebody was in the middle
of. It commits only where the box names an answer *in full*, because a prefix is somebody still
typing and taking the row the keyboard happens to be on would put the box in a mode they were
spelling their way towards; unnamed, the space types itself, which breaks the pattern and puts the
menu away. That is also what lets `!` be a leader rather than a key that does something: it is `run`
written in one character, so it narrows, commits, and is undone by a backspace exactly as `/run` is.

Six things there are decided rather than incidental:

- **It is entered in the page, visibly, and never parsed off the message.** If the server stripped a
  leading `/fork` out of what was posted, a paragraph that legitimately opens with one would
  silently be a fork, and it would have happened by the time anybody noticed. So the leader is
  consumed by the script, the mode is drawn, and both buttons are rendered by the server with their
  own labels and their own posted values: the script toggles one attribute on the form and hands
  `requestSubmit` whichever button that leaves standing, so it holds no label, no field name and no
  disposition. A button whose text and `name` the script rewrote would be the failure this shape
  exists to avoid.
- **Only at the start of an empty box, and only in the default mode.** Mid-message a `/` is an
  ordinary character; in a command box it is the front of half the paths anybody types, so a palette
  opening over one would be in the way of every command. A word no answer answers to is ordinary
  text too, so `/etc/hosts is where it lives` is a message.
- **The sending menu *is* the palette**, which is what keeps one list: the rows already say what
  each answer does and already carry its word, so a second list beside them would be a copy to keep
  in step. It opens narrowed to what still fits, by prefix rather than anywhere in the word, the
  opposite of the branch field, because a leader is a short word typed from the front. Enter takes
  the row the keyboard is on, which is how a word nobody finished typing is finished; plain Enter is
  safe to swallow there where it is nowhere else in this box, because what it would otherwise do is
  break a line in the middle of `/fo`. Two keys and not two mechanisms: the space says the word is
  done and Enter says the *row* is, which are different things to have decided.
- **A row pressed while a leader is being typed chooses the mode rather than sending.** The rows are
  submit buttons, so without the capture-phase intercept a press with `/fo` in the box would post
  `/fo` as the message: both a message nobody wrote and a session nobody asked for.
- **`Keep` is a mode like the rest and is the one that cannot be a submitter.** It posts nothing at
  all, so it is a `type=button` the shelf listens for, and the keyboard reaches it by pressing it
  rather than through `requestSubmit`. It is also the one mode nothing dispatches a `submit` from,
  so leaving it is said outright in the shelf's own listener rather than reached through the path
  every other answer takes; what decides is still the button's own `data-staying`.
- **Whether a mode outlives what was sent from it is the answer's own decision**, carried on the
  button the server drew for it as `data-staying` and read there rather than kept in a list in the
  script. `Run` stays, because a command is rarely the only one; everything else comes back to
  `Send`, because it is a thing somebody meant once, and a `Fork` or an `Aside` has navigated away
  by then anyway. The script leaves the mode a turn of the event loop after the `submit`, because
  what leaving it does is hide the very button the send is attributed to.

**Which modes exist is read off the buttons the server drew**, not kept in a list in the script. A
session with no files is offered no `Run`, so there is no `/run` and no `!`, and the two cannot
drift because there is only the one thing that decides it. What CSS lists by name is which
`data-leading` shows which button and sentence, the same bargain the card kinds take, and it is the
one place the answers *can* drift: CSS cannot ask whether a descendant's attribute matches an
ancestor's, so an answer added without a line there enters a mode that hides `Send` and reveals
nothing, leaving a composer with no primary button and no sentence.
`TestNamingAModeFromTheKeyboard` asks it of every button the server drew rather than of a chosen
one, which is what turns that into a failure rather than a mode nobody can use.

**A mode is left by Escape, and by a send where the answer is not one that stays.** What makes
staying safe is what makes the mode safe at all: the button says `Run`, not `Send`.

**The sentence saying what the mode does sits *above* the box.** The composer is the bottom of the
page, so a row appearing anywhere in its column pushes everything above that row upward: under the
box it moved the box itself out from under the cursor at the moment somebody entered the mode, where
above it what grows is the composer's top edge. `TestNamingAModeFromTheKeyboard` measures the box
across the press, because both layouts are correct markup and each screenshot is right on its own.

## The shelf

Named slots of unsent text, **per session, and copied when the session forks**. That copy does not
offend the rule against a second copy of what changes, [for the reason a fork does
not](../philosophy.md#a-fork-copies-what-was-said-and-the-turns-it-copies-are-settled).

Accumulative rather than save-and-replace, and the case that decides it is a review: reading a diff
and building one comment up across several turns is the shape this is for, where a single
overwriting draft would only ever hold the last thing typed.

**It cannot live in the checkpoint while it is editable, and that is structural rather than a
preference.** `without-durability-sqlite` writes steps with `ON CONFLICT (workflow, step) DO UPDATE
SET value = workflow_checkpoint.value`, which is a no-op update: a key keeps the value it was first
given, so a draft saved twice would keep its first text for ever. And `Service.token` counts rows,
so even a rewrite that did land would not move the change token and no other tab would learn of it.

So it starts in `localStorage`, keyed by session id exactly as the muted kinds are, and the fork
copy is the script's rather than the server's. `document` puts `data-forked-from` on the body for
exactly that: the server has never seen a draft so it cannot carry one the way `Service.fork`
carries a turn, but it can say which conversation this one came from and let the page holding both
stores do the rest.

**An empty shelf and a missing one are different, and the inheritance turns on it.** The adoption
runs only where `held(SHELF) === null`, because a reader who cleared theirs has a stored `[]` and
must not be handed the parent's back on every load with no way to refuse it. `test_browser.py` pins
that, and it is the one rule here a plausible simplification silently breaks.

**Keeping clears the box and taking appends to it**, never the other way round. Clearing is what
makes it "keep that" rather than "copy that", since the reason to shelve a paragraph is almost
always to write a different one next; appending is what can never lose something already typed, and
what assembles several kept notes into one message, which is the case an aside's merge and a diff
review are both instances of.

Moving it onto the session is the goal and not a regret, because a draft that does not reach another
browser is a draft you have to be at one machine to finish. **What that move costs, decided before
it is made rather than during it**: a table beside `sessions` rather than a checkpoint key, since
the checkpoint cannot hold a mutable value; a second change signal, since `token` counts checkpoint
rows and a drafts table is not one; and a story for two tabs editing one slot, which is a genuine
conflict where everything else here is append-only and therefore has none.

## Forget

**The one answer that changes what the *model* is handed rather than where the message goes.** It
records `forget` on the turn's own opening record, and `reached` starts the history there instead of
at turn 0.

**Nothing is deleted and nothing is hidden**, which is the whole reason the word is `forget` and not
`clear`. Every turn above the boundary still renders, still counts toward what the session cost, and
still comes across on a fork; the checkpoint is still the conversation. What starts again is only
the message history, which is the split `command` already makes between being *in* the checkpoint
and being *in* what a model is told, applied to turns rather than to one kind of record. A control
saying `clear` beside a transcript that keeps all of it would be describing something this does not
do.

**The rule says `context cleared`, and the object is what keeps that from being the `clear` the
control is refused.** A bare `clear` names nothing, so beside a transcript that keeps every word it
reads as a claim about the transcript; naming the *context* says the one thing that was cleared and
leaves the phrase true. That is [the plain technical word](../philosophy.md#the-words) at the one
place a reader meets this mechanism, and it is two words rather than a sentence because a rule now
carries six figures beside it: what a reader needs there is the noun and the verb, and the fork link
under the same finger already says what to do about it.

**It sits in the middle of the rule, between two of the gaps that hold the line apart.** A rule has
the turn's own controls at one end and its figures at the other, and a boundary belongs to neither:
drawn against the left group it read as one more fact about the turn rather than as the thing the
rule is saying. On a phone the three parts stack instead, each on its own row, the same two gaps
given a whole line's basis so that a flex item takes a line on its own, which is one mechanism at
both widths rather than a wrapper element that exists for one of them.

**It rides on the message rather than in a record beside it**, and that is what makes the boundary
impossible to get wrong rather than a saving. Two entries need an order, and a turn can open between
them: a marker delivered *after* the message can be missed by a pass that has already taken it, and
a resumed pass reading it would then build a shorter history and pair it with an answer the first
pass gave to a different question, which is exactly what `heard:{i}` exists to stop a steer doing.
One record is one append, so a message whose history policy has not landed cannot exist. It also
settles two smaller things for free: there is no dangling marker at the end of a session, and no
live pass that can miss one, since the message a pass just took carries the answer.

**A boolean and not the turn the history starts at**, which would be a number recoverable from where
the entry sits and able to disagree with it.

**Never a steer, and always with a message.** A boundary between turns is the only place one can go,
so it is delivered as a `Prompt` rather than a `Steer`: a draining pass stops at one of those, which
is the whole of what "never folded into the turn already running" means. That is why it shares an
arm with `NEXT` the way `FORK` shares one with `ASIDE`. It carries a message because there is no
reason to forget without going on to say something, and because a marker with no turn under it would
be a rule with nothing below it.

**`reached` asks about the turn it is about to return, not only the ones behind it.** The walk is
conditioned on a turn having *answered*, so a forget on the turn about to run is never reached by
it. Missed, the first pass answers that turn on the whole conversation and the pass that resumes it
answers on nothing. That is the one place this can be quietly half-implemented, and
`test_conversation.py` pins both routes to the same answer.

**Continuing the conversation a forget closed is `fork` at that turn**, and the affordance already
exists: the fork link lives on the turn rule, which is exactly where the boundary lands. `before`
copies the turns *below* the branch point and the marker lives on the turn that opens, so the branch
carries the whole backlog and no boundary, with nothing taught about the field. Forking *after* one
inherits it in the prefix and starts its history in the same place. A control of its own would be a
second name for one call, so what the rule carries instead is a `title` saying what forking there
means.

**The rule is the one this transcript draws that describes what is *above* it**, which is what lets
the phrase be short: every other rule looks forward at the request or the turn it opens. It is drawn
in a stronger ink rather than a hue of its own, because the palette runs on one axis and a boundary
belongs to neither side of it, and the phrase takes the full ink and the bold face where everything
else on a rule is faint: set in the same weight as a tree hash it reads as chrome to skip. The
panels above are left exactly as they were, because what changed is who was told, not what is worth
reading, and fading them would say the second thing while colliding with `muted`, which is the
reader's own decision and already drawn that way.

**The rule wraps and the controls on it do not.** It is the only rule carrying a phrase as well as
its figures, so it is the only one that wraps unprompted, and what goes onto the second line is the
tail of the figures rather than the `#N` and the fork link somebody is about to press. That is [a
control that toggles may not move](../philosophy.md#controls), one rule along.

**The dock gains a leftmost column**, widest-first the way the picker is ordered: it steps the
points where the model's history starts again. It is drawn in every session and steps nothing in
most of them, which is right rather than a gap, since the rail lives outside the region that swaps
and a column that appeared with the first forget would not appear until a reload. Its stops are
found in the live transcript the way every other column's are, by the `data-stop` a rule declares
about itself, so one recorded mid-session is reachable at once; its upper terminus is the top of the
transcript, which is what "before any forget" means.

## Handoff

**A forget whose message the session wrote itself**, and the one answer in the menu whose box may be
empty. It is [a bundled plugin](../plugins/handoff.md), so what a handoff is, when one fires on its
own, and what its card in the rail holds are that page's; what is the composer's is that `/handoff`
is a leader like any other, and that its row is the one carrying `formnovalidate`.

**The box may be empty for this answer and no other.** The box is `required`, which is right for a
message and would refuse the ordinary handoff, so the button says it does not need the form's
required fields and the boundary allows an empty message for this disposition alone. That is the
browser's own mechanism rather than the script toggling an attribute under a reader, which is the
same reason every mode's button is drawn by the server.

`Answer.demands` is where an answer says so, and it is read by *both* renderings: a menu row is a
submit button exactly as a mode's own button is, so an exception on one of them would be a control
that refuses from the menu and works from the keyboard. `TestWhereTheComposerSendsTo` drives it in a
real Chromium, because a form refused before any request leaves and a control that silently does
nothing look identical in the markup.

It sits beside `Forget` in the menu because they are the same family: both end a stretch of context
where they stand, and what separates them is who writes what the next one opens on.

**A note a plugin delivers is delivered and not appended, and that costs a pass boundary.** An entry
appended mid-pass is invisible to the pass that appended it, since `receive` reads the snapshot
loaded at the top, which is what makes a drain replayable, and an append queues nothing. A note
written that way leaves the session `Blocked` on a message already sitting in its own inbox with
nothing that will ever wake it. `app.delivering` therefore takes the whole `Durable` rather than the
checkpointer a pass holds.

## Steer

What becomes of an ordinary message that a pass finds while it is working. Nobody asks for one by
name, nothing is written that a message sent to an idle session does not write, and the only thing
that differs is where the entry lands.

**Two keys, and the difference between them is transport and delivery.** The message itself is an
inbox entry, appended from *outside* the pass, because the worker may be in another process and the
store is the only channel between them. `turn:{n}:heard:{i}` is a recorded step saying how far down
that queue the turn had read when it made request `i`, which is what says where a steer went.

**A message cannot be lost at the end of a turn, and nothing has to be claimed for that to hold.**
There is no slot: a message nobody took is still in the queue, and whichever turn opens next opens
on it. That deleted a whole mechanism: a marker the pass wrote into the next steer slot as it
stopped listening, a compare-and-set the store settled between the pass and whoever was typing, and
a `Service.steer` that could answer `None` and make its caller decide again. The window it existed
to close does not exist in a queue.

**Whether a message may be folded in at all is the record's**, not the moment's. `records.Steer` is
one a running turn may take and `records.Prompt` is one it must not, which is how `next` and
`forget` say what they mean; a pass draining its queue stops at the first prompt. See [the key
scheme](checkpoints.md#the-key-scheme).

**It is appended to `request_context.messages` in `before_model_request`, and emphatically not
`ctx.enqueue`, which was tried and delivered every steer one round trip late.** Pydantic AI's own
drain capability is ordered `outermost`, so it empties the queue in *its* `before_model_request`
before this one runs: a message enqueued there misses the request it was read for and lands in the
next one. That cost a round trip nobody asked for, drew the steer's panel below the answer it was
meant to shape, and made `heard:{i}` a claim about a request that never heard it.

Appending is sound for the two reasons the enqueue was reached for. `_agent_graph` builds the
request context with `messages=ctx.state.message_history[:]`, a *copy*, and what
`before_model_request` returns is adopted wholesale (`ctx.state.message_history[:] = messages`), so
the steer lands in `turn:{n}:messages` and the transcript draws it with nothing else taught about
it. And a *new* message is added rather than an existing one mutated, which is the thing the docs
actually forbid. Pydantic AI merges consecutive trailing requests for the wire with the tool parts
first, so a steer travelling beside a batch of results arrives after them in one request and is
recorded as its own message. It is emphatically not `CheckpointedModel.request` either: anything
added at the model reaches that one request and never the recorded history.

**What it reads is the pass's own snapshot rather than the store**, which is `Run.pending`'s own
shape: entries are ordinary records, so they are already in the snapshot the pass loaded on its way
in. A message delivered while the pass was setting up waits for the next one, which is a round trip
that has already been sent either way. That is the coupling [the
allowance](durability.md#what-one-pass-does) costs and a reason to leave it at one, since a pass
making several requests off one snapshot makes a message wait behind as many as it has left.

**It is also what deleted the second drain.** There used to be an `after_node_run` that read again
where a run would otherwise have ended, so that a message arriving during the last response could
redirect the run into one more request rather than reaching nobody. Nothing can arrive *during* a
pass: the drain before the first request already sees everything this pass ever will. What used to
cost the ending turn a round trip nobody asked for now opens the turn after it, which is both
simpler and one fewer request.

**The step is what makes the drain replayable, because the queue keeps filling between passes.** A
resumed pass reading live would ask a question the first pass never asked, and `turn:{n}:model:{i}`
is the *answer* to a question, so a replay that asked a different one would be pairing an answer
with a prompt nobody gave. The record is a **cursor**, which is why nothing is carried on the scope
any more: where the record was a list of texts and the next request needed a count of them, it is
now a place in a queue that the next drain simply reads. Reading the inbox is the capability
reaching into conversation state, so it arrives **injected** as `Draining`, symmetric with `Pricer`
and for the same cycle.

`Steering` is its own block type and `steer` its own `Kind`, because `panelled` reads a panel's kind
off its blocks: a steer arriving as `Prose` would be drawn as the model answering itself. It takes
the person's hue, since the axis is who produced the text.

**`heard:{i}` has a reader, and that is what stops a steered message vanishing.** A steer reaches
`turn:{n}:messages` only when the turn *ends*, so a running turn read from its model steps alone
would take somebody's message and show nothing at all until the reply finished, which is fine while
steering is a button somebody presses deliberately and not fine at all when every `Send` may become
one. So `blocks_from` walks the entries too: `told_in` turns the cursors into the steers each
request carried, and one is drawn above the response it shaped, exactly where the settled reading
will put it. What no cursor accounts for goes at the end, which is where a message nobody has read
belongs since nothing has been said since it.

**A prompt is where that stops, and `unread_in` is the rule.** A steer past the last cursor is drawn
as one the running turn may still take; a prompt cannot be folded in by anybody, so it and
everything behind it are drawn as messages waiting for turns of their own. With nothing being
answered every unread message is one of those, which is what keeps a message arriving just after a
turn ended from being drawn as a steer of a turn whose settled reading does not draw it at all.

## Run

The one answer in the menu that is not a message, and the only thing this console does that runs
outside the sandbox everything else runs behind. `! ` typed into an empty box is the shortcut to it.

**As the person and not as the agent, and that is the whole point rather than a gap.** A session's
`isolation` bounds what a *model* asked for, and [`sandbox.py`](sandbox.md) binds the clone
read-only precisely so no tool can write a history no panel shows and no fork inherits. `git commit`
and `git push` are the person's to run, and confining them is what would make this pointless. What
it adds to the blast radius is nothing new: a session on `Filesystem.EVERYTHING` already hands a
model the store, every other conversation, and `config.yaml` with the credentials in it. What it
does mean is that who can reach this console is the whole of what guards it, which was already true
and is now worth saying.

**Recorded and not told**, which is the split [the key scheme](checkpoints.md#the-key-scheme) rests
on: what a command exited with is settled the moment it exits, and nothing will ever rewrite it.

**Two keys and a background task, because a `pytest` is minutes and somebody is waiting on the
POST.** `Service.run` appends the command to the inbox and returns; `Commands` runs the thing and
writes `result:{entry}` when it is over. The panel is drawn from the entry the instant it lands and
`Command.result is None` is the whole of "still running", exactly as `ToolUse.returned is None` is
the whole of "still out". That is the control-plane argument the worker already answers for cloning,
one step along. The cost, stated: **no live output.** The panel says running and then shows the
whole result, which is right for `git commit` and irritating for a watch; live output needs a
channel outside the checkpoint, which is a different feature.

**It goes where it was run, and that is read rather than recorded.** The store files an entry in the
order it arrived, so counting a turn's model records ahead of the command's entry says how far the
reply had got when somebody typed it, and `alongside` puts the panel back between those two answers.
Nothing had to be written at the time and nothing raced the pass for a position in its sequence,
which is the objection that used to send commands to the end of the turn: collected there, a panel
sank down the page as each later answer landed above it, under a reader who had just run it. Both
readings of a turn come through the same merge with the same positions, so nothing moves when
`turn:{n}:messages` lands.

Which turn a command is in is decided the same way, by where its entry sits between two turns'
opening ones, so nothing has to compute a turn number at the moment it is posted. `Service.run` used
to answer `turns - 1` off a page that could already have moved.

**`Commands` is the one place this console holds work in flight**, which the note at the top of
`service.py` says it does not. Stated rather than quietly excepted: a running command belongs to one
process and does not survive a restart. What keeps it from spreading is that the place holds no
answers, since the command and its result are both in the checkpoint, so a page renders the same
thing whichever process is asked, and the task set exists only so a shutdown can reap what it
started.

**A shutdown writes the record from `aclose`, not from the task**, and that is not belt and braces:
a task cancelled before it has had a turn on the loop never enters its body at all, so its own
`except` cannot run and nothing would say what became of it. `supply` keeping the first value is
what lets the run that *did* get to say something for itself, with the partial output it managed,
keep its answer. `UNFINISHED` is outside both the range a process can exit with and the negatives a
signal produces, so "the console never learned" is not mistakable for either.

**A status is drawn as the number, never as "failed".** `git diff --quiet` exits 1 to mean there
*are* changes and `grep` exits 1 to mean no match, so flattening it would have this console report a
command doing its job as one that broke.

**The output is drawn open where a tool call's is folded, and the axis is who asked.** A call is the
model reaching for context, so what it returned is something a reader opens to check the work; a
command is a line the person typed, and what it said is the whole of why they typed it. It is still
a `<details>`, so it folds, the dock's fold controls reach it, and a reader who has read one can put
it away; it simply does not have to be opened to be read.

**A fold's frame shuts it, and not only its summary.** A summary is one row at the top of a box that
may be several screens of output, so putting a long one away meant scrolling back up to the single
place that would do it; the room around the output is at the *bottom* as well, which is where a
reader who has just read to the end already is. It is one complaint about three boxes: a command and
a document are drawn open so shutting is the press made oftenest there, and a call the reader opened
to check the work is the one whose return runs to hundreds of lines. Two boxes of the same shape
answering the same press differently would be the thing to explain.

**`FRAMES` names the bodies rather than the folds around them**, and that is what let one rule
survive the fold moving up to the panel: each frame shuts whichever `<details>` it is a body of, so
a document's shuts the panel it sits in and a call's shuts the call. Stated that way it also says
the thing a list of folds could not: a panel's own room is the whitespace between the blocks of a
conversation, which is in no frame here, so a press that missed a paragraph cannot fold the reply it
missed. That is the one place this rule must not reach, and it is why the panel joining `FOLDS` did
not put it in `FRAMES`.

What is *in* the box is exempt, and that exemption is the whole of what makes this safe: a press in
there is usually the start of lifting a line out, and a panel that folded under somebody selecting
from it would cost more than the scroll it saves. Two selectors, because the content takes two
shapes, a `pre` for a command's output and a tool's return against rendered prose for a document,
and the exemption is about the content rather than about either shape. A press that ended a drag is
out for the same reason, since a browser reports one as a click on wherever the pointer came to
rest. It shuts and never opens, since a shut panel is a summary and little else, so this is the way
out of a tall box rather than the toggle in a second place, and setting `open` dispatches `toggle`,
so the decision is recorded exactly as a press on the summary is.
`TestShuttingAFoldFromItsFrame` pins both kinds and both halves in a real Chromium, because where
the frame stops and the output starts is a fact about the rendered layout that no markup assertion
can see.

**And a command that said nothing says so**, rather than drawing the empty pane that being open
exposed. Plenty of them do, `git diff --quiet` being the gallery's own example, as is every command
whose whole answer is its exit status, and a blank rectangle under one reads as output that failed
to arrive. It is a stated absence for the same reason `no reference record` is. A command still
*running* gets no body at all, since "said nothing" is a claim about a finished one.

**That is what makes the fold a decision in two directions, and the script keeps both.** A call the
server renders shut can be opened and a command it renders open can be shut, so `mainplate.js` holds
what the reader decided about each fold rather than a set of the ones they unfolded, and a fold
nobody has touched is left to the server. And the id it keeps that under has to be one that does not
move: a command's is its own inbox entry, deliberately not the panel anchor a call's is built on,
because a panel's position moves as a turn is answered and a fold identified by it is a decision the
script loses on the next response. `test_browser.py` pins the two directions beside each other.

A panel's own fold is kept under its anchor, which has that same weakness and takes it knowingly: a
command landing mid-turn shifts every panel after it, so a decision about a panel can be applied to
its neighbour for as long as the turn runs. It is the exposure every permalink and `data-landed` on
this page already has, and the cost of getting it wrong is a fold in the wrong state rather than a
record in the wrong place.

**`! ` is `/run`'s own key and never a parse of the message**, which is [the leader
rule](#leaders) applied to the mode reached oftenest; see there for why a leader is entered in the
page rather than stripped off what was posted, and why the space is what commits it. A command box
is also where "only in the default mode" earns its keep, since `/` is the front of half the paths
anybody types.

**And it is the one mode that stays once a command has gone**, which is what `data-staying` is for:
a session that reaches for `Run` reaches for it again a line later, where every other answer in that
menu is a thing somebody meant once.

`requestSubmit(submitter)` and not `requestSubmit()` is load-bearing here and nowhere else:
unattributed it posts no button's pair at all, so a command typed into a command box would arrive as
an ordinary message and be said to the model. `TestTurningTheBoxIntoACommandBox` pins it in a real
Chromium, because that is htmx's and the browser's behaviour rather than ours and looks identical in
the markup either way.

`Run` is the one mode that changes what you are *writing* rather than only where it goes, so the box
takes the terminal's monospace and a heavier edge on top of the button and the sentence every mode
gets.

The mode is entered from the box and left from the box, both by a key pressed while it has the
focus, and it is deliberately *not* stored: it is a mode within a visit, like following the end,
rather than a decision about a conversation.
