# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Guidance a session is answered under, from two scopes. **Console guidance** is every `.md` under
  `<config home>/mainplate/guidance/`, the operator's own and true of every session; **repository
  guidance** is the project's own `AGENTS.md`, read out of the worktree the session works in.
  The repository is concatenated last and so wins where the two disagree, because a repository is
  right about itself. `AGENTS.md` rather than a name this console invented, with `CLAUDE.md` as the
  fallback where a directory has no `AGENTS.md`: a file only mainplate can read is knowledge that
  does not survive turning mainplate off, which is the whole reason to write it in the repository
  rather than in a prompt. A leading YAML block is taken off, so a `paths:` list never reaches a
  context window.
- An index of the guidance elsewhere in the repository, one row per file with the `description` from
  its own frontmatter, carried in the instructions on every request. That a directory *has*
  conventions is one line and what they are is a page, so the line rides in the prompt and the page
  is read when it is wanted. It is asked of git rather than walked, so a `.venv` is never descended.
- The guidance covering a directory, handed over on the request after a file tool reaches into it,
  as a system-voice message rather than an edit to the instructions, so the cached prefix is left
  alone. Whether it has already been handed over is asked of the history the model is about to be
  given, which answers every case with one question: the console delivered it, the model read the
  file itself, the model wrote the file, a fork carried it across, or a `forget` dropped it and it
  is handed over again. A `bash` command reaches none of this, because its argv is the model's and a
  path inside it is not this console's to parse; the index is what covers that.
- The system prompt drawn as a panel, folded, under the rule that opens the stretch of context it
  belongs to. It is read from what that stretch recorded rather than out of a turn's messages, so it
  is on the page while the first turn is still being answered rather than only once one has landed; a
  stretch nothing has composed for yet draws the panel with the working dots in it, which is where a
  session sits for as long as its clone and its worktree take. Drawn as the Markdown it is, since
  what is under the fold is `.md` files and a wall of `##` is the one reading of them nobody meant;
  the source rides along as `data-markdown`, so the copy button still hands back exactly what was
  sent. The fold is shut and stands for itself with its own opening line, clipped at the width of the
  panel, with the character count beside it. A console that shows what a model answered and hides
  what it was told is showing half of how a turn happened.
- Guidance handed over mid-turn drawn as a `guidance` panel, in the same fold, at the position it was
  delivered. Its own kind rather than the system prompt's, because the two sit in different places in
  the request - `instructions` in front of the cached prefix against a system part appended once into
  the history - reach the model with different authority depending on which model it is, and are two
  things the key can quiet apart. Shut, it names the file it came from, which is the line it opens
  with.
- Places reached by name rather than by path: `read`, `edit` and `create` take a `root`, and a
  command finds `$MAINPLATE_WORKTREE` and `$MAINPLATE_SCRATCH` in its environment. A worktree sits
  under 32 hex characters of session id, and a model reproducing those from memory eventually
  reproduces them wrong, which costs a refusal and a round trip to recover from. The names are one
  vocabulary both the tools and the sandbox read, so the two surfaces of one answer cannot drift.
- What a session is answered under recorded as a step, exactly as the model is sent it, composed
  once per stretch of context before that stretch's first request and replayed after that.
  Instructions sit in front of the cached prefix, so composing them again on a later turn would
  re-price every remaining request the moment anything under them moved, and a session working on a
  repository's own guidance moves it constantly. A `forget` ends a stretch and composes again, which
  costs nothing: the prefix it would have invalidated has just been thrown away.

### Changed

- A stretch of the model's reasoning folds, drawn open, with its own opening line as the summary and
  the browser clipping that line at whatever width the panel has. Open is what a turn being watched
  needs, since a fold rendered shut would hide the thinking at the moment it is worth watching; shut,
  a stretch is one line, and the dock's fold-everything button now puts every one of them away in one
  press. What a copy button hands back is unchanged, because it reads the source rather than the page.
