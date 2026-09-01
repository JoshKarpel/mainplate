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
from typing import Final

import nh3
from markdown import Markdown
from markupsafe import Markup
from pygments.token import STANDARD_TYPES

# The wrapper `codehilite` puts around a highlighted block, which is the one class here that is not
# a token: Pygments names the spans inside, and the extension names the box.
HIGHLIGHT: Final = "codehilite"

# Every class a highlighted block can carry, taken from Pygments' own table rather than listed
# here. A hand-written list would be a second declaration of somebody else's vocabulary, wrong the
# first time a language used a token nobody thought of, and wrong silently: the class would be
# stripped and that run of code would render unhighlighted with nothing saying why.
TOKENS: Final[frozenset[str]] = frozenset({name for name in STANDARD_TYPES.values() if name} | {HIGHLIGHT})

# Which classes survive sanitising, per tag. `allowed_classes` rather than allowing the `class`
# attribute itself, and the difference is the whole point: this text is shaped by a model, so
# `class` left open would let a reply paint itself as any part of this console's own chrome. A
# closed set of Pygments token names cannot.
ALLOWED_CLASSES: Final[dict[str, set[str]]] = {tag: set(TOKENS) for tag in ("div", "pre", "code", "span")}

# One converter for the process. `Markdown` accumulates state across a conversion and must be
# reset between them, which makes it a place rather than a value; holding one is safe here only
# because `convert` never awaits, so no second render can interleave with one on this event loop.
# It is not safe to share across threads, and this must not become one that is.
CONVERTER = Markdown(
    extensions=[
        # Fenced code, because a console answered by a model is mostly code.
        "fenced_code",
        # Highlighting, because the same is true of what is *in* the fence. It emits classes rather
        # than inline styles so the colours come from the console's own palette and follow the
        # reader's theme; inline styles would also mean allowing `style` through the sanitiser.
        "codehilite",
        "tables",
        # Without this, a list whose markers change mid-way silently merges into one list.
        "sane_lists",
        # A chat box promises that a newline is a newline. Markdown's own rule (a line break needs
        # two trailing spaces) is a rule about documents, and nobody typing a message knows it.
        "nl2br",
    ],
    extension_configs={
        "codehilite": {
            # An unlabelled fence is left alone rather than guessed at. Guessing is slow, it is
            # wrong often enough to be noticeable on short snippets, and a wrong guess colours
            # tokens by a grammar the text is not written in, which reads worse than no colour.
            "guess_lang": False,
            # A language Pygments does not know renders as a plain fence rather than raising, which
            # matters because the fence's label is written by a model.
            "noclasses": False,
        }
    },
    output_format="html",
)


@lru_cache(maxsize=2048)
def as_markup(text: str) -> Markup:
    """
    `text` as Markdown, rendered and then sanitised into markup a page can carry.

    Cached because it is a pure function of its input and the console is not: a transcript is
    re-rendered whole whenever the turn in flight records anything, so an unmemoised conversion
    would re-parse the entire conversation several times a turn to redraw the one panel that
    changed.

    The cache is bounded, and what it can hold is bounded twice over besides: a message is capped
    at the boundary that accepts it, and a key is the exact text, so the same message renders once
    however many times it is drawn.
    """
    CONVERTER.reset()
    return Markup(nh3.clean(CONVERTER.convert(text), allowed_classes=ALLOWED_CLASSES))
