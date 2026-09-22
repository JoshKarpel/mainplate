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
a worktree root has to be resolved against, or how a command is confined.

A further tool is a new package beside `files/` and `bash/` and one more name in that list. It is not
an edit to anything that already imports them.

**A tool that acts on the conversation rather than on the machine does not belong here at all**: it
belongs in a [plugin](../plugins/AGENTS.md), which is where `hand_off` lives. What is left in this
package is the tools whose subject is a file or a command, which is what makes the table below the
whole of the rule.

## Which tools a session gets

Read off `Choice.isolation`, not off whether the session picked a repository:

| Filesystem | Over its files | `bash` |
|---|---|---|
| `WORKTREE` | `read`, `edit`, `create` over the worktree and scratch; `list`, `grep` over the worktree | where there is a sandbox |
| `EVERYTHING` | `read`, `edit`, `create` over `/`; `list` and `grep` refuse | where there is a sandbox |
| `NOTHING` | `read`, `edit`, `create` over the scratch, where there is a sandbox; `list` and `grep` refuse | where there is a sandbox |

`NOTHING` gets its file tools only beside `bash`, and nothing at all without one, because the scratch
is made by the first command and a tool that cannot work still costs its description on every
request. `reaching` in `agent.py` is the one place that table is decided, and `tests/test_agent.py`
holds every arm of it.

**A plugin's tools are outside that table**, and a session with `NOTHING` still gets them: what a
plugin reaches is decided by its own tier rather than by what the *model* may touch. They are settled
at `describe` and never added mid-conversation, because a tool definition sits above the cached
prefix and introducing one late invalidates the whole conversation beneath it.

## `list` runs a program in the parent

`GitTracked.entries` runs `git ls-files` in this process, over the worktree, which is the one
directory a session may write. `ls-files` refreshes the index, so a call that let git find its own
directory would run whatever the tree's configuration named. See
[`docs/design/security.md`](../../../docs/design/security.md).

**So it holds a `Worktree` and goes through `Worktree.git`**, which is the only place that knows how
to run git safely: named git directory, built environment. Do not build a git subprocess here out of
`addressed` and `environment`, which is a second copy of that answer and drifts silently. What
`entries` needs beyond the default is `at=` and `Ran.stdout`, and anything else should be one more
argument there rather than a subprocess of its own.

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
from them**. The sandbox binds the worktree's `.git` read-only and that stops `bash` replacing the
pointer; it does nothing here, which is why `GitTracked.sealed` names `.git` and `Files.resolved`
refuses it. The two are one decision in two places because the two paths are genuinely different,
not a check written twice: remove either and the vector is open again through the other.

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