- Panels are named after what they hold, in the word the page prints: `prompt` and `steer` where
  they read `you` and `you (steering)`. A reader who learns a word from a panel now finds it in the
  code behind it. The `data-kind` values changed with the labels, and the reader's muted-kind
  choices are stored under those values, so a kind that was quieted comes back once and is quieted
  again.

## [0.0.1]

### Added

- A chat console over a Pydantic AI agent, where each session is a durable workflow under
  `without-durability`'s SQLite store: the checkpoint *is* the conversation, so a session survives
  a restart and a reply in flight is answered rather than lost.
- `StepwiseDurability`, a Pydantic AI capability that routes an agent's model requests *and* its
  tool calls through the running session's checkpoint, so a pass that reaches the provider and then
  dies does not pay for that answer twice, and a tool that has already read a file or written one
  is not run again against a directory that has moved since. Model requests are numbered by
  position within the turn; tool calls are keyed by the call's own id instead, because a batch of
  them runs concurrently and a counter would name a record by whichever won the race. Each response
  is *priced* and *timed* on the way past, before the step records it, so what a turn cost in money
  and in seconds is in the checkpoint beside what it said: settled the moment the request is
  answered, where re-deriving either later would quietly change what an old session came to. A tool
  call is timed as well, under its own id beside its result, because a tool returns a value of its
  own shape and a record carrying both would be indistinguishable from a tool that returned a pair.
  It is transparent outside a session, so the same agent stays usable in a script or a test.
- `mainplate serve`, which runs the console and the worker that answers its sessions over one
  SQLite file.
- A conversation read as panels of blocks, so reasoning and a tool call each get their own panel
  and their own colour beside the answer they belong to. Messages are rendered as Markdown and
  sanitised before they reach the page, and a tool's arguments are laid out rather than shown as
  the one line the model sent. A call carries how long it ran beside its name, so a folded turn
  says where its time went without being opened. Everything monospace is drawn in a vendored
  JuliaMono on a grid stated in whole pixels, so the tables and trees a model answers in, the gutter
  down every file it reads, and the arrows, dingbats and symbols it reaches for are all on one cell
  and join into lines rather than into dashes, instead of depending on what the reader happens to
  have installed. Reasoning is set in italic, and the code a model quotes while reasoning is not.
- A turn drawn as it happens, rather than all at once when it finishes. The responses and tool
  results behind a running turn are already in the checkpoint, recorded step by step so a resumed
  pass does not pay for them twice, so the page reads those instead of waiting for the turn's
  messages: reasoning appears, then a call with its arguments, then its result, then the next
  request. A call still out is drawn working, which is a state the transcript could always describe
  and nothing could previously produce.
- One live connection per page, held open for as long as the page is, carrying whatever it is
  watching as that changes. Each message is a whole current render rather than a delta, so a
  dropped connection costs nothing and a reconnect needs no replay; each names the region it is
  for, so a second region joins the same connection rather than opening another. The server notices
  by counting a session's recorded steps, which decodes none of them, so a quiet console sends no
  bytes at all.
- A rail beside the conversation: find-and-step search, a key that filters and doubles as the
  colour legend, a dock that steps whole turns, every panel in play, or only what the model
  produced, and folds every tool call, a follow-the-end toggle, and a light/dark/system theme. All
  of it is an enhancement; with JavaScript off the console still renders, posts, and folds.
- A **shelf**: text written and not sent, kept for one conversation and pulled back into the box on
  demand. `Keep` sits beside Send because it acts on the box, and the list of what is kept is in the
  rail where nothing rebuilds it mid-turn. Keeping clears the box and taking adds to it rather than
  replacing what is there, so several kept notes assemble into one message. A fork inherits what its
  parent kept, which the script copies because the server is never told a draft exists. It lives in
  the browser, so it is per machine for now.
