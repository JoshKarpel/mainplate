---
description: Make a repository ready for mainplate sessions, with a setup plugin that installs its toolchain and starts its dev server
---
# Getting a repository ready for mainplate

A session starts with a checkout and a read-only system: none of the
repository's toolchain is installed, and the session may have no network to
install it. A **setup plugin** is how a repository fixes that once per session:
a script declared in `.mainplate/mainplate.yaml` that the console runs before
the conversation, with a network, and whose answer can ask for jobs to be kept
running.

This is the commonest plugin a repository wants. For anything a plugin does
after setup (tools, cards, reacting to a turn), read the `plugin` skill as well.

Beside this file, generated from the console's own repository:

- `generated/setup` and `generated/mainplate.yaml` are a working setup plugin
  and its declaration: fetch mise, install the pinned tools, run the
  repository's own setup recipe, and ask for one job.
- `serve/generated/demo` is the job it asks for.
- `plugin/generated/environment.md` is every environment variable a plugin and
  a session's commands see, and `plugin/generated/limits.md` the timeouts.

## What a setup plugin can rely on

- It runs once per session, before the first turn, **in the sandbox, starting
  in the checkout root, with the network**. Every later event has none.
- `$HOME` is the plugin's own scratch directory, which the session's commands
  cannot see. **`$MAINPLATE_SCRATCH` is the session's scratch**, writable at
  this one event, and it is what every later command gets as its `$HOME`. So
  install into `$MAINPLATE_SCRATCH`: setting `HOME="$MAINPLATE_SCRATCH"` at the
  top is the simplest way to make every installer that writes under `$HOME`
  put things where the session will look.
- The system directories are read-only and there is no `sudo`: install
  user-level toolchains (mise, uv, rustup with `CARGO_HOME` and `RUSTUP_HOME`
  under the scratch, a Node version manager), never system packages.
- **`$MAINPLATE_ENV` is a file to append `KEY=value` lines to**, and those
  become the environment of the session's commands and jobs. One variable per
  line; the value is taken exactly as written, with no quoting or expansion,
  and nothing ever sources the file. **A `PATH` written there replaces the
  session's whole `PATH`**, so end it with the system directories
  (`$PATH` at the time you write it already ends with them), or the session
  loses `sh`.
- It reads one JSON payload on stdin, which a setup script can ignore, and
  prints **one JSON answer on stdout**. Printing nothing is a valid answer.
  stderr is logged.
- It must finish within the setup timeout, and exit 0. A failure or a timeout
  fails setup for the session, shows the person what it printed on stderr, and
  pressing the button again runs every plugin again from the top.

## The two rules that trip people up

1. **Nothing but the answer may reach stdout.** Installers print progress, and
   a line of it on stdout is an answer the console cannot read. Start the
   script with `exec 3>&1 1>&2`, which sends everything to stderr and keeps the
   real stdout as fd 3, and print the answer with `>&3` at the end.
2. **It must be safe to run twice**, since a retry runs it again in the same
   scratch. Skip what is already installed (`command -v mise || install`), and
   use installers that are idempotent.

## Asking for jobs

The answer can ask the console to keep commands running, typically the dev
server:

```json
{"jobs": [{"command": ".mainplate/serve", "port": 5173}]}
```

`port` is the one the command listens on inside the sandbox, handed to it as
`$PORT`. Every rule `start_job` describes applies, since this is the same kind
of job: idempotent, on `127.0.0.1`, accepting any `Host`. The `serve` skill is
how to write the command, including restarting it when files change; put it in
a script beside the setup one rather than a long line here.

A job runs with the session's network setting, which may be none. Do anything
that fetches in setup, not in the job.

`instructions` in the answer, a string, is added to what every session on the
repository is told. It is easy to overuse: the repository's `AGENTS.md` already
reaches every harness, so keep `instructions` to what is true only of a session
set up this way (where the toolchain was installed, the job that is running).

## Writing one

1. **Find what the repository needs.** Read the README and any `AGENTS.md` or
   `CONTRIBUTING.md` for the setup and test commands, and the files that pin a
   toolchain: `mise.toml`, `.tool-versions`, `.python-version`,
   `rust-toolchain.toml`, `package.json`'s `engines` and `packageManager`,
   `go.mod`. Prefer running the repository's own setup command (`just setup`,
   `make dev`, `npm ci`, `uv sync`) over restating its steps.
2. **See what the sandbox already has**: `command -v` each tool. Install only
   what is missing.
3. **Write `.mainplate/setup`** in the shape of `generated/setup`: shebang,
   `set -euo pipefail`, `exec 3>&1 1>&2`, `HOME` pointed at the scratch,
   install, the repository's own setup, `PATH` appended to `$MAINPLATE_ENV`,
   the answer on fd 3. Make it executable (`chmod +x`); the console runs the
   file itself.
4. **Declare it** in `.mainplate/mainplate.yaml`, which accepts only `plugins:`:

   ```yaml
   plugins:
     setup: .mainplate/setup
   ```

5. **Try it as the console would**, if the session has a network:

   ```sh
   scratch=$(mktemp -d) own=$(mktemp -d) env=$(mktemp)
   MAINPLATE_SCRATCH=$scratch MAINPLATE_ENV=$env HOME=$own \
     .mainplate/setup </dev/null >answer.json 2>setup.log
   echo "exit $?"; python3 -m json.tool answer.json; cat "$env"
   ```

   Then run it a second time against the same directories, to show it is safe
   to repeat. Without a network, say it is untried rather than claiming it
   works.
6. **Commit both files.** The plugin runs in a new session (or a fork) on the
   repository, once the person presses the button on that session's settings
   step, where it can be switched off; nothing runs a repository's plugin
   before that press, and a session already running keeps what it was set up
   with.
