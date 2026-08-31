# What somebody wrote, as markup safe to put on a page.
#
# Two steps, and both are required. Markdown turns text into HTML, and a sanitiser decides what of
# that HTML is allowed to exist. The second is not belt-and-braces: Python-Markdown passes raw HTML
# through untouched and does not look at URL schemes at all, so `<script>alert(1)</script>` and
# `[click](javascript:alert(1))` both reach the page from a plain `convert`. Its own documentation
# says so and points at an external sanitiser, which is what this is.
#
# The text being sanitised is not hypothetical. A model's reply is shaped by whatever was pasted
# into the box, and this project's direction is an agent that reads repositories, so before long
# the text will be shaped by files and command output too. A console holding somebody's session is
# not the place to find out whether that mattered.

from __future__ import annotations

from functools import lru_cache

import nh3
from markdown import Markdown
from markupsafe import Markup

# One converter for the process. `Markdown` accumulates state across a conversion and must be
# reset between them, which makes it a place rather than a value; holding one is safe here only
# because `convert` never awaits, so no second render can interleave with one on this event loop.
# It is not safe to share across threads, and this must not become one that is.
CONVERTER = Markdown(
    extensions=[
        # Fenced code, because a console answered by a model is mostly code.
        "fenced_code",
        "tables",
        # Without this, a list whose markers change mid-way silently merges into one list.
        "sane_lists",
        # A chat box promises that a newline is a newline. Markdown's own rule (a line break needs
        # two trailing spaces) is a rule about documents, and nobody typing a message knows it.
        "nl2br",
    ],
    output_format="html",
)


@lru_cache(maxsize=2048)
def as_markup(text: str) -> Markup:
    """
    `text` as Markdown, rendered and then sanitised into markup a page can carry.

    Cached because it is a pure function of its input and the console is not: a transcript is
    re-rendered whole on every poll while a reply is in flight, so an unmemoised conversion would
    re-parse the entire conversation once a second to redraw the one panel that changed.

    The cache is bounded, and what it can hold is bounded twice over besides: a message is capped
    at the boundary that accepts it, and a key is the exact text, so the same message renders once
    however many times it is drawn.
    """
    CONVERTER.reset()
    return Markup(nh3.clean(CONVERTER.convert(text)))