- **Steering**: a message put to the model in the turn it is answering now, rather than queued for
  the next one. It is written into the checkpoint from outside the pass, because the worker may be
  another process, and appended to the request the agent is about to make, so it travels up with
  whatever tool results are going the same way and shapes the very next answer rather than the one
  after it. A steer arriving as the turn would end has no request left to carry it, so it redirects
  the run into one more instead of being stranded. Both are recorded steps, so a resumed pass asks
  the same questions rather than whatever is queued by then. It reads back as a `steer` panel below
  the tool results it travelled with and above the answer it shaped, and it is on the page the
  instant it is sent rather than when the turn ends.
- **`Send` decides for itself whether a message steers**, because neither the button nor the reader
  can know: the page was rendered from a checkpoint that has moved by the time a paragraph has been
  typed into it, so choosing between two moments on the page is choosing against a state that no
  longer holds. The server reads the record and writes to it in one place instead, steering a turn
  that is being answered and starting one where none is. `Wait for the next turn` stays in the menu
  as the one answer the record cannot settle. A turn stops listening by *claiming* the next steer
  slot rather than by reading it, so the pass and whoever is typing contend for one key that the
  store settles: whoever loses is told what the winner put there, and a message sent as a turn ends
  becomes a turn of its own instead of going somewhere nothing would ever read.
- **A rule at every model request**, carrying the worktree taken before it, how long it took, what
  its answer cost, and the raw record behind it, which is the unit the checkpoint actually has a key
  for: a panel is a run of blocks of one kind and a request is a round trip, so a panel's record was
  a slice of a stored value reached by indices one walk had to hand another. It adds no concept,
  because every rule the transcript draws already stood at a request boundary - a turn opens with its
  first request - and the rule that opens a turn carries the turn's own facts besides. A record can
  be opened while the turn is still running, and opens in place, below the rule it belongs to.
- **Asides**: a fork recorded as a step out you mean to come back from, and a way back that sends a
  message into the conversation it came out of. Nothing mechanical separates an aside from a fork, so
  what is recorded is only what was meant, and what it buys is that the sidebar draws a digression as
  one. Coming back is a *message* rather than a merge: splicing an aside's turns into its parent
  would leave the parent holding requests whose context never existed. The way back is offered from
  any fork, since every fork knows where it came from.
- **Fork**, in a menu behind a caret beside Send: it asks the message in the box in a new session
  carrying this whole conversation and leaves the original untouched, which is the same operation the
  `fork` link on every rule performs, aimed at the end rather than at a turn. Where a message goes is
  one field on the composer's form, posted as the submit button's own value, and the menu is a
  `<details>` of submit buttons, so the whole control opens, chooses and sends with JavaScript off.
  Shift-Enter still means Send. Forking the end of a conversation was always supported and reachable
  by nothing.
- A rule opening each turn, carrying everything true of the turn rather than of any panel inside it:
  which turn it is, where the session may be forked from, the worktree the turn started on, and what
  it spent in time, tokens and money. All three are read from the same recorded responses the panels
  are, so a turn being answered fills its rule in as it runs rather than showing nothing until it
  lands. The time is what the turn spent waiting on the provider, summed over its round trips, since
  the calls it made in between are timed on their own panels and ran at once. A model nobody
  publishes a price for shows counts and no money, and a turn nothing timed shows no seconds, which
  is the same blank a card shows; the session's own total sits under the message box. Forking moved here from a link
  revealed by hovering a message, which is where a touch screen could not reach it at all.
- A panel marked for a beat when it arrives or when what it says changes, tinted in its own kind's
  hue, so a reader watching a turn fill in is told which part of it moved rather than left to spot
  it. Worked out from what a panel *says*, so unfolding a call or laying a search mark over one is
  not mistaken for news, and suppressed on a first render, where every panel is new.
