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
