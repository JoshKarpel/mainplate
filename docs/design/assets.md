# The stylesheet, the script, and the grid

`mainplate.css`, `mainplate.js`, the vendored htmx, and the two JuliaMono weights, all served from
the process rather than a CDN so a console on a machine with no route out still renders. What they
draw is [the console](console.md); this is why they are the way they are.

**The stylesheet is a deliverable, and no string assertion checks one.** `just shots` renders every
page from fixture checkpoints and drives a real Chromium over them, so a styling change can be
looked at rather than argued about. `just serve` restarts on any change under `src/mainplate`, which
is what makes an edit watchable: the assets are inventoried once at startup, so an edited stylesheet
only reaches a *new* process.

## Everything the script does is an enhancement

With `mainplate.js` absent the page still renders, posts, and folds. What the rail projects onto the
transcript, the search marks, the panel landed on, which kinds are muted, what is folded, cannot
live in the markup, so the script holds it as values and reapplies it after every swap through one
idempotent `repaint()` serving the first render, every swap, and every press.

## The three shapes, and one place that decides between them

Over 78rem the shell is three columns and the rail stands beside the conversation; between 48rem and
78rem it is two, with the rail lying over the page and drawn shut behind its clasp; under 48rem it
is one, and the session list becomes a strip of chips across the top.