- A copy button on every panel and inside every block of code in one, so what a model answered, what
  a call was handed, and what it gave back can each be taken off the page whole. One control in two
  places: the panel's own stands in its row of facts and hands over the whole of what it says, and a
  block of code sits inside its own corner and hands over itself. What comes out is the Markdown a
  message was written as rather than a reading of the rendering of it, so the fences, the emphasis
  and the tables survive being copied, and it does not depend on what the reader has open, so a call
  copies the same folded as unfolded.
- Following the end as a mode rather than a setting: a page opens pinned to the end, scrolling away
  releases it, scrolling back to the bottom re-enters it, and sending a message re-enters it too,
  since what a reader wants to see after typing is the answer to what they just sent.
- Shift-Enter sends a message and plain Enter breaks the line, which is that way round because a
  message here is prose that often wants a second paragraph and a fenced block. An empty box refuses
  from the keyboard exactly as it refuses from the button, and the cursor goes back into the box
  once the message has gone, whichever way it was sent.
- A message box that is one line at rest and grows a line at a time with what is typed, to fourteen
  lines or two fifths of the window, whichever is smaller, and scrolls inside itself past that. Send
  keeps its own height beside it rather than growing into a slab, and the box is edged in the
  person's own hue, so what a message is written in sits on the same side of the palette as the
  panel it becomes.
- A console that reads on a phone. Under 48rem the session list stops being a 17rem column and
  becomes a strip of chips across the top that scrolls sideways, which gives the conversation all
  but about a twentieth of the height and keeps every session one swipe away with no control to
  learn; the session being read is brought into view, on a strip and on a full-height list alike.
  The choosing on the new-session page becomes one scroller rather than three nested ones, with the
  message box still pinned beneath it, and the fork page scrolls as the single long thing it is.
  Every field that takes text or a choice is held at 16px, which is what stops a phone zooming the
  page as it is focused, and every control in the rail is sized to be hit rather than pointed at.
  A branch link, which a wide window reveals on hover, is drawn always where nothing can hover.
- `mainplate install` and `mainplate uninstall`, which converge and remove a user systemd unit
  pointing at the interpreter that ran them. Any `MAINPLATE_*` setting lives in an
  `EnvironmentFile` created `0600` on the first install and never overwritten.
- Endpoints in `config.yaml`: a URL, the API format spoken to it (`anthropic` or `openai`), and a
  credential. Each session records the endpoint and model it was created on and is answered on them
  for life, so changing what is configured leaves existing conversations readable. Credentials are
  read from the `0600` file and handed to the SDK, so they never enter the process environment.
- Model discovery: no models are configured anywhere. Each endpoint's own model-list API is asked
  what it serves, once before the console takes traffic and then on a timer, and the picker offers
  whatever comes back, grouped by the vendor each model comes from. A refresh that fails keeps the
  models discovered earlier; a first read that fails is a startup failure naming the endpoint.
  `default_model` names which one a new session starts on, defaulting to whatever the endpoint
  listed first. What the picker shows is what an endpoint *advertises*, which is narrower than what
  it will route, so an existing session on a model that never appears in the list is still
  answered: only a missing endpoint stops one.
- exe.dev support: on a VM with the built-in LLM integration, `mainplate install` discovers it
  through the reflection integration and writes keyless endpoints, so the box holds no credential
  at all. One gateway gets one endpoint per API format, which between them reach Anthropic, OpenAI,
  Fireworks, and xAI: around seventy models with nothing configured.
- A thinking level on every session, chosen beside the endpoint and the model and fixed with them
  for its life. Eight values, because saying nothing about thinking, asking for it to be off, and
  asking for it at the provider's own budget are three different requests rather than gradations of
  one. The effort names come from Pydantic AI's own type, so a level it adds reaches the picker
  without a change here. `default_thinking` names the one a new session starts on.
