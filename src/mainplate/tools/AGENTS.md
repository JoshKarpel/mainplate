---
description: How a toolset is assembled, and which tools a session gets.
---

# The tools

Every tool lives here, one package per tool, as `tools/{name}/{module}.py`. Only the constructor
reaches the harness: `tools/__init__.py` exports the constructors and the values they take and
nothing else, so `agent.py` asks for the tools a workspace affords without knowing that editing is
anchored, that a worktree root has to be resolved against, or how a command is confined. A further
tool is a new package beside `files/`, `bash/` and `handoff/` and one more name in that list, rather
than an edit to anything that already imports them.

Within `files/`, `anchors.py` is pure and `tools.py` is the shell around it, which is the split that
lets the interesting half be tested with a list of strings. The scheme itself is in
`files/AGENTS.md`.

## Which tools a session gets

**Over its *files*, that is decided by its `isolation`, not by whether it picked a repository.** A
session on `WORKTREE` gets `list`, `read`, `edit` and `create` over its worktree and its scratch;
one on `EVERYTHING` gets the same four over `/`, where `list` refuses because nothing there is in
git; one on `NOTHING` gets **none of them**, because tools that can only fail are worse than none
and cost a description on every request. `bash` is added to the first two wherever there is a
sandbox to run it in.

**`hand_off` is outside that entirely and is in every session**, `NOTHING` included, so a session
with no files still has exactly one toolset rather than none. It is not an exception to the rule
above but a different subject: what it reaches is the conversation, and every session has one.

**`handoff/` is the one whose subject is the conversation rather than the machine**, which is why it
alone is not conditioned on the isolation. It is in every session's cached prefix from the first
request rather than added when a handoff is wanted, because tool definitions sit above the system
prompt: introducing one at handoff time would cost a full uncached read of the window, where a
permanent one costs its own description at cache-read prices on every request. It takes the document
as an argument rather than reading one out of the turn's prose, because a model asked for a handoff
in words leaks the framing around it into what the next model is told. Both are argued in full in
[the composer's handoff section](https://joshkarpel.github.io/mainplate/design/composer/#handoff).

## `list`

**It asks git rather than walking**, so a `.gitignore` is obeyed and a `.venv` or a `node_modules`
never reaches a context window. `git ls-files --cached --others --exclude-standard` is the exact
call, and each flag earns its place: `--cached` is what is committed, `--others` is what the agent
itself just wrote, and `--exclude-standard` is the ignoring. What comes back is *flat*, one path per
entry, because git records files and never directories; `catalogue` builds the tree from those
paths, which is also why an empty directory does not appear at all. `depth` bounds the answer rather
than hinting at it: a directory at that depth is summarised with a count instead of opened, and
`MAX_ROWS` is the backstop on a large depth over a large repository.

**It survives `bash` rather than being replaced by it**, because what it does is not listing. A `git
ls-files` in a shell returns every path in the repository, flat, into a context window; `list`
builds a tree from those paths, opens it only to `depth`, summarises a directory past that with a
count, and caps the whole answer at `MAX_ROWS`. That is context economy, and it is the difference
between orienting in a large repository for a few hundred tokens and doing it for tens of thousands.
The sandbox gives it a second reason to exist: `list` is a narrow tool whose argv this console
writes, so the question a model asks most often stays off the unbounded path.

## What `bash` does not get a defence for

The at-least-once window. Anchored `edit` has one, since a re-run edit fails loudly on anchors its
own first run invalidated; an arbitrary shell command re-runs silently. That is the cost of `step`
rather than `transact` and it is unchanged by the sandbox, which bounds where a command reaches and
says nothing about how many times it runs.

Where a command runs, what it can reach, and why the roots are named rather than pathed is
[`sandbox.py`'s page](https://joshkarpel.github.io/mainplate/design/sandbox/).

## Refusals reach the model

Everything a tool turns down reaches the model as a `ModelRetry`, because all of it is correctable
from the message: a stale anchor, a `find` occurring twice, a batch that overlaps. `RETRIES` is
above Pydantic AI's default of one for that reason, and the reason is observed rather than
theoretical: a smaller model got an operation's shape wrong once and the default turned a
correctable mistake into a failed turn.
