---
description: The invariants the anchoring scheme rests on, and what a change here must not break.
---

# The anchored file tools

`anchors.py` is pure and `tools.py` is the shell around it, which is the split that lets the
interesting half be tested with a list of strings.

Why a line is addressed by a hash of its own content, and the measurements behind every constant
below, are [How a model reaches a
file](https://joshkarpel.github.io/mainplate/design/tools/). What follows is what a change here must
not break.

## The scheme's constants were measured, not chosen

Four lowercase letters, a box-drawing `GUTTER`, no anchor on a blank line, and extension by the
preceding line up to `MAX_DEPTH`. **Each has a number behind it on the design page.** Three base62
characters collide 88% of the time over a thousand lines; digits cost three times as much on some
tokenizers; a space instead of the bar has been observed to burn fifteen model requests on a
one-line README; blank lines were 17% of the lines and none unique. Read the page before
"simplifying" any of the four.

Two that look like slack and are not: a blank line still renders with the gutter and the
`UNADDRESSABLE` marker, so the column never breaks, and the marker is dashes rather than spaces so a
deliberate absence cannot read as an anchor that went missing.

## Nothing is stored between calls

Anchors are recomputed on every read. `written` holds both tables at once, so an anchor that moved
elsewhere is reported by comparison rather than out of any state kept between calls. Do not add a
store; the scheme was chosen partly because it needs none.

## The lock spans the read, not the write

`Files` holds one per resolved path and every operation takes the one for the path it touches, held
around the whole read-modify-write. Narrow it to the write and two concurrent `edit`s at one file
both report success while one of them is lost, and `create`'s promise never to overwrite quietly
fails. `create`'s existence check belongs inside it for that reason.

It does not reach `bash`, whose paths are not knowable before the command runs.

## A span is named by its field, never by a flag

`from`/`to` are inside it, `after`/`before` are outside it. One bound alone inserts there, and only
the exclusive forms may do that. Keep it that way: a flag is a thing to get backwards, and the
exclusive forms are also the only way a span reaches a blank line.

## Two tools that must not exist

- **No `write`.** A tool that overwrites a whole file is the escape hatch that makes anchored
  editing pointless: the first refused edit becomes a full rewrite discarding whatever was not read.
  `here.exists()` is the entire difference between `create` and the tool that is refused.
- **No `delete`.** It addresses nothing and returns nothing, so it never joins the scheme, and `rm`
  through `bash` does it exactly as well.

The formatter is not wired into `edit` either. It was tried and dropped.

## Read and write with `newline=""`

`Text` carries the line endings and the final newline that `splitlines` throws away, and universal
newline translation would quietly normalise a CRLF file, so an edit to one line becomes a diff on
every line. Split on `\n` rather than with `splitlines`, which also breaks on form feed.