- Forking: any turn can be branched into a new session that carries the turns before it, on a
  different model, a different endpoint, or a different thinking level. A fork is a *copy* of an
  immutable prefix rather than a pointer into its parent, so each session's checkpoint stays the
  whole of its own conversation and neither can change what the other reads. The branch point is
  before the forked turn's message, which comes across editable and is asked again on the new
  model, so seeing a turn answered differently never means retyping the question. The sidebar draws
  the resulting tree, each fork under what it came from and labelled with the turn it left at.
- A repository picker on the new-session page, alongside the endpoint, model and thinking level. Where
  the repositories come from is an interface (`forge.py`) with one implementation: on an exe.dev VM,
  `ExeDevGitHub` offers whatever GitHub integrations are attached, which needs no credential at all
  because exe.dev injects one at its own edge. Anywhere else no forge reaches anything, the picker
  does not appear, and the console is what it was before: a place to talk. A session may also choose
  no repository. A fork may *attach* a repository to a session that had none, and may not *swap*
  one for another: re-asking a turn against different files is a different question, where carrying
  on with files where there were none is the ordinary shape of thinking something through and then
  going to work on it.
- Git snapshots: a session that picked a repository gets a worktree of its own, and the tree is
  recorded before every model request rather than once per turn, so a turn that edits files records
  the state on each side of the work. A model request is the only boundary where that is honest:
  the tools of the previous batch have all returned, where a capture between two calls of one batch
  would record a tree the other calls were still writing to. Snapshots go through a shadow index,
  so nothing a reader can
  see moves - not their staged changes, not `HEAD`, not a branch, not `git log` - and are chained
  under `refs/mainplate/snapshots` so they survive `git gc`. An unchanged worktree writes no new
  object at all. Forking checks the new session's worktree out at the tree the forked turn
  originally saw, so a branch re-asks its question against the files that question was asked about.
  Snapshots are gitignore-aware, so what a branch checks out is the source as that turn saw it and
  never a `.venv`, a build directory, or an untracked file holding a secret.
- Two isolation settings on a session, picked when it is created and fixed for its life like the
  endpoint and the model, with forking the way to change them. **What files it has** is a repository
  it works in, no files at all, or this whole machine. **Network** is on or off, and off rather than
  a list of allowed hosts, because an allowlist holding a code forge holds every gist on it and one
  holding a package registry holds a package anybody can publish.
- The two are independent, because the whole-machine setting is still a sandbox with `/` bound
  rather than no sandbox: a session can have every file and no network, or a worktree and a network.
  A session on the whole machine can read this console's own configuration and its store, which is
  what choosing it means, and the card says so.
- Where a session works and what its tools may touch are **one** question on the picker, not two
  that have to be kept agreeing: the choices are every repository this console can reach, plus no
  files and this whole machine. Picking one settles both, so they cannot disagree at the source.
- A `bash` tool, on sessions with somewhere to run one and only where there is a sandbox to run it
  in. Every command runs in a mount namespace of its own holding that session's worktree, its clone
  read-only, and a read-only system: there is no network, no home directory, and nothing belonging
  to any other session. Reading git works, so `status`, `diff`, `log` and `blame` all answer, while
  `add`, `commit` and `stash` fail on a read-only filesystem. That is deliberate rather than
  incidental: a git write from inside would be a second history that no panel shows, no fork
  inherits and no rewind restores, and committing is the person's to do. Snapshots are unaffected
  because they run outside the sandbox, so the history they are chained onto cannot be rewritten
  from in there.
- Each command gets a new namespace and starts in the session's worktree, so nothing persists
  between two calls: no working directory, no exported variable, no background process. That follows from how a pass resumes rather than from
  frugality, since a sandbox held across calls would offer its state on a first pass and withhold it
  on a resumed one, where recorded results are replayed instead of re-run. Output is capped to its
  first and last lines with a count of what was dropped, a command that runs past its time limit is
  stopped and says so, and a command's exit status is always stated so success is never inferred
  from an empty answer.
