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

# What every conversion here does, whatever the text came from.
EXTENSIONS: Final = [
    # Fenced code, because a console answered by a model is mostly code.
    "fenced_code",
    # Highlighting, because the same is true of what is *in* the fence. It emits classes rather
    # than inline styles so the colours come from the console's own palette and follow the
    # reader's theme; inline styles would also mean allowing `style` through the sanitiser.
    "codehilite",
    "tables",
    # Without this, a list whose markers change mid-way silently merges into one list.
    "sane_lists",
]

EXTENSION_CONFIGS: Final = {
    "codehilite": {
        # An unlabelled fence is left alone rather than guessed at. Guessing is slow, it is
        # wrong often enough to be noticeable on short snippets, and a wrong guess colours
        # tokens by a grammar the text is not written in, which reads worse than no colour.
        "guess_lang": False,
        # A language Pygments does not know renders as a plain fence rather than raising, which
        # matters because the fence's label is written by a model.
        "noclasses": False,
    }
}

# One converter per kind of text, for the process. `Markdown` accumulates state across a conversion
# and must be reset between them, which makes it a place rather than a value; holding one is safe
# here only because `convert` never awaits, so no second render can interleave with one on this
# event loop. Neither is safe to share across threads, and this must not become one that is.
#
# Two of them, and the whole difference is `nl2br`. A chat box promises that a newline is a newline,
# because Markdown's own rule - a line break needs two trailing spaces - is a rule about *documents*
# and nobody typing a message knows it. A guidance file is a document, written by somebody who does:
# it is soft-wrapped at whatever width its author's editor uses, so honouring those newlines draws a
# paragraph as a column of ragged lines that says nothing about how it was written.
MESSAGE = Markdown(extensions=[*EXTENSIONS, "nl2br"], extension_configs=EXTENSION_CONFIGS, output_format="html")

DOCUMENT = Markdown(extensions=EXTENSIONS, extension_configs=EXTENSION_CONFIGS, output_format="html")


def converted(converter: Markdown, text: str) -> Markup:
    """`text` through one converter, rendered and then sanitised into markup a page can carry."""
    converter.reset()
    return Markup(nh3.clean(converter.convert(text), allowed_classes=ALLOWED_CLASSES))


@lru_cache(maxsize=2048)
def as_message(text: str) -> Markup:
    """
    Something somebody typed into a box, or a model answered with.

    Cached because it is a pure function of its input and the console is not: a transcript is
    re-rendered whole whenever the turn in flight records anything, so an unmemoised conversion
    would re-parse the entire conversation several times a turn to redraw the one panel that
    changed.

    The cache is bounded, and what it can hold is bounded twice over besides: a message is capped
    at the boundary that accepts it, and a key is the exact text, so the same message renders once
    however many times it is drawn.
    """
    return converted(MESSAGE, text)


@lru_cache(maxsize=64)
def as_document(text: str) -> Markup:
    """
    A Markdown *file*: the guidance a session is answered under, or a part of the repository's own.

    Cached for the reason a message is, and smaller because there are far fewer of them: one per
    stretch of context, plus whatever a turn was handed on approach.
    """
    return converted(DOCUMENT, text)