**Every phone rule is in one block at the *end* of `mainplate.css`, and that is not tidiness:** the
queries overlap, so the narrow one wins only by coming later. Split up, the rail's own `max-width:
78rem` block sat below the narrow one and put the 17rem sidebar column back on every phone, leaving
the conversation about a hundred pixels to render in, a bug invisible in either rule and obvious
with both in one list. So a rule that changes shape on a phone goes in that block; a rule that
applies at two widths, such as the rail's overlay, stays with the thing it is about.

A strip rather than a shorter band, and the axis is what decides it: a band is a second *vertical*
scroller stacked on the transcript's own, and two of those on one axis is what feels broken under a
thumb. What a chip gives up is the date, the repository and the tree's indentation, which are for
telling sessions apart where a strip is for getting back to one; the fork marker stays.
`toCurrentSession` brings the session being read into that list and is deliberately shape-blind:
`nearest` scrolls the list on whichever axis it actually scrolls on, so one call serves the strip
and the full-height column both.

Two more things change on a phone, and both follow from it having one column of room. **Nested
same-axis scrollers go away**: the wide picker has the models scrolling inside a block that scrolls
inside the page, which is what keeps the endpoints and the settings put beside a seventy-model list,
and on a phone it bought a list thirty pixels tall. So the models stop scrolling *and* nothing in
the picker shrinks; the `flex: none` is the half that is easy to miss, since every `min-height: 0`
above exists to let a part give way, and with nothing left to scroll that permission just squashes
the list and draws the rest of it over what follows. **And nothing on the transcript is revealed by
hover any more**, which is the same lesson taken one step further than a `@media (hover: none)`
override: the branch link used to appear on a person's panel under the pointer, so on a touch screen
forking did not exist until a media query put it back. On the rule it is simply always drawn, and
there is no pointer question left to answer.

`TestTheShapeOfANarrowWindow` is the guard, and it asks two things because overflow can be right for
the wrong reason: whether any page pushes the document sideways on a phone, and whether a page
carrying a rail is still *one* grid track there. The second is the direct guard on the breakpoint
ordering above, and it reports the sidebar track coming back rather than one of the ways that shows.

## One value scales the whole page

**And it is `html { font-size }`.** Everything except the monospace grid is sized in `rem`, the
text, the spacing steps, the reading measure, the sidebar and the rail, so a single percentage moves
all of it in the proportions it already has. It is 110% because the console read small enough that
the page was better at a browser zoom of 110%, which is the same scaling asked for by hand on every
visit, and a stylesheet that needs a zoom is a stylesheet with a number in the wrong place.

Deliberately *not* the breakpoints, which resolve `rem` against the browser's own default rather
than against this: the three shapes are about how much screen there is, and a shape should change
where the window runs out of room and not where the text got bigger.

## Monospace is a grid, and it is vendored because a grid cannot be borrowed

A model answers in tables and trees, and every `read` comes back as lines behind a `│` gutter, so
most of what a panel here shows is box drawing. Two rows of it join on two conditions, and missing
either draws that column as a dashed line rather than as a line.

The row pitch must be no more than the glyph's own ink, which is a fact about the font: `│` is drawn
over about 1.7 times its size in JuliaMono, where Fira Code, Cascadia Code, DejaVu Sans Mono,
Liberation Mono and Courier New all cap out well under that, so a stack of names makes the console's
spacing depend on which of those the reader happens to have. And the pitch must be a **whole number
of pixels**, or every row lands on a different subpixel phase and the joins falling between two
device rows draw as two half-lit ones, a hairline on some rows of a figure and not others, which is
the failure that survives getting the first condition right.

So `JuliaMono-Regular.woff2` and its bold are upstream unmodified, under the OFL beside them, and
`--mono-size` and `--mono-line` are stated in pixels: at a size of 14 a run joins at a pitch of 23
and breaks at 24, so the pitch is 22, which is that ceiling less the pixel that keeps it off its own
boundary. **Measured rather than taken from the outline**, because a rendered glyph is hinted: the
outline says 1.70em and `measureText` says less again, and neither is the number to build on. That
also means the pair cannot be *scaled* when the rest of the page is, since the whole numbers on
either side of the answer are a pixel apart: `html { font-size }` moves everything in `rem` at once
and this is the one thing it does not reach, so moving it is a measurement rather than a
multiplication.

The pixel held back is not symmetry: overlapping ink still draws the line the figure means, and a
gap draws a line the figure does not. Air beyond that has to come from a larger `--mono-size`, since
the span is a multiple of the size rather than a constant, which is the one knob that moves the
ceiling. Both the size and the pitch have to move together, and both belong to every monospace block
at once: `.text pre` for a fence and `.tool__body pre` for a read, whose family is said again there
rather than inherited, since a browser's own sheet sets `pre` to `monospace` and a rule on the
element beats a value inherited from an ancestor.

**A megabyte a weight is what completeness costs, and completeness is what is being bought.** A
model draws with far more than box drawing, and the gaps in an otherwise good font are exactly what
a fallback fills, one character at a time, on a cell of its own. Measured as the share of each block
a face carries: JuliaMono has all of box drawing, block elements, geometric shapes, arrows,
mathematical operators, miscellaneous symbols, dingbats and braille, every glyph on the cell, where
Fira Code has 2% of dingbats and no braille, Cascadia Code 8% of arrows, Iosevka 361 symbols at the
wrong advance, and Roboto Mono no box drawing at all. Two static weights rather than one variable
file, 400 and 700, so the 600 a panel role asks for resolves to the bold instead of being
synthesised by smearing the regular.

A *nerd font* is not the answer to a missing `✗` and the numbers say why: patching Cascadia Code
adds 9,219 private-use icons and takes it from 598 KB to 3.35 MB while leaving dingbats at 8 of 192,
because what it fills is the private-use area a shell prompt draws from and not the block `✗` lives
in. The lighter alternative to a whole face, if the megabyte ever has to go, is a face cut to the
symbol blocks under a `unicode-range` so it is fetched only by a session that shows one, with
`size-adjust` to put its cell on this grid: JuliaMono cut to arrows through braille is 115 KB.

`TestTheGridMonospaceIsDrawnOn` is what fails when any of this breaks, and it has to be a browser
twice over: every one of these renderings is a correct picture of *some* grid, so what is wrong with
the broken one is a hairline no still and no markup assertion can see, and the join is asked by
drawing a run into a canvas and reading the pixels back rather than by comparing the pitch against a
number. Rows rather than a column, because the stroke is a pixel wide across two half-lit columns
and the inkiest single column reports joins as gaps. A canvas rasterises about a pixel longer than
the same text laid out in the document, so the gap check is the coarse half and `pitch < span` is
the exact one.

**Reasoning is set in italic and the code inside it is not.** A model reasons *about* code, so a
block in a reasoning panel is a quotation of something that exists, and slanting it makes the
quotation differ from the thing quoted. It costs alignment as well: no italic face is vendored, so
an oblique is synthesised by shearing every glyph, which leans a gutter and the sides of a box while
leaving the horizontals flat.

## The vendored htmx

`htmax.min.js` is htmx 4 core plus every bundled extension in one file, with an allowlist in a meta
tag deciding which actually register. Why one file rather than core plus separately vendored
extensions is on [the console's page](console.md#htmx-4), because what reads the allowlist is
`EXTENSIONS` in `pages.py` rather than anything here.

`mainplate.js` itself depends on nothing. `package.json` at the repository root exists only for
`scripts/shoot.mjs`, so a checkout pins two Chromiums: Playwright's Python and Node bindings each
fetch their own.