- A scratch directory per session, beside its worktree and bound read-write, for whatever is not the
  repository's: a build cache, a downloaded artifact, a note to itself. It survives from one call to
  the next and from one turn to the next, and nothing snapshots it, which is the same decision as
  snapshots honouring a `.gitignore`: going back to before a call should not uninstall what was
  installed since. Being outside the worktree is what keeps it out of `list` and out of `git
  status`, where a directory inside would need an exclusion written somewhere no command can write.
- Two tool calls aimed at one file are serialised, so a batch of them cannot lose each other's work.
  A model emits several calls in one response and they run concurrently: two edits to one file each
  read it, each computed against what they read, and the loser's write disappeared while both calls
  reported success. Two `create`s of one path raced the same way, so the promise never to overwrite
  quietly failed. The lock covers the whole read-modify-write, so the second call reads the first
  one's result and an edit whose anchors that invalidated now fails loudly instead of silently.
- `read`, `edit` and `create` reach the scratch directory as well as the repository, so a plan or a
  notes file can be kept across turns and edited by anchor rather than rewritten. A relative path
  still means the repository; anywhere else is reached by naming its absolute path. `list` stays on
  the repository alone, because it answers by asking git and the scratch is deliberately not in git,
  and it says so and points at `bash` rather than failing.
- A console started on a machine with no `bwrap` says so in its log and offers its sessions every
  file tool and no `bash`, rather than refusing to start or running commands unconfined.
- A `list` tool taking a directory and a depth, so finding a file is looking rather than guessing at
  a name. A directory at the depth asked for is summarised with a count instead of opened, so the
  depth bounds the answer. It asks git what is there rather than walking, which means a `.gitignore`
  is obeyed and an installed environment or a build directory never reaches the model, while a file
  the agent itself just wrote does.
- File tools, on sessions that picked a repository: `read`, `edit`, and `create`, bound to that
  session's own worktree and refusing any path outside it. Lines are addressed by a four-letter
  anchor derived from the line's own content rather than by a line number, so an edit elsewhere in
  the file leaves other anchors valid and a line that has changed since it was read is a loud
  refusal instead of a silent edit in the wrong place. Nothing is stored between calls: the anchors
  are recomputed on every read, and where two lines would share one they take in the line above
  until they differ. A read renders each line as its anchor, a box-drawing `│`, then the line, so
  that no part of what the tool is saying can be mistaken for the file's own content, and the tool
  descriptions carry a worked example of the format.
- An `edit` that names a span by its ends, with the field name saying whether each end is inside it
  (`from`/`to`) or outside it (`after`/`before`). One end alone inserts there. Blank lines carry no
  anchor, so an exclusive end is how a span reaches them: deleting a function and the blank lines
  after it names the next code line with `before` and neither names a blank nor retypes that line.
  A `substitute` operation replaces text inside one anchored line, for when retyping a whole line
  of prose to change a word is the wasteful part. Operations are given as a list, resolved against
  one reading of the file and applied together, so they cannot shift each other and a batch whose
  operations overlap is refused entire rather than resolved in an order nobody chose. Every reply
  shows the changed regions with their new anchors, and names any anchor elsewhere in the file that
  changed as a result, so a run of edits needs no re-read between them.
- There is deliberately no tool that overwrites a whole file. `create` refuses a path that already
  exists, so making a file and changing one stay separate operations: a tool that rewrote a file
  wholesale would be the escape hatch from anchored editing, discarding whatever had not been read.
- The repository a session works in, on its row in the sidebar, as `owner/repo` while a forge
  reaches it and the recorded id once none does. It is read out of the session's own `choice` with
  one join rather than held in the index: a checkpoint is a row per key, so this costs one small
  row per session and keeps the index the settled facts it already held.
- A `recorded` disclosure under every settled panel, showing the JSON the checkpoint actually holds
  behind it: the prompt for a person's panel, and the stored parts for every other. Fetched only
  when it is opened, so the transcript a running turn keeps updating does not carry it, and
  preserved across that update so it does not shut under the reader's hand. A panel of the turn in
  flight offers none, because what is behind it is still being written.
