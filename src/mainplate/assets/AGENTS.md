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

`TestTheGridMonospaceIsDrawnOn` draws a run into a canvas and reads the pixels back, because the
failure is a hairline no screenshot shows.

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
