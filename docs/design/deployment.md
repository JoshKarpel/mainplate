# Running it as a service

`mainplate install` renders a user systemd unit naming this checkout and converges it;
`mainplate uninstall` removes it, keeping the environment file and the database. `install.py` is
where both live.

## The unit

Three things there are load-bearing and easy to undo by accident:

- **The unit names `sys.executable`, never `uv run`.** A `uv run` process holds a shared lock on the
  uv cache for its whole lifetime, so a service started that way blocks `uv cache prune` on the
  machine for as long as it is up, and `--no-cache` is not an escape for a package with
  dependencies. `sys.executable` is not resolved either: a venv's `bin/python` is a symlink to a
  base interpreter that has none of the venv's packages.
- **The restart is unconditional, the `daemon-reload` is not.** An upgrade in place renders
  identical unit text, so gating the restart on a difference would report success while leaving the
  old code serving.
- **The environment file is written once and never overwritten.** It holds the credential, so an
  install that rewrote it would delete the key on every upgrade.

`just install` runs `uv sync` first, and that is not a convenience: the unit names this checkout's
interpreter, so an install from a stale environment points systemd at a venv missing whatever was
just added.

## No unit hardening, and the sandbox is the reason

There is no `Protect*`/`ReadWritePaths` block, deliberately, and that is not an exception to
[the sandbox](sandbox.md) but a consequence of it. The agent edits repositories, so the paths it
legitimately writes are the worktree root and everything under it, which is exactly what a
`ReadWritePaths` would have to name: a unit sandbox loose enough to permit the worktree protects
nothing.

The boundaries that actually hold are both *inside* the process and per session rather than per
service, which is what a unit setting can never be: `Files.resolved` for the file tools, and a mount
namespace for `bash`.

That is also why the service is not itself confined. It holds the credential, the store, and every
session's worktree, all of which it needs, so the useful boundary is the one around what a model
asked for and not the one around the console. What that leaves guarding it is who can reach the
console, which is [what a command the person runs](composer.md#run) already rests on.

## Ports

`just serve` and `just install` are on different ports on purpose, so a foreground run for a quick
look never takes down the service. `just demo` is the same foreground console on a database of its
own, for poking at a page without touching real sessions.
