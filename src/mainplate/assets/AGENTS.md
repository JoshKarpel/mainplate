---
description: What a change to the stylesheet, the script or the vendored font must not break.
---

# The assets

Why any of this is the way it is, including every number below, is [The stylesheet, the script, and
the grid](https://joshkarpel.github.io/mainplate/design/assets/). What follows is what must hold
while editing here.

An edit is only visible to a *new* process, since the assets are inventoried once at startup. `just
serve` restarts on any change under `src/mainplate`; `just shots` is how a styling change gets
looked at rather than argued about.

## A narrow rule goes in the block at the end of `mainplate.css`

There are two shapes and one width between them, 78rem, and every rule for the narrow shape is in
the one `max-width` block at the end of the file. **Do not add a second top-level width.** There
used to be a shape between, with the rail folded and the list not, and its query overlapped the
narrow one so that whichever came later won: a shape rule written beside the thing it is about,
above the narrow block, was silently overridden, which is how the 17rem sidebar column came back on
every phone and left the conversation a hundred pixels wide. One block has no order to get wrong.

**What a phone needs beyond the fold goes in the `48rem` query nested at the end of that block**:
room on a line, a pointer that hovers, a height a whole picker fits in, a thumb to press with. The
fold is about columns and fires on half a laptop; a rule that stacks a line or hides half of it
fires there too if it is written in the outer block, which is how a 1200px window came to draw its
rules the way a phone does. Nested, it comes after everything it refines and needs no ordering.

`TestTheShapeOfANarrowWindow` is what fails when this goes, and it also pins that the narrow shape
begins exactly where three columns stop fitting.

## `--mono-size` and `--mono-line` are measured, in pixels, and do not scale

The pitch must be no more than the `│` glyph's own ink **and** a whole number of pixels, or box
drawing draws as a dashed line. At size 14 a run joins at 23 and breaks at 24, so the pitch is 22.

- **Do not put them in `rem`.** `html { font-size }` scales everything else on the page; these are
  the one thing it must not reach, because the whole numbers either side of the answer are a pixel
  apart.
- **Do not move either alone.** They move together, and moving them is a *measurement* against a
  real rendering, not a multiplication.
- **Both belong to every monospace block**, `.text pre` and `.tool__body pre` alike. The latter
  restates the font family deliberately: a browser's own sheet sets `pre` to `monospace`, and a rule
  on the element beats a value inherited from an ancestor.

## The gutter in front of a line is `data-gutter`, painted by `::before`

A diff's line numbers are an attribute the stylesheet draws, not text, so that copying the block
copies the diff; `mainplate.js` reads `textContent` and never sees them. A read's anchors are not
drawn at all, and a line the tool wrote itself is `data-said`, which is the only thing that sets it
apart from the file's lines. **Do not move the gutter into the text, and do not draw the anchors**;
do not put a newline *between* `.line` spans either: each carries its own as its last character,
which is what lets a line be `display: block` and paint its whole row while the block's text still
reads as lines. `TestWhatAnOpenCallShows` and the read-copies-the-file browser test are what fail
when any of that goes. The Pygments token colours are on any `pre` rather than `.text pre`, because
a call's body is coloured by the same tokens.

`TestTheGridMonospaceIsDrawnOn` draws a run into a canvas and reads the pixels back, because the
failure is a hairline no screenshot shows.

## Anything somebody else wrote comes through `just vendor`

`htmax.min.js`, `mermaid.min.js`, the two faces and the licence beside each are rows in
`scripts/vendored.toml`, and the copy here is the bytes that row's digest names. **Do not edit one,
and do not drop a script in here by hand**: `tests/test_vendored.py` hashes every row's copy and
refuses a `.js` or `.woff2` no row names. Bumping one is editing the version in its `url`, running
`just vendor`, and recording the digest it refuses on once the file has been looked at. The
pre-commit hooks are told to leave these files alone, which is why a vendored file may end without
a newline while nothing else here does, and `.gitattributes` tells git to store them verbatim, so a
licence published with CRLF is CRLF in every checkout and not only on the machine that vendored it.

`mermaid.min.js` is fetched by the script the first time a `mermaid` fence is on the page and by
nothing else. **Do not put it in a `<script>` tag**, which is three and a half megabytes on every
page for the pages with no diagram. What a drawing is put on the page as is an `<img>` with a `data:` URL,
for SVG written by hand and the library's output alike: an image runs no script and fetches nothing,
and inlining the markup instead would rest the page on the library's own sanitising of text a model
wrote. `TestDrawingAFence` is what fails when either goes.

## The faces are upstream, unmodified, and there are two of them

`JuliaMono-Regular.woff2` and its bold, under the OFL beside them. Two static weights rather than
one variable file, so the 600 a panel role asks for resolves to the bold instead of being
synthesised by smearing the regular. No italic face is vendored, which is why code inside a
reasoning panel must not be slanted: an oblique is synthesised by shearing every glyph, and it leans
a gutter while leaving the horizontals flat.

Before swapping the face for a smaller one, read the coverage numbers on the design page. The
megabyte is buying every symbol block on the cell, and a fallback filling a gap does it one
character at a time at the wrong advance.

## The installed app stays online-only

`service-worker.js` is network-only. It may register and control `/`, but it must not write Cache
Storage entries or supply an offline response: the server's checkpoint is the conversation, and a
cached page would be an empty shell or a stale second copy. The `Service-Worker-Allowed` header in
`app.py` is what lets a script under `/assets/` take that root scope.

The manifest's `192x192` and `512x512` PNGs and the `180x180` Apple touch icon are raster forms of
`icon.svg`. Keep the mark inside the central safe circle so a platform's mask does not cut it.

## Two more that are easy to undo

- **Do not replace `htmax.min.js` with core plus separately vendored extensions.** One file cannot
  drift from itself; two have to be kept on one version, and the failure when they are not is a swap
  that silently misbehaves. Which extensions register is `EXTENSIONS` in `pages.py`.
- **Everything `mainplate.js` does stays an enhancement.** With the file absent the page must still
  render, still post, and still fold. What it holds is what cannot live in the markup, reapplied
  after every swap through one idempotent `repaint()`.
- **`soon` words a duration exactly as `elapsed` in `pages.py` does, including the unit that is
  zero.** The server draws the first figure and this repaints it a second later, into the same
  element, so dropping a `0m` here is a countdown that changes shape while a reader is looking at it.
  Two units at every width above a minute, on both sides. The widths the two owe each other are
  `WORDED` in `tests/conftest.py`, which both suites are parametrised from, so a width added here
  goes in that table rather than in either test. See
  [the format is canonical, and the zone is the only thing that varies](https://joshkarpel.github.io/mainplate/design/console/#the-format-is-canonical-and-the-zone-is-the-only-thing-that-varies).
- **Do not format a moment here.** `paintClock` writes the reader's zone into a cookie and asks for
  the page again where the one it got was drawn against another; every date and time on the page is
  rendered by `pages.py`. Rewriting `<time>` elements instead looks like the smaller change and is
  the larger one: half the moments on this page are inside sentences a tooltip holds, so it buys a
  second implementation of what a date looks like, in a language that cannot see the first. See
  [which clock a moment is printed against](https://joshkarpel.github.io/mainplate/design/console/#which-clock-a-moment-is-printed-against).
  There is also nothing here to localise: the format is canonical `%Y-%m-%d %H:%M` at every reader,
  so only *which instant* follows the browser and never how it is written.
- **`paintClock` runs beside `applyTheme`, before the document exists, and needs to.** It reads the
  zone the page was drawn against off `<html>`, whose open tag the parser has already passed; moved
  into `start` with the rest of the wiring it would read `document.body`, which is `null` there, so
  a reader in another zone would paint a whole page of wrong times before asking for the right ones.
  Move the attribute to `<body>` and the check stops firing at all rather than firing late, which is
  what `TestTheClockAPageIsDrawnAgainst`'s browser tests fail on.
- **Nothing that runs before `start` may throw**, because it all sits in one block: an exception
  there takes the rest of the file with it, leaving a page with no folds, no copy buttons, no live
  connection and no composer, which is worse than the script being absent. That is why `held`, `hold`
  and `sameClock` catch, and why `cookieValue` treats a value it cannot decode as nothing: a cookie
  is arbitrary text and `decodeURIComponent` raises on a malformed escape.
- **`start` returns where there is no `<body>`, and that case is reachable.** `paintClock` can ask
  for the page again from the head, which abandons the parse where it stands, and
  `DOMContentLoaded` still fires on what was abandoned. The document is already being replaced, so
  there is nothing to wire; without the guard every first visit from another zone raises.
- **The reload is worth doing once and never twice.** `paintClock` reads its own cookie back before
  reloading, because a browser blocking this origin's cookies makes the write a silent no-op, and a
  guard that trusted it would ask for the page again on every load for ever.
