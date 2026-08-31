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
- Profiles in `config.toml`: an endpoint and a credential, with the model chosen separately. Each
  session records the pair it was created on and is answered on it for life, so changing what is
  configured leaves existing conversations readable. Credentials are read from the `0600` file and
  handed to the SDK, so they never enter the process environment.
- exe.dev support: on a VM with the built-in LLM integration, `mainplate install` discovers it
  through the reflection integration and writes a keyless profile, so the box holds no credential
  at all.
