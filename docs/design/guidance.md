# What a session is told

The words a session is answered under, and there are two scopes. **Console guidance** is the
operator's own, every `.md` file under `<config home>/mainplate/guidance/`, sorted by path and
concatenated. **Repository guidance** is the project's own `AGENTS.md`, read out of the worktree the
session works in. Both go into the agent's `instructions`.

**All of it is a [plugin](plugins.md)**, in `src/mainplate/plugins/bundled/guidance`, and it is the
half of the protocol handoff does not exercise: `instructions` contributed at `setup`, and an
`inject` at `before_request`. So what follows describes a script this console speaks to over a pipe,
and everything below is that script's rather than the console's unless it says otherwise - what the
console keeps is `instructing`, which is the one join that puts the operator's standing instructions,
every running plugin's contribution and the note about this session's tools in that order.

**Which means every word of it can be replaced.** Install your own `guidance` beside the bundled one
and turn ours off with one switch on the settings step, or run both and let them sit alongside each
other, which is the coherent reading of two guidance loaders in one session.

**Nothing in it imports `mainplate`.** A bundled plugin runs on exactly the path a third-party
plugin runs on, so what ships is a worked example rather than a privileged one. The price is stated:
the frontmatter reader is a `description:` line rather than a YAML parser, because a plugin with no
dependencies is worth more here than the general case of a field nothing else reads.

**The repository is last, so it wins.** That is not a claim about trust: a repository is right about
itself, which is the local-conventions rule one layer out, and it is why escapement puts the gains
in the repository rather than in the server. What the console holds is how to work; what the
repository holds is what this project is.

**`AGENTS.md`, and a name this console invented for nobody.** A `.mainplate/` directory would be
knowledge only mainplate can read, and the whole reason to write a line in the repository rather
than in a prompt is that it survives the tool being turned off. `CLAUDE.md` is the fallback where
there is no `AGENTS.md`, and only the fallback: a repository carrying both is carrying one set of
instructions twice, and pi reads the pair and warns about exactly that in its own docs.

## Instructions, not a message

