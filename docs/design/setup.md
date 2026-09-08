# Setting a repository up

How a session gets the repository it works in ready: the script a repository carries for that, where
the console runs it, what crosses back, and the switch in front of it. It is the one program a
repository supplies that the console runs itself rather than through [the plugin
protocol](plugins.md), and this page is why.

## The script is the repository's and the mechanism is the console's

**A repository says how to get itself ready in `.mainplate/setup`**, an executable beside
`mainplate.yaml`, and it says nothing else to this console about it: no manifest, no field, no JSON.
`uv sync` in a shell script is the whole of what most repositories need, and it generalises past
Python without changes, since `npm ci`, `cargo fetch` and `go mod download` all want the same moment
and the same place.

**The console runs it once per session, on the pass that answers the settings step**, behind the
same mount namespace `bash` runs behind, with the network on, starting in the worktree, and with the
session's own scratch as `$HOME`. Every one of those is decided rather than incidental:

- **Once, and on that pass**, because that is where slow work lives and where a person has said which
  programs may run. Nothing runs it before the press, and [a fork](forking.md) plants a fresh
  worktree and runs it again, which is how a session iterates on its own setup script.
- **Behind the namespace**, because it is the repository's code and runs where the repository's code
  runs. The clone is read-only in there exactly as it is for a command, so a setup script cannot
  write a history either.
- **With the network on**, because what a setup does is fetch, and it is safe to offer here for the
  reason it is safe at a plugin's `setup`: the tree holds the commit the repository supplied, nothing
  the model wrote exists yet, and no credential of this console's is inside the namespace.
- **With the session's scratch as `$HOME`**, because that is [what the session's own commands get
  as theirs](sandbox.md#the-scratch-directory). Everything a toolchain fetches lands under `$HOME`,
  so a `$HOME` anywhere else would be a `uv sync` whose interpreter a later `just test` cannot find.
  Measured on this repository's own script, that is on the order of a few gigabytes per session,
  and the disk a session takes is the disk it keeps; tidying sessions is what answers that, and it
  is a line item on a bill the worktree already opened.

## What crosses back is a file

**The script is handed `$MAINPLATE_ENV`, the path of a file it appends `KEY=value` lines to, and
those lines are what the session's commands are then run under.** That is GitHub Actions'
`GITHUB_ENV`, and it is the right shape for two reasons that are worth keeping apart:

- **It is an allowlist by construction.** Only what the script writes crosses, so whatever else the
  setup environment held stays where it was. There is no filter to maintain, and no way for a
  variable the script did not name to reach a command.
- **It keeps the console language-agnostic.** The script writes its own `PATH` line with a shims
  directory on the front, and the console never learns what a shim is, where mise keeps them, or
  that Python has an interpreter directory. Every tool-specific fact stays in the repository.

**Set whole, after everything the sandbox sets itself.** A `PATH` the script recorded is the `PATH`,
not a fragment spliced into one by a rule somebody has to learn. The cost, stated: a script that
leaves `/usr/bin` off breaks its own session's commands, and the fork is where that is corrected.

**It reaches the session's commands and nothing else.** A plugin's own namespace gets none of it,
and that is the constraint rather than an omission: a repository's script setting `PATH` for every
plugin would redirect what its own `.mainplate/pre-commit` executes whenever the model tries to stop, which is
the hazard [a plugin's private scratch](plugins.md#a-scratch-of-its-own-which-is-not-the-sessions)
exists to close, arriving through a new door.

**Recorded, under `setup:environment`, beside the two registrations and under the same rule.**
Nothing is recorded until every setup has answered, so a session past its step always has an answer
there, empty where the script was not run. The parent reads that file as a *value* and never as a
program, which is [the line the security page draws](security.md).

## It has a switch of its own

**The settings step draws it under the repository's tier, beside the repository's plugins, with a
switch like theirs.** The declaring pass records whether the tree carries a `.mainplate/setup` on the
repository's own declaration, so what the step offers and what the pass may run are one reading of
the tree, and nothing that draws a switch has run anything.

It is the one switch on the step that is not a plugin's, and it lives in the same `enabled` column
under the key `setup`. That cannot collide with a plugin's, because a plugin's key is a *qualified*
name and every qualified name holds a colon. The same column rather than a second one because it is
the same question, answered on the same step, by the same press, and carried across a fork the same
way: a branch draws the step with its parent's answer as the default.

**The tier's heading counts it.** A heading that turned the repository's tier off and left its setup
running would be a switch that lies.

**Off, the session still opens.** The script is not run, the environment is recorded empty, and the
session's commands run exactly as they would in a repository carrying no script. That is what makes
the switch worth having rather than the trust switch alone: a session on a repository somebody is
reading rather than working in wants its hooks and its setup off and its guidance on, and the trust
switch says no to all of it at once.

**Failing, the session comes back to the step.** A script that exits non-zero, runs past the twenty
minutes a setup is allowed, or writes a line to the environment file that is not `KEY=value` fails
the setup loudly, with the last lines of what it printed above the switches. There is no quiet
version: a session that opened over a repository whose dependencies never arrived would find out one
command at a time. Pressing again is a fresh attempt, and turning the switch off is a line away.

## Why it is not a plugin

**It was built as a bundled plugin first, and that shape needed five exceptions for one name.** A
plugin running a repository's script has to be confined like a repository's where every other
bundled plugin runs unconfined; it needs the *session's* scratch as `$HOME` where every other
confined plugin gets one of its own; it has to be left off sessions with no worktree, where the
bundled set is otherwise declared for every session; it has to register under the console's key by
tier while being confined, where the two had been one boolean; and the console has to know it by
name to do any of that. Each exception was small and each was a rule somebody would have to learn.

What the console owns instead is one function, `prepared`, that runs a script in the session's own
namespace and hands back what it wrote. The protocol stays closed, [the trust switch](
plugins.md#trusting-a-repositorys-plugin) still governs whether the script is read at all, and the
settings step still stands in front of running it. What was given up is the idea that everything a
repository asks this console to run is a plugin; what was kept is that everything a repository asks
this console to run is confined, switched, and recorded.

**The line between this and a plugin is who executes.** A plugin is spoken to and answers; this is
run and leaves a file. A repository that wants a program run during the conversation writes a plugin.
A repository that wants its dependencies fetched once writes a script, and it is the more common
want by far.

## This repository's own

`.mainplate/setup` here installs mise into the session's scratch, runs `mise install` for the tools
`mise.toml` pins, and runs `just dependencies` under them, which is `just setup` without the line
that installs a git hook. That line is split out because git's hooks live in the clone's *common*
directory, shared by every worktree of it and bound read-only in a session, so installing one from
a session is both refused and wrong. It is a person's to run, once per clone, from [the composer's
`Run`](composer.md#run).

The `PATH` it writes back puts mise's shims first, so a session's `uv`, `just` and `node` are the
pinned ones, then mise's own directory, then the system's. Nothing in the console knows any of that.