- Syntax highlighting on fenced code blocks, in the console's own palette rather than an imported
  theme. Only Pygments' own token classes survive sanitising, so a reply cannot paint itself as any
  part of the console's chrome.
- A new-session page built around the choosing rather than around the box. The endpoints are cards
  naming the API format each speaks and the URL each points at, which is what tells two endpoints
  apart when one gateway answers both formats on one hostname; the models are cards carrying cost,
  context window, output cap, capabilities and release date. Every option is a radio input inside a
  label, so the whole card is the target and the page works with JavaScript off. The choosing runs
  down the top of the page with the message box pinned under it, which is the arrangement a page
  with no conversation on it wants, and it is ordered widest-first - the repository, then the
  endpoint, then the model, then the thinking level, then the name and the message box - so the
  choice that decides what the agent can touch at all is the first thing on the page rather than a
  line under seventy model cards.
- All four questions the picker asks are one component: a group of cards that folds down to the one
  picked, says how many options it has (`27 options`), and can be narrowed by typing. A gateway
  serves seventy models, and a wall of that many cards left everything after it past the end of a
  scroll; shut, the whole of what a session is decided by is four lines and four cards. The fold is
  a checkbox and the folding is a CSS `:has()` rule, so it works with JavaScript off and a shut
  group draws the card whose radio is actually checked rather than a summary that could go stale.
  With scripting on, picking a card folds its group away.
- The repository and the thinking level are cards too, rather than `<select>`s. A native `<option>`
  renders as text in every browser, so a select can carry neither the fold nor the forge a
  repository was reached through - and that forge is the fact telling two rows apart the moment
  repositories come from more than one place.
- Typing narrows a group. Each carries a `<datalist>` of the names in it, so the browser completes
  one with no script at all, and the script hides the cards that do not match. Matching is over a
  card's whole text, so a model answers to its name and to the id the request will actually name.
  Naming one exactly picks it and shuts the group, which is what taking an entry from the
  completion menu does; the match is never on a prefix, so spelling `xhigh` does not stop at
  `high`.
- An optional name for a session, in a field above the message box. Left empty, a session is named
  after its first message exactly as before. A given name goes through the same rule, so there is
  one answer to what a session name is rather than one per way of arriving at one.
- A model reference database, off unless `config.yaml` names one under `model_reference`. No
  gateway reached so far publishes a price anywhere in its model list, and coverage of everything
  else is uneven: an endpoint describes its own vendor's models richly, forwards somebody else's record
  verbatim for the ones it resells, and says nothing at all about the rest. So every fact on a card
  comes from the one database instead, which is what lets two models on a page be compared. Records
  are found by the routed `provider/model` id first and then by the model's canonical name upstream,
  and a name two providers claim resolves to neither, so an ambiguous price is shown as no price
  rather than as somebody's markup. `source` is fetched when it is a URL and read when it is a path,
  so a machine with no outbound access can point at a file. It is read before the console is ready
  and re-read on a timer, and unlike model discovery it can never stop the console starting: a
  database that will not load costs a card its numbers and nothing else. Where one is configured and
  has no record for a model, the card says so; where none is configured, nothing is reported as
  missing, because nothing was asked.
- Configuration is YAML (`config.yaml`) rather than TOML, and the vocabulary it uses is settled.
  What the file declares is an **endpoint**: a `url`, the `format` spoken to it, and a credential.
  The **provider** of a model (`anthropic`, `fireworks`, `xai`) is discovered rather than
  configured and is deliberately not a level of that hierarchy, because the same provider appears
  under more than one endpoint: every Fireworks model on exe.dev's gateway is listed by both of its
  formats under one id. So the shape is `endpoint -> model`, with the provider the heading the
  model cards are grouped under, and `format` means the same thing here as it does under
  `model_reference`.
