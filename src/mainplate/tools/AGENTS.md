---
description: What must hold of a tool package, and which tools a session gets.
---

# The tools

Why any of this is shaped the way it is is [How a model reaches a
file](https://joshkarpel.github.io/mainplate/design/tools/). What follows is what must hold while
editing here.

## The package boundary

One package per tool, as `tools/{name}/{module}.py`. **Only the constructor reaches the harness**:
`tools/__init__.py` exports the constructors and the values they take and nothing else, so
`agent.py` can ask for the tools a workspace affords without knowing that editing is anchored, that
a checkout root has to be resolved against, or how a command is confined.

A further tool is a new package beside `files/` and `bash/` and one more name in that list. It is not
an edit to anything that already imports them.

The one reader outside the harness that reaches past the constructors is `calls.py`, the page's
rendering of a call, which imports the anchor scheme's constants to tell a file's lines from the
tool's own and `DIFF` to find an edit's diff. That is the page knowing the shape of what a tool
prints, which it has to; it is not `agent.py` knowing it, and nothing else should come to.

## `edit` hands back a `ToolReturn`, and the loop splits it

`Files.edit` returns an `Edited`, the reply and a unified diff of the change, and the tool function
wraps them as Pydantic AI's `ToolReturn`: the reply as `return_value`, the diff under `metadata`
as `{DIFF: ...}`. `Stepping.call` in `durability.py` is what unwraps that into the record's
`returned` and `metadata`, and it refuses a `ToolReturn` carrying `content` or `tools`, so a tool
here may use those two fields and no other. The model is never sent the metadata; the page reads
it. See [every tool that writes hands back
anchors](../../../docs/design/tools.md#every-tool-that-writes-hands-back-anchors).

**A tool that acts on the conversation rather than on the machine does not belong here at all**: it
belongs in a [plugin](../plugins/AGENTS.md), which is where `hand_off` lives. What is left in this
package is the tools whose subject is a file or a command, which is what makes the table below the
whole of the rule.

## Which tools a session gets

Read off `Choice.isolation`, not off whether the session picked a repository:

| Filesystem | Over its files | `bash` |
|---|---|---|
| `CHECKOUT` | `read`, `edit`, `create` over the checkout and scratch; `list`, `grep` over the checkout | where there is a sandbox |
| `EVERYTHING` | `read`, `edit`, `create` over `/`, where there is a sandbox; no `list` or `grep` | where there is a sandbox |
| `NOTHING` | `read`, `edit`, `create` over the scratch, where there is a sandbox; no `list` or `grep` | where there is a sandbox |

`NOTHING` gets its file tools only beside `bash`, and nothing at all without one, because the scratch
is made by the first command and a tool that cannot work still costs its description on every
request. `list` and `grep` are offered only where a root is a checkout, `Files.has_repository`, for
the same reason: both ask git, and over a scratch or `/` either could only refuse. `reaching` in
`agent.py` decides the roots, `agent_for` builds the tools from them, and `tests/test_agent.py` holds
every row of that table against the agent a pass builds.

**A plugin's tools are outside that table**, and a session with `NOTHING` still gets them: what a
plugin reaches is decided by its own tier rather than by what the *model* may touch. They are settled
at `describe` and never added mid-conversation, because a tool definition sits above the cached
prefix and introducing one late invalidates the whole conversation beneath it.

**There is no git tool.** A checkout owns its `.git`, so git in `bash` does everything a session
needs, confined like every other command; a tool wrapping a subset of it would be a second git
surface to keep safe for nothing the shell does not already do.

## `list` runs git, and only in the sandbox

`GitTracked.entries` runs `git ls-files` over the checkout, which is the one directory a session may
write, `.git` included. `ls-files` refreshes the index, so a call in this process would run whatever
the checkout's configuration named. See [`docs/design/security.md`](../../../docs/design/security.md).

**So it holds a `Checkout` and goes through `Checkout.git`**, which runs git behind `bwrap`. Do not
build a git subprocess here, or anywhere in the parent, against a checkout path: it would read the
session's configuration with this process's authority, and nothing at the call site would say so.
What `entries` needs beyond the default is `at=` and `Ran.stdout`, and anything else should be one
more argument there rather than a subprocess of its own.

## `grep` reads what `edit` can address

`grep` asks the same `GitTracked.entries` as `list`, then reads matching text in the parent and
renders it through `Anchored` computed over each whole file. Keep all three parts: asking git keeps
ignored trees out, whole-file anchors are the names `edit` actually resolves, and the parent-side
read means it shares `Files`' path checks and per-file locks rather than trusting a command's output.

The `Files` value passed to `file_tools` and `grep_tools` must be the same value. Two values with the
same roots carry different lock maps, so an edit could otherwise write while grep was reading. Search
results may be stale after the lock is released, and that is safe: `edit` resolves the returned
anchor against the current file and refuses one that moved.

`grep` is repository-only, like `list`. The scratch and the whole-machine root already have `bash`,
and walking either in the parent would add a second, unbounded directory traversal to answer a
question the shell already answers.

## These tools do not pass through the sandbox

`read`, `grep`, `edit` and `create` access files from the parent, so **no bind protects anything
from them**. `GitTracked.sealed` names `.git` and `Files.resolved` refuses it and everything under
it, so an `edit` never rewrites a ref or a config line underneath git's own locking. That is about
correctness rather than reach: `bash` runs git against the same directory, so the same bytes are one
`git config` away, and nothing here relies on the refusal to keep a program from running.

`sealed` is a property on each root arm, beside `name`, so a new kind of place brings its own answer
rather than needing an entry in `resolved`. Keep the refusal to a root's *top level*: a `.gitignore`,
a `.github/`, and a fixture with a nested `.git` are ordinary files.

## Two things not to undo

- **`list` asks git, and keeps doing so once `bash` exists.** It is not a listing convenience that
  `git ls-files` in a shell replaces: it builds a tree, opens it only to `depth`, summarises past
  that with a count, and caps at `MAX_ROWS`. That is the difference between orienting in a large
  repository for hundreds of tokens and for tens of thousands.
- **Everything a tool turns down is a `ModelRetry`**, never a fault that ends the turn, because all
  of it is correctable from the message. It reaches the model as the call's failed result, recorded
  under the call's key like a return, and nothing counts how many times: a refusal is an answer, not
  a strike.
