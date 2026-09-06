# How a model reaches a file

What a session's agent is given to work with: which tools it gets, how a line is addressed, and why
`edit` takes a batch rather than a search and a replacement.

Where those tools may reach, and the mount namespace `bash` runs behind, is [where a command
runs](sandbox.md). This is what the tools themselves are.

## One package per tool

Every tool lives under `tools/`, as `tools/{name}/{module}.py`. Only the constructor reaches the
harness: `tools/__init__.py` exports the constructors and the values they take and nothing else, so
`agent.py` asks for the tools a workspace affords without knowing that editing is anchored, that a
worktree root has to be resolved against, or how a command is confined. A further tool is a new
package beside `files/`, `bash/` and `handoff/` and one more name in that list, rather than an edit
to anything that already imports them.

Within `files/`, `anchors.py` is pure and `tools.py` is the shell around it, which is the split that
lets the interesting half be tested with a list of strings.

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

`handoff/` is therefore the one package whose subject is the conversation rather than the machine,
which is why it alone is not conditioned on the isolation. It is in every session's cached prefix
from the first request rather than added when a handoff is wanted, because tool definitions sit
above the system prompt: introducing one at handoff time would cost a full uncached read of the
window, where a permanent one costs its own description at cache-read prices on every request. Four
orders of magnitude. It takes the document as an argument rather than reading one out of the turn's
prose, because a model asked for a handoff in words leaks the framing around it into what the next
model is told. Both are argued in full in [the composer](composer.md#handoff).

## `list`

**It asks git rather than walking**, so a `.gitignore` is obeyed and a `.venv` or a `node_modules`
never reaches a context window. `git ls-files --cached --others --exclude-standard` is the exact
call, and each flag earns its place: `--cached` is what is committed, `--others` is what the agent
itself just wrote, and `--exclude-standard` is the ignoring. What comes back is *flat*, one path per
entry, because git records files and never directories; `catalogue` builds the tree from those
paths, which is also why an empty directory does not appear at all.

```text
., 8 files within 2 levels

.gitignore
README.md
pyproject.toml
src/
  demo/ (4 files)
tests/
  test_app.py
```

`depth` bounds the answer rather than hinting at it: a directory at that depth is summarised with a
count instead of opened, and `MAX_ROWS` is the backstop on a large depth over a large repository.

**It survives `bash` rather than being replaced by it**, because what it does is not listing. A `git
ls-files` in a shell returns every path in the repository, flat, into a context window; `list`
builds a tree from those paths, opens it only to `depth`, summarises a directory past that with a
count, and caps the whole answer at `MAX_ROWS`. That is context economy, and it is the difference
between orienting in a large repository for a few hundred tokens and doing it for tens of thousands.
The sandbox gives it a second reason to exist: `list` is a narrow tool whose argv this console
writes, so the question a model asks most often stays off the unbounded path.

## A line is addressed by a hash of its own content

A line number is the one address that cannot fail: an edit above shifts everything below it and `47`
still resolves, so a stale line number silently edits the wrong place. A content hash either
resolves to exactly one line or does not resolve, which turns that into a loud refusal. It also
means the model never retypes what it is replacing, which is the expensive half of a
search-and-replace edit and the half that lands in *output* tokens.

A read puts a four-letter anchor in front of every line:

```text
app.py, 6 lines

cxec│def greet(name):
infr│    return f"hello {name}"
----│
----│
vhvn│def farewell(name):
kxpe│    return f"bye {name}"
```

### The numbers behind the scheme

Five things about it were measured against this repository rather than chosen, and the numbers are
the reason not to "simplify" any of them:

- **Four lowercase letters.** 26^4 expects about one collision per thousand distinct lines. Three
  base62 characters, which the published implementations of this idea use, collide 88% of the time
  over a thousand lines, which is why they need probe-based tie-breaking and then a persistent store
  to keep the probe order stable. One more character deletes that whole tower.
- **Letters, not digits.** OpenAI's tokenizer packs digit runs three to a token, so digits look
  ideal there; Qwen and StarCoder2 spend one token per digit, where a six-digit anchor costs three
  times as much. Four lowercase letters cost 2.4 to 3.1 extra tokens per line on every tokenizer
  tested.
- **A box-drawing `GUTTER` divides the anchor from the line, and it is worth the token per line it
  costs over a space.** A space is what a line of code is already full of, so `xhkm # mainplate`
  says nothing about where the name stops and the file starts. A model that guesses wrong writes the
  anchor back as content on its next edit, and every read after that shows a *fresh* anchor in front
  of the stale one, which confirms the guess and puts recovery out of reach. That is observed, not
  hypothetical: a session burned fifteen model requests on a one-line README that way. No ASCII
  character is safe here, since a plain `|` can legitimately open a line and costs the same 1.0
  token per line on `o200k_base` (0.9 on `cl100k_base`) as the box character does.
- **Blank lines get no anchor.** They are 17% of the lines here and *none* is unique on its own
  content, so they were the largest single source of both overhead and instability. Leaving them out
  takes the share of lines unique on their own content from 62.5% to 75.6% and cuts the lines
  needing three or more lines of context by 41%. They are still *rendered* with the gutter and the
  `UNADDRESSABLE` marker, so the column never breaks and no line of a read is parsed by a different
  rule than the one above it, which costs 2 tokens per blank line. Dashes rather than spaces there,
  at identical token cost: a run of spaces before the bar is invisible, so a deliberate "no anchor"
  would read the same as an anchor that went missing.
- **A duplicate line and a hash collision are the same problem**, so one rule answers both: where
  two lines share an anchor, extend each with the line before it and hash again. About 24% of
  anchorable lines need one line of context and 5% need two, capped at `MAX_DEPTH`; past that a line
  is inside a run nothing tells apart and gets no anchor, which the model routes around.

**The cost of that last rule is the thing to know before changing it**: an extended anchor depends
on its neighbours, so an edit just above one invalidates it. Measured here, a single-line edit
invalidates 0.59 anchors and 0.13 of those are more than five lines away. Both are answered by what
the reply says rather than by making anchors survive changes they should not survive: the changed
regions come back with fresh anchors, and any anchor that moved elsewhere comes back as an explicit
remapping. `written` has both tables in hand, so the remapping costs a comparison rather than any
state kept between calls. There is deliberately no store of anything.

## A batch of operations

`edit` takes a **list**, resolved against one reading of the file and applied together. That is the
thing content addressing buys that search-and-replace cannot: overlap is *decidable* before anything
is written, so a contradictory batch is refused entire instead of resolved in an order nobody chose.

Which lines a span covers is said by the **field name**, so there is no inclusive-or-exclusive flag
to get backwards:

```json
{"op": "splice", "from": "vhvn", "before": "kxpe", "text": ""}
```

`from`/`to` are inside the span and `after`/`before` are outside it. One bound alone inserts there,
and only the exclusive forms may do that, since an inclusive bound with no partner does not describe
a span. The exclusive forms are also how a span reaches a blank line, which has no anchor: deleting
a function and the blanks after it is `from` its first line `before` the next code line, so it
neither names a blank nor retypes the line it stops short of.

`substitute` is the one operation that works *inside* an anchored line rather than over a span of
them, for where retyping a whole paragraph to change one word is the wasteful part. It is the same
bargain the anchors themselves are: what the model does not retype is what does not land in output
tokens.

### Two calls at one file are serialised

**Because a batch of tool calls runs concurrently.** `Files` holds a lock per resolved path and
every operation takes the one for the path it touches. Without it two `edit`s aimed at one file
interleave: each reads, each computes against what it read, each writes, and the loser's work
vanishes while *both* calls report success to the model. Two `create`s race the same way, both
seeing a path that is not there yet, so `create`'s promise never to overwrite quietly fails.

The lock is held around the whole read-modify-write rather than around the write, and that is what
makes it work: serialised that way the second call reads the first one's result, so anchors do the
job they were chosen for. An edit whose anchors the first one invalidated fails loudly; one whose
anchors still resolve lands. `create`'s existence check is inside the lock for the same reason.

It does not reach `bash`, whose paths are not knowable before the command runs, so a shell command
rewriting a file under an `edit` is outside what this can see. That is the same boundary
[snapshots](workspace.md#snapshots) already draw when they capture only at model-request boundaries,
where every tool of the previous batch has returned by construction.

### Every tool that writes hands back anchors

So a write is never followed by a read to find out where anything now is. `create` renders the whole
new file; `edit` renders the changed regions and names any anchor that moved elsewhere. That is one
property rather than two conveniences, and it is what lets a run of edits happen with no re-read
between them.

## Two refusals rather than omissions

**There is no `write`**: a tool that overwrites a whole file is the escape hatch that makes anchored
editing pointless, since the first refused edit becomes a full rewrite that discards whatever was
not read. `create` is that same whole-file write restricted to the one case where the objection does
not apply, a path that does not exist yet having nothing to discard, so `here.exists()` is the
entire difference between the tool that is here and the tool that is refused. It is also why there
is no `delete` now that there is a `bash`: deletion addresses nothing and returns nothing, so it
never joins the anchoring scheme, and `rm` does it exactly as well.

**The formatter is not wired into `edit`**, which was tried and dropped: exclusive bounds fix the
addressing gap that made blank-line hygiene awkward, where a formatter would only have tidied the
symptom, at the price of a per-repository configuration decision on every write.

## What `bash` does not get a defence for

The at-least-once window. Anchored `edit` has one, since a re-run edit fails loudly on anchors its
own first run invalidated; an arbitrary shell command re-runs silently. That is the cost of `step`
rather than `transact`, see [durability](durability.md#the-capability), and it is unchanged by the
sandbox, which bounds where a command reaches and says nothing about how many times it runs.

## Refusals reach the model

Everything a tool turns down arrives as a `ModelRetry`, because all of it is correctable from the
message: a stale anchor, a `find` occurring twice, a batch that overlaps. `RETRIES` is above
Pydantic AI's default of one for that reason, and the reason is observed rather than theoretical: a
smaller model got an operation's shape wrong once and the default turned a correctable mistake into
a failed turn.

## Line endings

`Text` carries the two things `splitlines` throws away, the line endings and the final newline, and
`tools/files/tools.py` reads and writes with `newline=""` so universal-newline translation does not
quietly normalise a CRLF file. Without both halves an edit to one line is a diff on every line,
attributed to an edit that touched one. It splits on `\n` and not with `splitlines`, which also
breaks on form feed, a page break some source files genuinely use.