**Which is a decision about caching before it is one about placement.** Pydantic AI's `instructions`
are a per-request parameter it re-renders on every request, never a message at a position, so they
sit in front of the cached prefix. What that buys is that guidance is present on every request
without being appended anywhere, and what it costs is that changing it invalidates the cache from
the system block onward. Anything that arrives *mid* conversation therefore cannot go here: it would
re-price the whole conversation, and it belongs appended as a `SystemPromptPart`, which is one more
entry at the end and leaves the cached prefix the top-level `system` parameter sits in untouched.
That is what [the directory-scoped guidance below](#guidance-elsewhere-in-the-repository) does.

**Composed once per stretch of context, and recorded**, under `instructions:{n}` where `n` is the
turn that stretch began at. Instructions sit in front of the cached prefix, so composing them again
on a later turn re-prices every remaining request the moment anything under them has moved, and a
session working on a repository's own `AGENTS.md` moves it constantly. Re-reading buys nothing
against that, because the thing most likely to have edited the file is the model, and it knows what
it wrote.

**A stretch of context and not a whole session, because a forget ends one.** That is a
weaker-sounding promise that costs nothing: what recomposing spends is the requests that would have
read the prefix from cache, and a forget has just thrown the whole prefix away. Composing again
exactly there is therefore free.

**What it recomposes is the join and not the guidance**, and that is the one thing the port to a
plugin took away. A plugin is set up once per session, so what it contributed is settled from the
moment its [settings step](plugins.md#starting-a-session-takes-four-steps) was answered; a forget
composes the stretch's own `instructions:{n}` again and every block in it says the same thing. So an edited `AGENTS.md` is picked up by [forking](forking.md) rather than by
forgetting, which is the answer this console gives to every other question about a session's terms.

`history_began` asks the same `forgets` predicate `reached` clears history on, so the two cannot
disagree about where a context starts.

Five details there are decided:

- **It is a `Run.step`**, so the first pass composes and every later one replays. That also means it
  cannot be composed twice under one key in a pass, which is why a pass answering two turns of one
  stretch memoises what it composed and a pass crossing a forget composes a second time.
- **It records exactly what the model is sent**, every running plugin's contribution and the notes
  about this session's worktree and network included, and `agent_for` speaks it verbatim. Composed out there instead, those notes would be a
  sentence the model carried that no record held, so a page could report only what a *turn's*
  messages held, which is nothing until a turn has landed, and they would be recomposed on every
  turn from live state, in front of a cached prefix they are supposed to sit still behind. The
  reason they cannot simply be appended in `agent_for` on top of a record that already holds them is
  the replay: a second pass hands it back the recorded string and would get them twice.
- **What decides the note and what decides the toolset is one function.** They are read off one
  `Choice.isolation` and one worktree, and `agent.reaching` answers both at once, because in two
  `match` statements they would be two places to keep in step over one answer and the failure would
  be quiet: a session told it has a scratch directory whose tools cannot reach one.
- **The key is not turn-prefixed**, deliberately. `before` copies turn-prefixed keys by shape, so a
  turn-shaped name would carry a parent's instructions into a fork that may have attached a
  repository the parent never had. Named this way a fork composes its own.
- **`working_note` names a session's places and never paths them**, which is `roots.py`'s whole
  argument said one layer out and a fact about the cache besides. A worktree sits under 32 hex
  characters of session id, so printing the path invites the failure the root names were built to
  prevent; and instructions are the per-request parameter Pydantic AI renders in front of the entire
  cached prefix, so a sentence naming one session's directories makes that session's prefix unlike
  every other's. With the paths out, the note is a pure function of the isolation: two sessions of
  the same shape compose byte-identical instructions, and a fork's first request reads its parent's
  prefix from cache rather than paying full price for the whole conversation again. A relative path
  already lands in the worktree and `$MAINPLATE_WORKTREE` already names it in a command, so nothing
  was given up. `test_what_a_stretch_records_is_exactly_what_its_requests_carried` asserts the path
  is absent beside its control that the note is present, so an emptied note cannot pass it.

**Console guidance is read once at startup instead, and nothing watches it.** `just serve` watches
`src/mainplate`, and this lives under the config home, so an edit there wants the process restarted
by hand rather than arriving on the next save. That is the ordinary reloadable-configuration answer
of "restart to apply", said out loud rather than left to be discovered: the failure it prevents is
editing a rule, seeing the dev server reload for an unrelated reason, and believing the new rule is
in force.

**Frontmatter is taken off.** It is addressed to whatever loads the file rather than to the model,
so passing a `paths:` list on spends a context window on it and invites an answer about it. Only a
block that opens the file and closes counts, so a document whose first line is a rule of dashes is
left as written.

## The system prompt is drawn as a panel

Under the rule that opens the stretch it belongs to, folded, as the Markdown it is. A console that
shows what a model answered and hides what it was told is showing half of how a turn happened. Five
things there are decided:

- **It is read out of `instructions:{n}` and never out of a turn.** That record is written before
  the stretch's first request, where a turn's messages do not exist until it ends, so the panel is
  on the page while a turn is being answered rather than only afterwards. What makes the record
  worth trusting for this is that `agent_for` speaks it verbatim: nothing is composed on top of it,
  so what a stretch records and what its requests carried are one string.
  `test_what_a_stretch_records_is_exactly_what_its_requests_carried` holds the two ends against each
  other, with the worktree note as the control, since that is the part that used to be added after
  the record was written.
- **One per stretch, under its own rule.** A forget composes again, so a single panel above
  everything would stand the newest instructions over turns answered under an older one. Under the
  rule the reader gets the order it happened in: the boundary, then what the model is told from
  here, then the message.
- **It is not a `Panel`.** A panel's identity is its turn and its position, and this belongs to the
  first but not the second; giving it one would have taken `#N.0` off the person's opening message,
  which the fork link and every permalink already point at. `waiting_panel` is the same split, and
  `Transcript.system_prompts` is where it rides instead. It is `system-prompt` in `data-kind`, in
  the anchor and in the class, and `system prompt` on the page, because a reader who reaches for
  `#told` is reaching for a word this console prints nowhere.
- **A stretch nothing has composed for yet carries the working dots on the panel's own row**, in the
  opening line's place, with the panel drawn shut. Composing reads a repository the pass is the one
  to fetch: a session's first message is on the page before there is anything to put under it, and
  on a fresh clone that gap is minutes. Drawn rather than left out, so what is coming is visible
  from the moment the message is; on the row rather than inside an open panel, because a panel
  opened to show three dots is a row spent on three dots, and because a fold whose default *moves*
  is one the console cannot draw either way once a morph has recorded the state it delivered.
  `instructed_in` is what keeps it off a stretch nothing will ever compose for, which is every turn
  answered before this console recorded instructions at all. Those draw no panel, which is a loss
  taken knowingly against a spinner that would never resolve. `opening.html` in the gallery is that
  state to look at.
- **Rendered as the Markdown it is, and that costs nothing about what was sent.** What is in the
  panel is `.md` files concatenated, the operator's guidance and the repository's `AGENTS.md`, so
  its headings, lists and fences are the structure their authors wrote, and a wall of `##` is the
  one reading of it nobody meant. The claim that this is what was *sent* is kept by the source
  riding along in `data-markdown`, which is what the copy button hands back, and by the raw record
  on the rule one step further out. The block is deliberately uncapped and does not scroll: a reader
  who opened the panel asked for all of it, and a box that scrolls has no still corner for the copy
  button to pin to.

    **It is a `.block` and not bare prose**, which is what puts a copy button on the panel: the
    script seats one against a panel's blocks, and the whole prompt is what somebody reaches for.

    **It carries no fold of its own; the panel is its fold.** It is drawn as the same
    `block--document` the guidance a turn is handed mid-way is drawn as, because on the page the two
    are the same thing, and what separates them is where each sits in the request, which is what the
    *panel* around each says. The document's opening line is on the panel's row, which is what
    identifies it without opening it. See [every panel folds, from its own
    row](console.md#every-panel-folds-from-its-own-row).

The panel takes the person's hue, by the same rule as `command`: the axis is who produced the text,
and what is in a system prompt was written by the operator and by whoever wrote the repository's
`AGENTS.md`. The console composed it; it did not write it.

**A message's newlines are the author's and a document's are its wrapping**, which is the whole
difference between `markup.py`'s two converters. A chat box promises that a newline is a newline,
because Markdown's own rule, that a line break needs two trailing spaces, is a rule about
*documents* that nobody typing a message knows. A guidance file is a document, soft-wrapped at
whatever width its author's editor uses, so `nl2br` there draws one paragraph as a column of ragged
lines saying nothing about how it was written. `as_message` and `as_document` are the pair, and
`written(text, document=...)` is where a caller says which it has. Everything else about the two is
one list, the extensions and the sanitiser included, because the *safety* of this does not depend on
where the text came from: a guidance file is written by whoever wrote the repository, which is the
same trust as whatever reached the message box. `test_markup.py` asks every sanitiser question of
both.

## Guidance elsewhere in the repository

A monorepo puts rules beside the part they are about, and loading all of them would swamp a context
window with instructions about apps this session will never touch. **Load-time scoping cannot help
here**, and that is worth knowing before reaching for it: "does this repository have a web app" is
always yes, where the question is whether *this session* is working on it, which nothing resolvable
at load time can answer. So nesting is not a scoping mechanism this console implements. It is a
placement convention repositories already have, and two things carry it.

**An index, in the instructions, on every request.** One row per nested guidance file: its path, and
a `description` from its own frontmatter where it has one. That `apps/web` has conventions is one
line and what they are is a page, so the line rides in the prefix and the page is read when it is
wanted. It also serves the goal path scoping never did, which is knowing a part of the repository
*has* rules before reaching in and breaking them. Walked with the noisy directories pruned rather
than asked of git: a plugin runs in a namespace with git reachable, but the walk is what makes this
work in a directory that is not a checkout at all, and the pruning is what keeps it affordable, since
the directories that make a walk expensive are the ones a guidance file is never in.

**And the file itself, handed over on approach.** The plugin reads which paths the model has named to
a file tool and asks for the guidance covering them to be injected, from the root down. Six things
there are decided:

- **It is delivered at `before_request`, not attached to a tool return.** A batch of calls and
  the reply to them are one exchange, so there is no earlier moment: a tool return and the next
  request arrive together. Delivered here it needs no change to `Files` at all, it is the console's
  own voice rather than text smuggled into a tool's output, and it is where a tree diff would go if
  the `bash` hole ever needs closing.
- **The history is the ledger**, which is why the whole message list is in the payload rather than a
  summary of it. The plugin asks whether the block is already in what the model will be handed, and
  that one question answers every case: it delivered it, the model read the file itself off the
  index, the model *wrote* the file, a fork carried it across in the copied prefix, or a `forget`
  dropped it and it is delivered again. A set kept on the session would be wrong rather than merely
  redundant, since it would survive a boundary and leave the model working without guidance it can no
  longer see - and a set kept in the plugin cannot exist at all, because a plugin is a process per
  event.
- **What was injected is recorded**, under `turn:{n}:injected:{i}`, and a resumed pass replays it.
  `guiding` was safe unrecorded by being a pure function of the history it was handed; a plugin is
  somebody else's program and cannot be trusted to be pure, so the same point offered to a plugin
  writes down what it said. Without that a resumed pass could put a different sentence in front of a
  recorded answer, which is the one disagreement between two passes this whole mechanism exists not
  to have.
- **A `SystemPromptPart` rather than a `UserPromptPart`.** Nobody typed it, so `interjected` tells
  the two apart by which part carried them and the transcript draws guidance as its own kind rather
  than as the person having said it. It is also what leaves the cached prefix alone, and *that* is
  the whole reason this is appended rather than added to the instructions: appended it is one more
  entry at the end, where an instruction re-prices every request from the system block onward.

    **How it reaches the model is the provider's business, and it varies more than is comfortable.**
    Pydantic AI's `prepare_messages` renders a non-leading system part as a real `{"role":
    "system"}` entry only where the profile sets `supports_inline_system_prompts`: always on the
    OpenAI wire, and on Anthropic only for the four models in
    `_INLINE_SYSTEM_PROMPT_MODEL_PREFIXES` (`claude-fable-5`, `claude-mythos-5`, `claude-opus-4-8`,
    `claude-opus-5`). Everywhere else, `claude-sonnet-4-6` and `claude-sonnet-5` included, which is
    most of what sessions here run on, it is rewritten as a `<system>`-tagged `UserPromptPart`.
    Nothing in this console may depend on which, and the caching argument above is the one that
    holds either way.

- **It is injected as `Guiding`**, symmetric with `Pricer` and `Draining` and for the same cycle:
  the capability stays ignorant of what a guidance file is and one instance still serves every
  session.
- **`bash` reaches nothing here, and that is stated rather than left to be found.** Its argv is the
  model's, so a path inside it is a string this console has no business parsing. That is the hole
  the index covers, and a diff between consecutive `turn:{n}:tree:{i}` would close it for writes:
  the snapshots are already taken, so it is available whenever it earns its keep.
- **The delivered block renders**, as a `guidance` panel at the position it was delivered. A console
  that shows what a model answered and hides what it was handed is showing half of how a turn
  happened, and this is the half that arrives mid-turn. Drawn as the Markdown it is, by the standing
  prompt's own argument one panel up: it is an `AGENTS.md` with a line of the console's own in front
  of it, drawn as the same `block--document`, in a panel drawn shut and named on its row by the line
  it opens with.

    **`guidance` and not `system-prompt`, and the line between them is mechanical rather than
    editorial.** A system prompt is `instructions`, a per-request parameter re-rendered on every
    request in front of the cached prefix; this is a `SystemPromptPart` appended into the history at
    a position. Two mechanisms, two places in the request, two things a reader may want to quiet
    apart in the key, so two words, by the rule that keeps `steer` apart from `prompt`. What they
    share is the shape on the page, which is why they share a block and not a kind.

    **The two are not weighted alike by the model either, and that is an argument for the split
    rather than against it, but it is the provider's answer and not ours.** Pydantic AI measured it
    and recorded the result in `_INLINE_SYSTEM_PROMPT_MODEL_PREFIXES`: on `claude-opus-5` an inline
    system entry carries enough authority to lift a restriction the top-level prompt set, every
    time, where `claude-sonnet-5` accepts the same entry with a 200 and ignores it, so Sonnet gets
    the `<system>`-tagged user text instead, on which a plain formatting instruction actually lands
    more often. So the same guidance is above the standing prompt on one model and in the user's
    voice on another. Write nothing here that turns on it; what this console owns is where each one
    goes, and the caching consequence of that is the same everywhere.
