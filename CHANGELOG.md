# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.1]

### Added

- A chat console over a Pydantic AI agent, where each session is a durable workflow under
  `without-durability`'s SQLite store: the checkpoint *is* the conversation, so a session survives
  a restart and a reply in flight is answered rather than lost.
- `StepwiseDurability`, a Pydantic AI capability that routes an agent's model requests through the
  running session's checkpoint, so a pass that reaches the provider and then dies does not pay for
  that answer twice. It is transparent outside a session, so the same agent stays usable in a
  script or a test.
- `mainplate serve`, which runs the console and the worker that answers its sessions over one
  SQLite file.
- A conversation read as panels of blocks, so reasoning and a tool call each get their own panel
  and their own colour beside the answer they belong to. Messages are rendered as Markdown and
  sanitised before they reach the page.
- A rail beside the conversation: find-and-step search, a key that filters and doubles as the
  colour legend, a dock that jumps between the two sides of the exchange and folds every tool call,
  a follow-the-end toggle, and a light/dark/system theme. All of it is an enhancement; with
  JavaScript off the console still renders, posts, and folds.
- Shift-Enter sends a message and plain Enter breaks the line, which is that way round because a
  message here is prose that often wants a second paragraph and a fenced block. An empty box refuses
  from the keyboard exactly as it refuses from the button.
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
- Git snapshots: a session that picked a repository gets a worktree of its own and each
  turn records the tree it started on. Snapshots go through a shadow index, so nothing a reader can
  see moves - not their staged changes, not `HEAD`, not a branch, not `git log` - and are chained
  under `refs/mainplate/snapshots` so they survive `git gc`. An unchanged worktree writes no new
  object at all. Forking checks the new session's worktree out at the tree the forked turn
  originally saw, so a branch re-asks its question against the files that question was asked about.
  Snapshots are gitignore-aware, so going back to a turn restores what is version-controlled and
  leaves the environment alone.
- The repository a session works in, on its row in the sidebar, as `owner/repo` while a forge
  reaches it and the recorded id once none does. It is read out of the session's own `choice` with
  one join rather than held in the index: a checkpoint is a row per key, so this costs one small
  row per session and keeps the index the settled facts it already held.
- A `recorded` disclosure under every panel, showing the JSON the checkpoint actually holds behind
  it: the prompt for a person's panel, and the stored parts for every other. Fetched only when it
  is opened, so the transcript that swaps once a second does not carry it, and preserved across
  that swap so it does not shut under the reader's hand.
- Syntax highlighting on fenced code blocks, in the console's own palette rather than an imported
  theme. Only Pygments' own token classes survive sanitising, so a reply cannot paint itself as any
  part of the console's chrome.
- A new-session page built around the choosing rather than around the box. The endpoints are cards
  naming the API format each speaks and the URL each points at, which is what tells two endpoints
  apart when one gateway answers both formats on one hostname; the models are cards carrying cost, context
  window, output cap, capabilities and release date. Both are radio inputs inside labels, so the
  whole card is the target and the page works with JavaScript off. The picker fills the middle of
  the page and the message box sits under it, which is the arrangement a page with no conversation
  on it wants.
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
