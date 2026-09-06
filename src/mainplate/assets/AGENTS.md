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

## A phone rule goes in the block at the end of `mainplate.css`

The queries overlap, so the narrow one wins **only by coming later**. A shape rule written beside
the thing it is about, above that block, is silently overridden: that is how the 17rem sidebar
column came back on every phone and left the conversation a hundred pixels wide.

A rule that applies at *two* widths, such as the rail's overlay, stays with the thing it is about.
`TestTheShapeOfANarrowWindow` is what fails when this goes.

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

## Two more that are easy to undo

- **Do not replace `htmax.min.js` with core plus separately vendored extensions.** One file cannot
  drift from itself; two have to be kept on one version, and the failure when they are not is a swap
  that silently misbehaves. Which extensions register is `EXTENSIONS` in `pages.py`.
- **Everything `mainplate.js` does stays an enhancement.** With the file absent the page must still
  render, still post, and still fold. What it holds is what cannot live in the markup, reapplied
  after every swap through one idempotent `repaint()`.
