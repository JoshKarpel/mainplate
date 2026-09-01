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
- `mainplate install` and `mainplate uninstall`, which converge and remove a user systemd unit
  pointing at the interpreter that ran them. Any `MAINPLATE_*` setting lives in an
  `EnvironmentFile` created `0600` on the first install and never overwritten.
- Profiles in `config.toml`: an endpoint, the wire spoken to it (`anthropic` or `openai`), and a
  credential. Each session records the profile and model it was created on and is answered on them
  for life, so changing what is configured leaves existing conversations readable. Credentials are
  read from the `0600` file and handed to the SDK, so they never enter the process environment.
- Model discovery: no models are configured anywhere. Each endpoint's own model-list API is asked
  what it serves, once before the console takes traffic and then on a timer, and the picker offers
  whatever comes back, grouped by the vendor each model comes from. A refresh that fails keeps the
  models discovered earlier; a first read that fails is a startup failure naming the profile.
  `default_model` names which one a new session starts on, defaulting to whatever the endpoint
  listed first. What the picker shows is what an endpoint *advertises*, which is narrower than what
  it will route, so an existing session on a model that never appears in the list is still
  answered: only a missing profile stops one.
- exe.dev support: on a VM with the built-in LLM integration, `mainplate install` discovers it
  through the reflection integration and writes keyless profiles, so the box holds no credential
  at all. One gateway gets one profile per wire, which between them reach Anthropic, OpenAI,
  Fireworks, and xAI: around seventy models with nothing configured.
- A thinking level on every session, chosen beside the profile and the model and fixed with them
  for its life. Eight values, because saying nothing about thinking, asking for it to be off, and
  asking for it at the provider's own budget are three different requests rather than gradations of
  one. The effort names come from Pydantic AI's own type, so a level it adds reaches the picker
  without a change here. `default_thinking` names the one a new session starts on.
- Forking: any turn can be branched into a new session that carries the turns before it, on a
  different model, a different profile, or a different thinking level. A fork is a *copy* of an
  immutable prefix rather than a pointer into its parent, so each session's checkpoint stays the
  whole of its own conversation and neither can change what the other reads. The branch point is
  before the forked turn's message, which comes across editable and is asked again on the new
  model, so seeing a turn answered differently never means retyping the question. The sidebar draws
  the resulting tree, each fork under what it came from and labelled with the turn it left at.
- A repository picker on the new-chat page, alongside the profile, model and thinking level. Where
  the repositories come from is an interface (`forge.py`) with one implementation: on an exe.dev VM,
  `ExeDevGitHub` offers whatever GitHub integrations are attached, which needs no credential at all
  because exe.dev injects one at its own edge. Anywhere else no forge reaches anything, the picker
  does not appear, and the console is what it was before: a place to talk. A session may also choose
  no repository, and a fork inherits its parent's rather than being offered another.
- Git snapshots: a session that picked a repository gets a worktree of its own and each
  turn records the tree it started on. Snapshots go through a shadow index, so nothing a reader can
  see moves - not their staged changes, not `HEAD`, not a branch, not `git log` - and are chained
  under `refs/mainplate/snapshots` so they survive `git gc`. An unchanged worktree writes no new
  object at all. Forking checks the new session's worktree out at the tree the forked turn
  originally saw, so a branch re-asks its question against the files that question was asked about.
  Snapshots are gitignore-aware, so going back to a turn restores what is version-controlled and
  leaves the environment alone.
- Syntax highlighting on fenced code blocks, in the console's own palette rather than an imported
  theme. Only Pygments' own token classes survive sanitising, so a reply cannot paint itself as any
  part of the console's chrome.
