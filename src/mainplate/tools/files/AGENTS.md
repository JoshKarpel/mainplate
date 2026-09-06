---
description: "How a model names a line: the content-addressed anchoring scheme behind read, edit and create."
---

# How a model names a line

**A line is addressed by a hash of its own content.** A line number is the one address that cannot
fail, so a stale one silently edits the wrong place; a content hash either resolves to exactly one
line or does not resolve, which turns that into a loud refusal. It also means the model never
retypes what it is replacing, which is the expensive half of a search-and-replace edit and the half
that lands in *output* tokens.

`anchors.py` is the pure half and `tools.py` is the shell around it, which is the split that lets
the interesting part be tested with a list of strings.

## The numbers behind the scheme

Four things about it were measured against this repository rather than chosen, and the numbers are
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

The cost of that last rule is the thing to know before changing it: an extended anchor depends on
its neighbours, so an edit just above one invalidates it. Measured here, a single-line edit
invalidates 0.59 anchors and 0.13 of those are more than five lines away. **Both are answered by
what the reply says rather than by making anchors survive changes they should not survive**: the
changed regions come back with fresh anchors, and any anchor that moved elsewhere comes back as an
explicit remapping. `written` has both tables in hand, so the remapping costs a comparison rather
than any state kept between calls. There is deliberately no store of anything.

## A batch of operations

`edit` takes a **list** of operations, resolved against one reading of the file and applied
together. That is the thing content addressing buys that search-and-replace cannot: overlap is
*decidable* before anything is written, so a contradictory batch is refused entire instead of
resolved in an order nobody chose.

Which lines a span covers is said by the **field name**, so there is no inclusive-or-exclusive flag
to get backwards: `from`/`to` are inside the span and `after`/`before` are outside it. One bound
alone inserts there, and only the exclusive forms may do that, since an inclusive bound with no
partner does not describe a span. The exclusive forms are also how a span reaches a blank line,
which has no anchor: deleting a function and the blanks after it is `from` its first line `before`
the next code line.

`substitute` is the one operation that works *inside* an anchored line rather than over a span of
them, for where retyping a whole paragraph to change one word is the wasteful part. It is the same
bargain the anchors themselves are: what the model does not retype is what does not land in output
tokens.

## Two calls at one file are serialised

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
rewriting a file under an `edit` is outside what this can see. That is the same boundary snapshots
already draw when they capture only at model-request boundaries, where every tool of the previous
batch has returned by construction.

## Every tool that writes hands back anchors

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

## Line endings

`Text` carries the two things `splitlines` throws away, the line endings and the final newline, and
`tools.py` reads and writes with `newline=""` so universal-newline translation does not quietly
normalise a CRLF file. Without both halves an edit to one line is a diff on every line, attributed
to an edit that touched one. It splits on `\n` and not with `splitlines`, which also breaks on form
feed, a page break some source files genuinely use.
