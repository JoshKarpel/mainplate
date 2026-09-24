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

import re
from collections.abc import Iterator
from functools import lru_cache
from typing import Final

import nh3
from markdown import Markdown
from markupsafe import Markup
from markupsafe import escape
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.lexers import get_lexer_for_filename
from pygments.token import STANDARD_TYPES
from pygments.token import _TokenType
from pygments.util import ClassNotFound

# The wrapper `codehilite` puts around a highlighted block, which is the one class here that is not
# a token: Pygments names the spans inside, and the extension names the box.
HIGHLIGHT: Final = "codehilite"

# Every class a highlighted block can carry, taken from Pygments' own table rather than listed
# here. A hand-written list would be a second declaration of somebody else's vocabulary, wrong the
# first time a language used a token nobody thought of, and wrong silently: the class would be
# stripped and that run of code would render unhighlighted with nothing saying why.
TOKENS: Final[frozenset[str]] = frozenset({name for name in STANDARD_TYPES.values() if name} | {HIGHLIGHT})

# The fence labels the script can draw a picture from, as the class each puts on its `<code>`. Two
# and closed, for the reason the token set is closed: the label is written by a model, and a class
# per label would be a class of the model's choosing on the page. `language-` is `codehilite`'s own
# prefix, kept so the class reads as what it is. Everything else a fence is labelled with reaches
# the formatter the same way and is stripped by the sanitiser, which is the test that pins this.
LANGUAGE_PREFIX: Final = "language-"
DRAWABLE: Final[frozenset[str]] = frozenset(f"{LANGUAGE_PREFIX}{label}" for label in ("mermaid", "svg"))

# Only web URLs are links. Command and tool output is untrusted text, so every non-URL run is escaped
# before it becomes markup and the URL is the only part given an `href`.
URL: Final = re.compile(r"""https?://[^\s<>()\[\]{}"']+""")
TRAILING_URL_PUNCTUATION: Final = ".,;:!?"

# Which classes survive sanitising, per tag. `allowed_classes` rather than allowing the `class`
# attribute itself, and the difference is the whole point: this text is shaped by a model, so
# `class` left open would let a reply paint itself as any part of this console's own chrome. A
# closed set of Pygments token names cannot, and neither can the two labels the script draws from,
# which are allowed on the one element the formatter puts them on.
ALLOWED_CLASSES: Final[dict[str, set[str]]] = {
    tag: set(TOKENS) | (set(DRAWABLE) if tag == "code" else set()) for tag in ("div", "pre", "code", "span")
}


class Labelled(HtmlFormatter[str]):
    """
    Pygments' HTML formatter, keeping the fence's label on the `<code>` it wraps.

    `codehilite` hands a formatter *class* the label as `lang_str` and hands the stock `html`
    formatter nothing, so which formatter is named decides whether the page can tell a fence
    labelled `mermaid` from one labelled `text`. The stock formatter keeps the option with every
    other it was not asked for and reads it nowhere, which is why this is a subclass rather than a
    configuration: the only thing it changes is the open tag `_wrap_code` yields.
    """

    def _wrap_code(self, inner: Iterator[tuple[int, str]]) -> Iterator[tuple[int, str]]:
        label = str(self.options.get("lang_str", ""))
        yield 0, f'<code class="{escape(label)}">' if label else "<code>"
        yield from inner
        yield 0, "</code>"


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
        # The class rather than the name, which is the difference between the label reaching the
        # page and being dropped on the way; see `Labelled`.
        "pygments_formatter": Labelled,
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
def linked_text(text: str) -> Markup:
    """Verbatim text with its HTTP(S) URLs rendered as links."""
    parts: list[Markup] = []
    at = 0
    for found in URL.finditer(text):
        url = found.group().rstrip(TRAILING_URL_PUNCTUATION)
        parts.append(escape(text[at : found.start()]))
        parts.append(Markup('<a href="{}" referrerpolicy="no-referrer">{}</a>').format(url, url))
        parts.append(escape(found.group()[len(url) :]))
        at = found.end()
    parts.append(escape(text[at:]))
    return Markup().join(parts)


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


# The lexer a shell command is coloured by. `sh -c` is what runs it, and Pygments' one shell lexer
# reads POSIX shell and bash alike.
SHELL: Final = "bash"


@lru_cache(maxsize=256)
def language_of(path: str) -> str | None:
    """
    The Pygments lexer a file is coloured by, from its name alone, or nothing where it knows none.

    Nothing rather than a guess, for the reason an unlabelled fence is left alone: a wrong grammar
    reads worse than no colour. The first alias is what `get_lexer_by_name` takes back, and a name
    rather than the lexer itself so that `highlighted` has something hashable to cache on.

    Cached because the lookup walks every lexer's filename patterns, and a transcript re-renders
    whole whenever the turn in flight records anything.
    """
    try:
        return str(get_lexer_for_filename(path).aliases[0])
    except ClassNotFound, IndexError:
        return None


def token_class(kind: _TokenType) -> str:
    """
    The class Pygments' HTML formatter would put on a token of this kind, which may be none.

    A kind the table does not name takes its nearest named ancestor's, which is the formatter's
    own rule for a lexer's private subtypes.
    """
    found = kind
    while found not in STANDARD_TYPES and found.parent is not None:
        found = found.parent
    return STANDARD_TYPES.get(found, "")


@lru_cache(maxsize=512)
def highlighted(language: str, text: str) -> tuple[Markup, ...]:
    """
    `text` as one run of markup per line of it, each token wrapped in the class Pygments names it.

    Per line rather than as the one block the HTML formatter produces, because what the page draws
    in front of each line - an anchor, a diff's line numbers - is a fact about that line, so the
    markup has to be cut where the lines are. A token that spans lines is cut with them, which is
    what the formatter's own line wrapping does too.

    Stripping and the trailing newline are both off, so the lexer is handed exactly the text and
    hands back exactly as many lines. The one preprocessing step no option turns off is a bare
    carriage return becoming a line break, so a text that comes back with a different number of
    lines is shown uncoloured rather than misaligned: what the gutter says about a line has to be
    about that line.

    Cached for the reason a message is, and bounded smaller because what is cached is larger: a
    read is up to fifteen hundred lines, where a message is a few paragraphs.
    """
    lines = text.split("\n")
    lexer = get_lexer_by_name(language, stripnl=False, ensurenl=False)
    marked: list[list[Markup]] = [[]]
    for kind, value in lexer.get_tokens(text):
        cls = token_class(kind)
        for at, piece in enumerate(value.split("\n")):
            if at:
                marked.append([])
            if not piece:
                continue
            marked[-1].append(Markup('<span class="{}">{}</span>').format(cls, piece) if cls else escape(piece))
    if len(marked) != len(lines):
        return tuple(escape(line) for line in lines)
    return tuple(Markup().join(pieces) for pieces in marked)
