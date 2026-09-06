# The console

The page: how it is rendered, how a second render reaches a browser nobody reloaded, and the
controls around the conversation. What *draws* it, the three shapes, the one value that scales
everything, and the monospace grid, is [the stylesheet and the grid](assets.md).

## htmx 4

Vendored at `assets/htmax.min.js`, and it reads very differently from htmx 2: explicit `:inherited`,
lowercase colon-separated event names (`hx-on:htmx:after:swap`), `hx-status:` in place of
`responseHandling`, and every status swapping except `204` and `304`.

**`htmax` and not `htmx`, and the extensions are gated by a meta tag.** `htmax` is core plus every
bundled extension in one file, taken because one file cannot drift from itself: core and an
extension vendored separately are two files that have to be kept on one version, and the failure
when they are not is a swap that silently misbehaves rather than an error anybody sees. The price is
ten extensions this console does not want, several of which would change how a page behaves just by
being included: `history-cache` puts back the history store htmx 4 deliberately removed, and
`hx-live` and `alpine-compat` are reactive scripting. `<meta name="htmx-config" content="extensions:
...">` is the allowlist, read before any of them register, so a name absent from `EXTENSIONS` in
`pages.py` is never installed rather than installed and unused.

## Pages, and the one connection

Pages are `without-html` node trees, [pure functions of already-answered
questions](../philosophy.md#a-page-is-a-pure-function-of-already-answered-questions). A page and the
fragment inside it are the same function called at two depths, which is what stops the two
renderings from disagreeing.

**A page holds one connection and the transcript carries no `hx-` attribute of its own.** The region
neither asks for itself nor decides when to: `streaming.py` sends it down the page's event stream
whenever the session records anything. That deletes a whole class of bug rather than moving it: a
trigger on a region that is itself replaced had to be `every` and never `load`, because morphing
keeps the element and a `load` poll fires exactly once and then waits forever on an answer that
already arrived, invisibly to any markup assertion. A region with no trigger has nothing to get
wrong.

**`transcript_region` takes a whole `Conversation` rather than the things drawn out of one**,
because every caller had one in hand and was taking it apart the same way. What the region needs is
the session, what was said, whether it is stalled, the model's window and where its reserve falls,
and five arguments derived from one value are five chances for a caller to pair a transcript with
another session's window.

**One connection drives two regions**, which is what `partial` was always for: the transcript, and
[the cache note](cost.md#whether-the-cache-is-still-warm-and-what-that-is-worth) in the composer.

Three things about that connection are decided rather than incidental:

- **It lives outside everything that swaps**, directly under `body`. Held by the transcript it would
  be a connection its own traffic kept tearing down. Its target is itself with `innerHTML`, so it is
  an inert sink: every message is `<hx-partial>` elements naming their own targets, which htmx
  applies while leaving the connecting element alone, and anything else lands somewhere harmless
  rather than over the conversation.
- **Every message is a whole current render, never a delta.** So a reconnect needs no replay and no
  cursor, a dropped frame costs nothing, and a duplicate morphs to a no-op. It is also why the first
  thing a stream sends is the current state: what a page that has just connected needs and what one
  connected for an hour needs are the same thing.
- **The server notices by polling a change token**, not by being told. `Service.token` counts a
  session's recorded steps, which is sound because a checkpoint is append-only and cheap because it
  decodes none of them. The two halves of the process stay joined only by the store, exactly as they
  would be if the worker were elsewhere.

The transcript swaps with **`outerMorph`**, and that is what lets a turn be watched: a turn records
several times while it runs, so a replacement would shut a call the reader opened to watch, over and
over, precisely while they were reading it. `test_browser.py` pins that against a real Chromium and
a real server, because it is a second render reaching a page nobody reloaded and neither a still nor
a markup assertion can see one.

## What a swap must not disturb

**A panel that arrives or changes is marked for a beat** (`data-fresh`, a colour fade in the panel's
own kind hue), because a re-render that lands silently leaves the reader to spot what moved. Two
things there are decided:

- **The change is worked out here, not taken from the swap.** Morphing reports nothing a listener
  can hear: `htmx:before:morph:node` is an extension hook rather than a DOM event, and it fires
  before htmx has decided whether the node differs. So `mainplate.js` keeps a signature per panel.
- **The signature is the *text* of a panel's blocks.** A reader unfolding a call, a search mark laid
  over a word, a kind switched off in the key: all change a panel's markup and none is news. It is
  blocks rather than the whole panel because the header row, a role and an anchor, is the same on
  every render, so nothing there is ever news either. The first render marks nothing, since every
  panel is new to the script then and a conversation flashing top to bottom points at everything.

**Following the end is being at the end**, decided in both directions by where the reader has
scrolled, and re-entered by sending a message. `land` therefore has to route its scroll through
`scrolling()` like `toEnd` does: without it, landing on the *last* panel puts the reader at the
bottom and the scroll listener switches following back on at the very moment they asked to be
somewhere in particular.

The rail (search, key, dock, shelf, handoff, theme) lives **outside** the region that swaps, so no
control is rebuilt under a reader's finger. What it projects back *onto* the transcript, the search
marks, the panel landed on, which kinds are muted, what is folded, cannot live in the markup either,
so `assets/mainplate.js` holds it as values and reapplies it after every swap. That projection is
one idempotent `repaint()` serving the first render, every swap, and every press.

## The copy button

**One sits on every panel and inside every block of code in one, and they are one control in two
places rather than two controls.** One look, one listener, one clipboard, one way of saying it
worked; what each copies is decided by where it sits, so the one in a fence hands over the fence and
the panel's own hands over what the panel says. Five things there are decided:

- **The script seats them, and the server draws none of them.** A fence is markup the Markdown
  renderer produced, so `pages.py` has no node to hang a button on inside one; rendering the panel's
  and seating the code's would be two mechanisms for one thing when the seating has to exist anyway.
  They come off before a swap and go back after it, exactly as the search marks do and for the same
  reason: a node the server never sent is a node a morph should not be reconciling.
- **What comes out is what was *written*, not what is drawn.** A message is rendered Markdown and
  the rendering is lossy in exactly the way somebody copying cares about, so a block that was
  Markdown carries its source in `data-markdown` and that is what the button hands over. It is not a
  second copy of anything: it is the same value the element was built from, put into the same
  render, and nothing else reads it. Only the kinds that *are* Markdown, since a tool's arguments
  and its return are already shown verbatim and a fence renders as the characters it was written
  with. Measured on the gallery's own conversation, carrying the sources costs the page 16%, most of
  that the system prompt, which is the longest Markdown on any page and the one a reader is least
  likely to be copying from. It is carried all the same: a fold nobody opened costs bytes, and a
  fold somebody did open with no way to lift the prompt out of it costs the control.
- **Where there is no source to carry it is `textContent`, never `innerText`.** `innerText` is what
  is *rendered*, so a folded call would copy as its summary alone and one button would answer two
  different things a click apart. The buttons are taken back out of the text first, since one seated
  inside a fence is inside the very text that fence hands over.
- **The confirmation is a value, projected.** A running turn morphs the transcript every time it
  records anything, which is exactly when somebody is lifting a result out of it, so `copied` names
  the button rather than marking it: the panel it is on and where in the panel it sits.
- **A block that scrolls has no still corner to pin to.** An absolutely positioned child of a scroll
  container travels with the content, so `.text pre` no longer scrolls and its `code` does, and the
  raw record on a rule, a bounded box that genuinely scrolls, gets no button at all.

The panel's own stands in the row of facts just left of the permalink, and the code's is inset
inward from its block's corner on both axes, which is where a reader looks for each. **The panel
keeps the spacing it has always had, and that is the point of putting the button in a row that
already exists.** Hung off the top edge of the text instead, which is where scriptorium puts it,
riding the border of a block that has one, it needs room, and the room costs every panel a strip of
empty page between its title and what it says: a change to the whole transcript's rhythm bought for
one control. The permalink gives up its own `margin-left: auto` only where the button is there to
take it over, so a page rendered with the script absent still has it flush right.

## The session list

Each row names the repository its session works in, which is what tells two conversations apart
once there is more than one. It reads `owner/repo` while a forge still reaches the repository and
the recorded id once none does, so a detached integration leaves the row saying where the session is
rather than saying nothing. That is `SELECTION` reading the repository straight out of the session's
`choice` rather than a column beside it; see [the philosophy](../philosophy.md#the-session-index-is-one-row-and-it-reaches-rather-than-copies).

A fork is drawn nested under what it came from and labelled with the turn it left at, which is
emergent from the `Origin` on each row rather than from anything inside a checkpoint.

## The picker

**Ordered widest-first: workspace, network, endpoint, model, thinking, handoff**, and then the name
and the message box, which are the composer's rather than the picker's. What files a session has is
the broadest thing about it and is one question rather than two, so it leads; the network follows
because it is the other thing deciding what the agent can do at all, where the endpoint and the
model only decide who answers; the endpoint and the model are adjacent because they are a pair, the
list being whatever the endpoint above it offers; the thinking level is a setting *on* the model, so
it sits under it; and the handoff reserve is measured *against* the model, so it comes after both.

**Every question the picker asks with a closed set of answers is one component.** `choosing` in
`pages.py` takes a legend, a toggle id, the names on offer and a body of cards, and gives back a
group that folds to what is picked, says how many options it has, and can be narrowed by typing. The
workspace, the network, the endpoint, the model and the thinking level are all built from it, and
that is why none of them is a `<select>`: a select renders its options as text in every browser, so
it could carry neither the forge a repository came from, nor the sentence under a level that is not
one, nor the fold. Having two kinds of control answering versions of one question was the thing to
remove.

**Two questions here are deliberately not cards, and both for the same reason**: `starting_at` asks
for a commit-ish and `tending_group` asks for a number of tokens, and neither has a set to draw.
Behind `choosing` they would be a card per ref a repository has, or a card that is really a text
box.

Another question with a closed set of answers is a `choosing` call and nothing else. The script
names none of the card classes, since it finds a card structurally, as a `<label>` with a radio in
it, or by the `data-name` the card declares, so a new kind of card needs no edit there. The one
selector it does name is `.models__provider`, which is not a card but the heading over a run of
them. The CSS is the one place a new kind is listed by name, because the shared card rules are a
grouped selector, so a card added without being added there is drawn unstyled and, worse, does not
fold.

Four things there are decided:

- **The fold is a checkbox and the folding is `:has()`, so nothing in the script decides it.** That
  is what keeps a shut group honest: what it draws is the card whose radio is actually checked, read
  off the radio, so there is no second copy of the choice to go stale. A summary line naming the
  model was the obvious alternative and is exactly that copy: with scripting off it names the wrong
  model from the first pick onwards. Everything the script does to a group, shutting it on a pick,
  narrowing it, checking a card the reader named, *sets* state and never reads the choice back out
  to decide anything.
- **The count of what is on offer is on the control** (`27 options`), because a shut group is one
  card and a card on its own reads as a fact about the session rather than a choice. It has to be
  rendered *inside* what the endpoint swap replaces, which is why `model_cards` returns the whole
  group, head and fold included, rather than just the list: outside it, the count keeps saying 27
  after the list under it became 46.
- **A swap resetting the fold is the right state to arrive in.** Picking an endpoint replaces the
  model group, so it comes back shut on that endpoint's default model, which is a real choice
  already made and worth seeing rather than a list to close.
- **One argument is the count, the completions and the filter**, so the three cannot disagree.
  `names` is one entry per card: passing a model's label *and* its routed id was two entries each
  and a group announcing 54 options over 27 cards. Searching by id still works, because the filter
  matches a card's whole text and the id is printed on it; the `<datalist>` is the readable half and
  a list of names interleaved with `anthropic/claude-sonnet-4-6` is not that.

The narrowing box is the one control on this page that does nothing without the script, and it is
drawn that way deliberately: the `<datalist>` beside it is the browser's own completion over the
same names, so with the file absent typing still helps and every card is still there to be picked.

**Naming one exactly is choosing it.** Taking an entry from the completion menu puts the whole name
in the box, and that is the reader having decided, so the card is checked and the group shuts rather
than leaving them to reach for the one card still showing. The match is on the whole name and never
a prefix, which is what stops the keystrokes spelling `xhigh` from stopping at `high`; every card
carries the name it answers to as `data-name`, the same string that went into the completion list.
Checking a radio from script fires nothing on its own, so the pick dispatches a real bubbling
`change`, which is what shuts the fold *and* what lets an endpoint's own `hx-get` swap the models.

**A sticky heading is measured from the scroll container's *content* edge, so that container gets no
block padding.** `.setup` carried `padding-block` and `.picker` now does, which looks like moving a
value between two elements that fill each other and is not: on a phone `.setup` is the box that
scrolls, so padding on it parked the sticky provider heading that far down the box and left a strip
above it with model cards sliding through. Inside, the padding scrolls away with the content, which
is what it was always for.

**And a scroller clips, so `.picker` carries inline padding too: the room a focus ring is drawn
into.** `overflow-y: auto` computes `overflow-x` to `auto` as well, so a control flush with the
scroller's edge has its gold cut off on that side: the base and the branch boxes, which fill their
grid columns, and every card while its group is open. Inside the scroller for the same reason the
block padding is. `TestTheFocusRingHasRoomToBeDrawn` is what fails when it goes, and it measures the
gap against the ring's own `outline-width` and `outline-offset` rather than against a number written
down twice. It sets a window narrower than the suite's own, because at 1400 the picker sits inside
its `max-width` with room to spare and the clipping, which is every narrower window and so the
common case, does not happen at all.

The picker's controls are **associated with their form by name, not by nesting**, and that is
load-bearing on the start page. There the choosing fills `main`'s growing row and the box is pinned
under it, so every radio in every group is a *sibling* of the form that posts them;
`form="choosing"` (`CHOOSING_ID` in `pages.py`) is the whole of what makes them submit, and without
it the console refuses its own page with a 422 saying a message needs an endpoint and a model. The
fork page nests its picker inside a form of the same name, so `model_cards` can carry one attribute
and serve both the pages and the `/fragments/models` swap. The fold's own checkbox is the one
control that deliberately carries *neither* a `name` nor a `form`: it is how a group is looked at,
not part of what a session is decided by. A markup assertion cannot see any of this, which is why
`TestWhatAFormPosts` asks a browser what `form.elements` holds and `TestFoldingAGroupOfCards` asks
what a shut group still posts.

## The message box

What happens to what is written in it is [the composer](composer.md); this is the box itself.

**Shift-Enter sends and plain Enter breaks the line**, which is that way round because a message
here is prose that wants paragraphs and fenced blocks: a box where the obvious key sends is a box
you cannot write one in. `sendFrom` calls **`requestSubmit`** and not `submit`, and that is the
whole of why one delegated listener serves every page: `submit()` posts *without* dispatching a
`submit` event, so htmx would never see a send on a session page and the browser would navigate away
from the conversation instead. It also runs the form's own validation, so an empty box refuses from
the keyboard exactly as it refuses from the button. The Send button names the key, because a
shortcut nothing on the page mentions is one nobody uses.

Plain Enter is the one key a mode may take, and only while the leader palette is open: what it would
otherwise do there is break a line in the middle of `/fo`. See [leaders](composer.md#leaders).

**The box is one line at rest and grows a line at a time**, to fourteen lines or two fifths of the
window, whichever is smaller, and scrolls inside itself past that. `field-sizing: content` is the
whole of the mechanism, so there is no script and no height kept anywhere a swap could take it back
from; `rows` stays in the markup as the floor for a browser without the property, which a browser
that has it ignores. The cap has a viewport term as well as a count of lines because the box sits
under the conversation it belongs to: bounded only by lines, a long message on a short window would
leave the transcript nothing. `TestTheBoxYouTypeIn` is what fails when this breaks, and it has to be
a browser: every height here is a correct rendering of *some* box, so what is asserted is how one
box changes across what is put in it, which no still and no markup assertion can see. A browser that
ignored the property would draw the `rows` floor and look entirely deliberate.

`resize: none` is *said* rather than left off, and that is not a style preference: a textarea's own
default is `resize: both`, so dropping the declaration puts the handle back. It goes because a
dragged height is an inline style that outranks the content, so the box would stop growing and stop
shrinking from the moment it was touched, and stay tall after the message had gone.

**The box is edged in the person's own hue** rather than in the neutral `--edge` every other input
takes, which is the palette's one axis applied to the thing a message is written in: cool is what
reached the model, so the box belongs on the same side of it as the panel a message becomes and as
the ground Send is painted in. The gold in a screenshot is the focus ring (`--mark`) over that
border, not the border.

**The Send control takes its own height rather than the box's**, and the row's `flex-end` puts it
level with the bottom of the box, which is where its menu hangs from anyway. Stretched to a box that
now reaches fourteen lines, it would be a slab of person-hue reading as a panel rather than a
button.

**And the cursor goes back into the box once the message has gone**, whichever way it was sent: the
button takes the focus on a click, and `hx-disable` blurs the box itself while the post is in
flight, so without this the cursor is on nothing at all by the time the answer swaps in. *When*
matters as much as whether: htmx re-enables what it disabled just after dispatching
`htmx:finally:request`, so the focus is asked for a turn of the event loop later, and asked any
sooner it is asked of a box that is still disabled and takes nothing. Only where nothing else has
claimed the focus meanwhile, so a reader who went to the search box while the message was in flight
is left where they went. `TestWhereTheCursorIsAfterSending` drives both ways of sending, and it too
has to be a browser: the focus is a live property the server never renders, and the ordering it
turns on is htmx's rather than ours.

## Markdown, and the sanitiser

A message is **rendered Markdown, then sanitised**, in `markup.py`. Both halves are required.
Python-Markdown passes raw HTML through untouched and never looks at URL schemes, so
`<script>alert(1)</script>` and `[x](javascript:alert(1))` reach the page from a plain `convert`;
`nh3` is what stops them. Do not drop it because the text "comes from the model": a reply is shaped
by whatever was pasted into the box, and this project's direction is an agent that reads
repositories.

Fenced code is highlighted, and the sanitiser is where that gets interesting. `codehilite` emits
classes, `nh3` strips `class` by default, and the fix is **`allowed_classes` with Pygments' own
`STANDARD_TYPES` vocabulary** rather than allowing the attribute. The difference is the whole point:
this text is shaped by whatever reaches the box, so an open `class` would let a reply paint itself
as the rail, the composer, or a panel of somebody else's kind. Taking the vocabulary from Pygments
rather than listing it also means a token a new release emits is allowed the day it appears, where a
hand-written list would strip it silently and that run of code would render unhighlighted with
nothing saying why. `guess_lang` is off: a wrong guess colours text by a grammar it is not written
in, which reads worse than no colour. The palette is the console's own hues in `mainplate.css`, not
an imported Pygments theme with its own opinion about light and dark.

The two converters, and why a message's newlines are treated differently from a document's, are in
[what a session is told](guidance.md#the-system-prompt-is-drawn-as-a-panel).

## Panels and rules

A turn is read out of the checkpoint as **panels of blocks**, not as a question-and-answer pair. A
block is prose, reasoning, a call with its result, or a command with its own; a panel is a run of
blocks of one kind within one model request, and it is what the page draws a coloured edge down. The
palette runs on one axis and every kind takes its side from it: cool is what the person produced
(their message, their steer, their command), warm is what the model produced (its answer, its
reasoning drawn back toward the ink, a call in ochre). A kind added later has its hue decided by
that rather than chosen for it. A part kind `parted` has no rendering for is passed over rather than
refused, because the provider and Pydantic AI are both free to add one.

**The axis is who wrote it and not who was told**, which a command is the case that settles: it is
the one kind on the person's side that no model ever saw, and what says so is the `title` on its
role and its chip in the key rather than a hue of its own. A colour for "the model does not know
about this" would be a second axis over one palette.

**Every kind is named after the thing it holds, in the word the page prints**, so `prompt`, `steer`
and `command` are the labels, the `data-kind` values and the words behind them in `records.py`. They
were `you`, `you (steering)` and `you (ran)`, which sent a reader who learned a word from a panel
looking for an identifier the code does not have. Two things that cost, both accepted:

- **`prompt` overlaps `records.Prompt`, which is narrower.** The record is a message that *must*
  open a turn; the panel is whatever message a turn opened on, and a `Steer` arriving at an idle
  session is drawn as one. That is the collision this vocabulary already has with `StepKind` on
  `command` and `tool`, safe for the same reason: they never mix and mypy refuses the crossing.
- **Three labels no longer share the word `you`**, which was the non-colour cue that they are one
  side. The hue and `data-side` still carry it, and `TITLES` is where `you (ran)` put its extra
  meaning down.

`muted` is keyed by these values in `localStorage`, so the rename left every stored decision naming
a kind nothing draws: a reader who had quieted one gets it back once and mutes it again. Said rather
than migrated, because it is one press and the alternative is code that reads a vocabulary this
console no longer has.

**A panel says what is in it; a rule says what is true of the request or the turn around it.** Which
turn it is, where the session may be forked from, the worktree a request was made against, and what
it spent all belong to the exchange rather than to any one run of blocks, and hung on a panel they
had to be hung on a chosen one. Usage settles it: it belongs to a `ModelResponse`, and one response
becomes as many panels as it has kinds of part, so there is no attribution rule from a response to a
panel that is not invented. `transcript_region` groups the panels by `turn` and then watches `asked`
change within one, so nothing keeps a second list of where a turn or a request begins.

**A rule names its turn, and that is legibility rather than decoration.** A turn rule sits directly
under the last panel of the turn before it, so a bare row of figures there reads as a footer
summarising what is *above* it, which is the opposite of what it says. The `#1` against the `#1.0`
on the panels below settles the direction, and doubles as the permalink to the boundary the fork
acts on. A rule inside a turn names its request the same way, as `r1.1`, which is also what opens
the record: the whole address rather than the index within the turn, for `Panel.label`'s reason one
level along, since a rule inside a turn draws no `#N` and a bare `r1` said which request without
saying of what. The `r` is what keeps it from being read as a panel, which numbers a different axis:
`#3.1` is turn 3's second *panel* and `r3.1` is its second *request*.

### The record hangs off a request, not a panel

`Source`, `sourced_at` and the per-panel `recorded` disclosure are gone. A panel is a run of blocks
of one kind and a request is a round trip, so a panel's record was a *slice* of a stored value
reached by indices one walk had to hand another. A request has a key of its own, so `requested_at`
is a lookup, and it answers while the turn is still running: a step is written once and never
rewritten, where `turn:{n}:messages` does not exist until the turn ends.

**A rule per request, with the turn rule being the first one.** That adds no concept: every rule the
transcript has ever drawn already stood at a request boundary, because a turn opens with its first
request. A marker in the panel's own header row was wrong in a way this fixes, since one request
becomes as many panels as it has kinds of part, so a marker on one of them attributed a round trip
to a fraction of itself.

It is what `Panel.asked` is for, and why `panelled` cuts by the request *and* the kind rather than
by the kind alone. The cost is a merge: two responses that both answer in prose used to be one panel
and are now two. That is the point rather than a regression, because a merged panel left no gap
between requests for a rule to stand in, and it is also the more accurate reading.

A turn's first rule carries the turn's own facts as well, which turn it is, where it may be forked
from, the tree it started on, and what the **whole turn** spent, and the later ones carry only their
own request's. The tree there comes off the turn's first panel rather than out of request 0, which
is the same key read a request earlier: `turn:{n}:tree:0` is written *before* the model is asked, so
a turn whose first answer has not landed yet still says what it started on. Where the figures on one
rule are a turn's and on the next a request's, the title says which; only `rule--turn` is what the
dock's turn arrows step, or a turn with four round trips in it would give that column four stops.

## Every panel folds, from its own row

**A panel is a `<details>`, its row of facts is the `<summary>`, and the mark sits immediately right
of the title.** That is one mechanism for what used to be three folds and five kinds that could not
fold at all. What a reader wants put away is theirs to decide, so the console says only where each
kind *starts* (`OPENS` in `pages.py`) and nothing more.

**It began as a complaint about vertical space, and the space was the symptom.** A stretch of
reasoning, the standing system prompt and a delivered guidance file each carried a `<details>` of
its own whose summary was the front of its own body, so a panel spent one row saying what it was and
a second row saying it again, and once open the second row held a lone marker and nothing else.
Those three had no fold worth keeping, because the thing their summary said is the thing the panel's
row already says. They are drawn plain now, and the panel is their fold.

**A call and a command keep theirs, and the difference is whether the summary is the block
restated.** A tool's name with its outcome and how long it ran, and a command's line with the status
a program chose, are facts about the block rather than a prefix of it, and a panel holds a whole
*batch* of either, so a reader wanting one read out of three needs a fold per call as well as one
per panel. Two levels, and each is named: a panel's fold is the reader's, a call's is the call's.

**The row is the summary and everything in it keeps working**, which is the browser's rule rather
than this markup's: a summary's activation behaviour skips a press whose target is interactive
content, so the permalink navigates and the copy button copies without folding the panel underneath.
`TestFoldingAPanel` asks Chromium both questions, because a page where that did not hold would look
identical in the markup and fold the panel a reader had just linked to.

**The mark is a `::after` on the role rather than the summary's own**, and that is forced: a
disclosure's marker always leads the row, and this one has to come *between* the title and the line
it stands for. On the role it takes the panel's kind colour for free, so there is no second list of
hues to keep in step.

**Shut, the transcript is its own outline**, one row per panel, which is what the dock's
fold-everything button now produces. That is a wider meaning than it had, since it used to put the
calls away, and it is the one worth having: what `muted` could never give a reader coming back to a
finished conversation is a conversation that takes less room.

**A third dock button puts every fold back where the console had it**, and it is not a midpoint
between the two beside it: those set every fold one way, and this hands out a different answer per
fold, a call shut and a reply open and a system prompt away. It is the way back from either of the
others, which without it are one-way presses over a whole conversation. What makes it possible is
`opens` in `pages.py`, which writes `data-opens` beside the `open` attribute: `open` is the state
and is what makes the page work with no script, `data-opens` is where the console *put* it, and the
two stop being the same thing the moment anything presses anything. Not a copy that can drift, then,
but the original beside the current.

**`mainplate.js` records every toggle as a decision, a morph's own included, and that is
deliberate.** A call still out is drawn open, so the morph that delivers one records it open, and a
reader watching it fill in keeps it open when the result lands rather than having it collapse under
them at the moment it became worth reading. Told apart, by comparing against the `data-opens` the
server just sent, a still-out call would shut itself on arrival and a reader could not ask
otherwise, because at the moment they would press, open is already what the console said, so the
press reads as agreeing rather than as deciding. The two are indistinguishable there, so nothing
tries. What it costs is that little stays undecided on a turn being watched, and the third button is
the way back. `TestWatchingATurnArrive` pins both halves against each other.

**A turn out on a tool call draws no waiting panel at all.** A call with no result is already drawn
working, on its own panel, and it is the model's call, so a second panel of dots under it says the
same thing twice, and says it in a shape nothing is writing, since an empty reply below a call reads
as a turn that has started answering where what is happening is a tool running. `out_on_a_call` asks
it of the turn being answered rather than of the last panel on the page, because a person can type
while a reply is coming and what is at the bottom may be their message. A *command* running is not
this: it runs outside the conversation and no model was told about it, so it says nothing about
whether one is answering.

**A panel whose default would otherwise move carries the working dots on its own row instead.** The
panel saying a reply is being written, and a stretch of context whose instructions no pass has
composed yet, are both drawn *shut* with the dots in the opening line's place. Two things fall out
of one decision: a panel opened to show three dots is a row spent on three dots, which is the thing
this row exists not to spend; and a fold whose default moves under a reader is one the console can
no longer draw either way once a morph has recorded the state it delivered.

## The line a shut panel stands for

**Every panel's row carries the front of what is in it, clipped by the browser at the panel's
width.** Folding prose is only worth offering if the shut state identifies it, so this is what makes
the fold above worth having on a message and a reply rather than only on reference material.

What that line *is* differs by what the blocks are, and `panel_opening` draws the split the old
per-block folds already drew. Prose stands for itself with its own opening. A call and a command
have no prose to take a front off, so the panel *names* what is in it: `read, read` and `git status
--short, git diff --quiet`. The first block for the prose kinds and every block for the two that are
named, which is not an inconsistency: an opening is a prefix, and a prefix of a run of paragraphs is
the front of the first, where a list of calls naming only its first would be hiding the rest.

There is deliberately no length in the markup: `text-overflow: ellipsis` measures a line against a
box, which is a measurement the server cannot make, and any character count it picked would cut in
the wrong place at every other width. `min-width: 0` is what lets the flex item shrink below its
content and so is the whole of what makes the ellipsis appear.

**The row carries the line and no figure beside it, a document's included.** The tempting one is a
character count on a system prompt or a delivered guidance file, since what is in either is paid for
on every request from here on and no rule speaks for that. It is refused because characters are not
the unit: every other number on this page is tokens or money, a window is measured in tokens, and a
count that cannot be compared against the gauge on the rule below it is a number a reader converts
rather than reads. What a request carried is a fact about the request, so it belongs on the rule
with the rest of them.

`pages.OPENING` is a bound on what is *carried* rather than on what is shown, and it exists because
a line holding the whole of a long block would put every word of it on the page twice, on a region
re-rendered whenever the turn in flight records anything. The number is what keeps the clipping
honest: clipped short of it the ellipsis says there is more, and clipped *at* it with no ellipsis it
would say there is not. So it has to exceed what the widest panel can show, which is a bounded
question because the transcript is capped at `--measure`, and
`TestTheLineAShutPanelStandsFor::test_more_is_carried_than_the_widest_panel_can_ever_show` measures
the worst case there is, the narrowest glyph the prose face draws repeated, and fails if it fits.

Three consequences of the line being a prefix of the body under it:

- **It is hidden once open**, since a prefix standing directly above the body says nothing twice.
  Hidden and not removed: taken out of the flow the row's free space collapses and the permalink
  slides left across the panel, and [a control that moves under the finger that pressed
  it](../philosophy.md#controls) cannot be pressed twice.
- **The search skips `.opening`.** Found in both, the dock would step through one sentence at two
  stops and the count would say there is twice as much of it as there is. It is the one entry in
  `markHits`'s skip list that is there for being a second copy rather than for not being
  conversation.
- **A copy button reads `data-markdown`**, as it does for every other Markdown block, so what comes
  out is the source and the opening is in it once.

**It is the source rather than the rendering**, which is what a message written to be markup makes
worth saying: the sanitiser never sees this line, so the escaping of a text child is the whole of
what keeps it inert, and `test_markup_in_a_message_is_still_text_in_the_line_its_panel_stands_for`
reads it back with a real HTML parser.

## What the figures on a rule say

Six of them, and none is picked out from the others: how long it took, how much context it carried
and how much of that came out of the cache, how full the model's window is, how much came back, what
it cost, and what the conversation has cost so far. The cost used to take a stronger ink, which read
as the figure to look at; which one somebody is reading changes with what they are doing, so picking
one is deciding that for them.

**The input figure is the context and not the sum**, which is `Spent.context`'s argument said on the
page. Every request of a turn carries the whole conversation again, so a summed input says what the
provider charged for, several times over about the same tokens; what a reader wants off a rule is
how much of the window is gone, which is where the turn's *last* request left it. The summed figure
is still true and still drawn, under the message box, as what the session has been charged for.

**A symbol per figure, and the words in the titles.** A rule is one line that must not wrap and it
now carries six figures where it carried three. `↑` and `↓` are a count of tokens going up to the
model and coming back, `▣` is how much of the first came out of the provider's cache instead, and
`Δ` against `Σ` is what this exchange added against the running total, which is that pair's own
notation and reads as a pair rather than as two prices to tell apart by size. Five cells against the
twenty or so the words would take, on the one line that cannot afford them.

`▣` is the one of the five that is a mark rather than notation, and it is a mark rather than the `↩`
tried before it for two reasons: `↩` is already the sidebar's aside, so it would be one glyph
reading two ways on one page, and beside `↑` it read as a third direction rather than as a fact
about the first. A square with something in it reads as a store, sits on the cell like every other
symbol here, and cannot be mistaken for an arrow.

The cached count sits inside the context figure as `↑96K (▣45K)` rather than beside it, because it
is a fact about that count and not a figure of its own, the way the wire's own numbers nest. The
bracket is what says so.

**The separator is interleaved rather than carried by each figure.** Every one of these is drawn
only where there is something to say, so a dot baked into a figure is a dot that appears with it:
the time had none and the count after it had one, and a turn nothing timed then opened with a dot
standing for nothing.

**The running total is inclusive of what the rule speaks for**, so a turn rule says what the
conversation had cost by the end of the turn it opens, exactly as it already says what that turn
spent: both figures on it summarise what is below rather than what is above. It follows
`altogether`'s rule one rule at a time, so the first unpriced turn takes it off every rule below,
and it is left off entirely where it *is* the figure beside it, since the first priced turn of a
session would otherwise print one number twice.

**And the line itself is a gauge.** It is filled from the left as far as the request's context
reaches into the model's window, shading from the rule's own colour toward `--vermilion`, which is
the palette's red already. A rule is a hairline drawn across the whole column at every request
boundary, so the one thing a long conversation most wants to know costs no row and no control: a
reader scrolling down watches the line lengthen and warm. Four things there are decided:

- **The scale is the whole width and the fill is clipped to it**, rather than the gradient being
  squeezed into the filled part. Squeezed, every conversation ends in red and the colour at a point
  means nothing; clipped, a point along the line means the same fraction on every rule of every
  session. It shades rather than steps because a threshold is a number somebody would have to invent
  and defend, where the whole point is that this gets worse gradually.
- **The server computes fractions and nothing else.** The colours, the geometry and the cap are
  decisions, so they live in the stylesheet; a fraction is a fact about one request, and there is
  nowhere else it could come from. They are the only inline styles this console writes.
- **`rule__reserve` is the second mark on that scale**, a short bar standing across the rule where
  the session's reserve opens, drawn only where auto-handoff is on. On the same scale as the fill,
  so the two are read against one another: the fill says how far this request got and the mark says
  where a handoff would be asked for. See [handing off without being
  asked](composer.md#handing-off-without-being-asked) for why it is an element rather than a second
  pseudo.
- **The window comes from the reference and not from the checkpoint.** `Conversation.window` is
  `facts_of` asked about the session's own choice, which is the same lookup that prices a turn, so a
  database that learns a model's window shows it on every session already running on that model.
  Absent, with no database, an endpoint that no longer lists the recorded id, or a model with no
  record, the counts are drawn and the fraction and the gauge simply are not, which is what a rule
  was before there was one.

## The dock

The turn column steps `rule--turn` rather than the person's panels, and that is a removal. There is
exactly one message per turn, so a "previous message of yours" column and a "previous turn" column
visit the same positions and differ only in where they stop: two controls answering one question,
which is the thing this console removes wherever it finds it. Every arrow declares what it steps
over (`data-stop`) rather than being told apart by what it lacks, because a button identified as
"the one with no side" stops being identifiable the instant a second kind of stop exists. The
modifier and not every rule, because a rule now stands at every model request and a turn with four
round trips in it would otherwise give that column four stops.

**Four columns, widest first**, which is the picker's own ordering one control along: where the
model's history starts again, then turns, then every panel in play, then one side of them. The
forget column is the newest and the only one that finds its stops by an attribute a rule declares
about itself rather than by a class; see [forget](composer.md#forget) for why it is drawn in every
session.

**The leap to the start lands on the rule that opens the first turn**, which is the top of the
transcript rather than the first panel in it: a turn rule carries that turn's own facts and its fork
link, and where the stretch has instructions there is a system prompt panel between the rule and the
message, so landing on the panel put the reader below both with nothing saying so. The leap to the
end is still the last panel, since nothing is drawn under one.

## Opening the raw record

The raw record hangs off a **model request** rather than a panel, on the rule at that request's own
boundary. Two things about how it is fetched are decided rather than incidental:

- **On demand and `hx-preserve`d.** A running turn re-renders the transcript repeatedly, so the raw
  record of every request is not something to carry in it; and because the server renders the
  disclosure closed, a morph takes the `open` attribute back off unless the element is preserved.
  htmx reads `hx-preserve` off the *incoming* markup, so taking it off the live node proves nothing.
- **`once` is safe here in a way it never was under a panel.** A step's key is written once and
  never rewritten, so a request's record is settled the moment it exists, where a panel's record
  came out of `turn:{n}:messages`, which does not exist until the turn ends. So a tag can be opened
  mid-turn and the panel disclosure could not.

**Opened, it grows the rule downward rather than lying over the conversation.** A record read
against the reply it came from is worth more than a page that holds still, and an overlay is the one
shape where the two cannot be looked at together. It takes a line of the rule to itself, since the
rule wraps and the record asks for the whole of one, which is what a phone decides: sharing the line
leaves the record a column six characters wide, and pinning the figures so it does not is a row that
runs off the side of the screen.

**[A control that toggles may not move.](../philosophy.md#controls)** The record tag is where that
was learned: what wrapped onto the second line was the whole `<details>`, so `r0` set off across the
rule on the way to opening it.

The shape that gets this right is the general one, so reach for it before inventing another. What
wraps must be the *content* and never the summary above it, which means the summary and the content
have to be separate items of the row that wraps. `display: contents` on the `<details>` is what does
that: the tag makes no box of its own, so the summary stays an item in its own place and the content
becomes the item that takes a line. Both candidates for that item are told the same thing, because
`::details-content` is the box a browser wraps a disclosure's content in and the content itself is
the item where there is no such box, and the closed state has to hide *both* or an empty item leaves
every shut rule a row gap taller.

`TestOpeningTheRecordBehindARequest` is what fails when this breaks, and it has to be a browser:
both states are correct markup and each screenshot is right on its own, so what is measured is one
element's box across the press. Within its rule rather than within the window, because the page
follows the end and a record opening at the bottom scrolls the transcript under it, which is the
console doing what it is asked, and would otherwise report as the marker having moved.
