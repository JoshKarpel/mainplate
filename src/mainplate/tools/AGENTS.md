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
| `WORKTREE` | `list`, `read`, `edit`, `create` over the worktree and the scratch | where there is a sandbox |
| `EVERYTHING` | the same four over `/`, and `list` refuses since nothing there is in git | where there is a sandbox |
| `NOTHING` | none of them | no |

`NOTHING` gets none rather than four that can only fail, because a tool that cannot work still costs
its description on every request.

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

## Two things not to undo

- **`list` asks git, and keeps doing so once `bash` exists.** It is not a listing convenience that
  `git ls-files` in a shell replaces: it builds a tree, opens it only to `depth`, summarises past
  that with a count, and caps at `MAX_ROWS`. That is the difference between orienting in a large
  repository for hundreds of tokens and for tens of thousands.
- **Everything a tool turns down is a `ModelRetry`**, never a fault that ends the turn, because all
  of it is correctable from the message. `RETRIES` is above Pydantic AI's default of one for the
  same reason.
