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

A further tool is a new package beside `files/`, `bash/` and `handoff/` and one more name in that
list. It is not an edit to anything that already imports them.

## Which tools a session gets

Read off `Choice.isolation`, not off whether the session picked a repository:

| Filesystem | Over its files | `bash` |
|---|---|---|
| `WORKTREE` | `list`, `read`, `edit`, `create` over the worktree and the scratch | where there is a sandbox |
| `EVERYTHING` | the same four over `/`, and `list` refuses since nothing there is in git | where there is a sandbox |
| `NOTHING` | none of them | no |

`NOTHING` gets none rather than four that can only fail, because a tool that cannot work still costs
its description on every request.

**`hand_off` is outside that table and is in every session**, `NOTHING` included. It is not an
exception to the rule but a different subject: what it reaches is the conversation, and every
session has one. Do not condition it on the isolation, and do not add it only when a handoff is
wanted, since a tool definition sits above the cached prefix and introducing one late invalidates
the whole conversation beneath it.

## Two things not to undo

- **`list` asks git, and keeps doing so once `bash` exists.** It is not a listing convenience that
  `git ls-files` in a shell replaces: it builds a tree, opens it only to `depth`, summarises past
  that with a count, and caps at `MAX_ROWS`. That is the difference between orienting in a large
  repository for hundreds of tokens and for tens of thousands.
- **Everything a tool turns down is a `ModelRetry`**, never a fault that ends the turn, because all
  of it is correctable from the message. `RETRIES` is above Pydantic AI's default of one for the
  same reason.
