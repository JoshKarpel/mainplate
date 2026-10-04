---
description: What must hold while adding to or changing a page, a fragment, or anything the console draws.
---

# The pages

Every page and fragment the console serves is drawn here, as `without_html` node trees. Why each part
looks and behaves as it does is [the console's design note](https://joshkarpel.github.io/mainplate/design/console/);
what follows is what must hold while editing here. Styling a control is the stylesheet's, and its
constraints are in [`../AGENTS.md`](../AGENTS.md).

## A route gathers, and a function here draws

**No markup is written anywhere else.** A route in `console.py` answers every question the page asks,
reading the store, the clock and the catalogue, and then calls one function here with the answers.
It never builds HTML itself, not as an f-string, not with `html.escape`, and not as a "simple page
that does not need the shell". A page drawn outside this package is one the gallery cannot render,
the stylesheet was never written for, and the next change to `document` or `shell` silently misses.

The one thing a route may send that is not drawn here is bytes somebody else wrote and the console
only serves: an asset, or a file a session produced. Those are served as they are, never wrapped.

## A page is a pure function of already-answered questions

Nothing here reads a store, a file, the network, or the clock. `now()` is not an answered question,
so a duration a page prints is measured by the caller and handed in; see `Conversation.since`. That is
what lets `scripts/gallery.py` draw every page from fixtures with no server behind it, and a function
here that reaches for anything is a page the gallery can no longer draw.

## Every page is a `document`, and every page has `home` on it

`document` is the head, the scripts, the stylesheet and the live connection. **`home` is the one
piece of navigation every page owes a reader**: the console's mark, linking to the dashboard, which
is where every other place is reached from. A refusal's way back is its own sentence linking there.

`shell` is the session list, with `home` at its head, the phone's bar, and the pane, and it is what a
page about conversations is drawn in: `document(links, heading, shell(..., pane=[...]))`, like its
neighbours in `session.py` and `dashboard.py`. A page whose subject is something else may leave the
list off and draw `home` in a bar of its own, as an artifact's page and the debug page do, so that its
subject gets the window. **That bar is `pagebar`**, home and the way up and the title as one path, and
not a header drawn again; such a page holds no live connection (`live=False`), since the list is the
region the stream redraws. The cost, stated: from such a page another session is two presses away rather than
one.

**The shell's bar across the top is drawn only on a phone and holds only the name.** Somewhere new to
go from every page is a section on the dashboard, which is where a reader starting from nothing lands.

## Every address comes from `Links`

A link is `url_for` over the route that serves it, through a `to_*` method on `Links`, so no path is
spelled twice. A new route is a field on `Links`, set in `console.py`'s `LINKS`, and a method here.
**Never write a path as a string literal**, `"/artifacts"` included: the day the route moves, the
literal is a link to nothing and no test notices.

## Reuse what the page already draws

A new piece of an existing kind is built from the one already drawing that kind: a row of a list is
`session_row`'s shape, a fact is `fact`, a moment is `when_element` worded through `moments.py`, a
figure through `figures.py`, and a control of each kind the page has is listed in
[`../AGENTS.md`](../AGENTS.md#a-control-is-one-the-page-already-draws). A second drawing of one kind
is a second look to keep in step.

## Where a new piece goes

Each module's header says what belongs in it. A piece two parts of the page both draw gets a module of
its own below both, rather than one part importing the other to reach it: that is why `archive.py`
and `picker.py` exist. Python refuses an import cycle at startup, so a piece that seems to need one is
a piece in the wrong module.

## Every page and every state of one is in the gallery

A new page, or a new state of an existing one, is a fixture in `scripts/gallery.py` in the same
change, which is what `just shots` screenshots and what the browser tests read. See
[`scripts/AGENTS.md`](../../../scripts/AGENTS.md) for which of a fixture and a variation it is. A page
the gallery does not draw is one nobody looks at until it is broken.
