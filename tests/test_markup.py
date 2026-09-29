from __future__ import annotations

from collections.abc import Callable

import pytest
from markupsafe import Markup
from pygments.token import Token

from mainplate.markup import DRAWABLE
from mainplate.markup import HIGHLIGHT
from mainplate.markup import LANGUAGE_PREFIX
from mainplate.markup import TOKENS
from mainplate.markup import as_document
from mainplate.markup import as_message
from mainplate.markup import highlighted
from mainplate.markup import language_of
from mainplate.markup import linked_text
from mainplate.markup import token_class

PYTHON = """```python
async def handler(session: str) -> int:
    # a comment
    return 42
```"""

# Both renderers, because the sanitiser's job does not depend on where the text came from: a
# guidance file is written by whoever wrote the repository, which is the same trust as whatever
# reached the message box.
RENDERERS = [as_message, as_document]


class TestWhatReachesThePage:
    def test_prose_is_markdown(self) -> None:
        assert "<strong>bold</strong>" in as_message("**bold**")

    def test_a_fenced_block_is_highlighted_into_token_classes(self) -> None:
        rendered = as_message(PYTHON)
        assert f'class="{HIGHLIGHT}"' in rendered
        assert 'class="k"' in rendered, "keywords carry a token class"
        assert 'class="c1"' in rendered, "comments carry a token class"

    def test_verbatim_text_links_http_urls_without_turning_the_rest_into_markup(self) -> None:
        url = "https://github.com/JoshKarpel/mainplate/compare/main...feature"
        assert (
            linked_text(f"<script> {url}.") == f'&lt;script&gt; <a href="{url}" referrerpolicy="no-referrer">{url}</a>.'
        )

    def test_an_unlabelled_fence_is_left_alone_rather_than_guessed_at(self) -> None:
        """
        A guess is wrong often enough on a short snippet to be worth not making, and a wrong guess
        colours the text by a grammar it is not written in.
        """
        rendered = as_message("```\njust some text\n```")
        assert 'class="k"' not in rendered

    def test_a_language_nobody_has_heard_of_renders_rather_than_raising(self) -> None:
        """The fence's label is written by a model, so an unknown one must not be an exception."""
        assert "gibberish here" in as_message("```notalanguage\ngibberish here\n```")


class TestColouringOneLineAtATime:
    """
    What a call's body is coloured with: the same tokens a fence gets, cut where the lines are, so
    the page can draw something in front of each line.
    """

    def test_each_line_comes_back_as_its_own_markup(self) -> None:
        lines = highlighted("python", "def f():\n    return 1  # one\n")
        assert len(lines) == 3
        assert lines[0].startswith('<span class="k">def</span>')
        assert '<span class="nf">f</span>' in lines[0]
        assert '<span class="c1"># one</span>' in lines[1]
        assert lines[2] == ""

    def test_a_string_spanning_lines_is_coloured_on_both(self) -> None:
        lines = highlighted("python", 'x = """a\nb"""')
        assert len(lines) == 2
        assert lines[0].endswith('<span class="s2">a</span>')
        assert lines[1].startswith('<span class="s2">b</span>')

    def test_a_comment_spanning_lines_is_cut_where_they_are(self) -> None:
        """A lexer that hands back one token for several lines still comes back as one run per line."""
        lines = highlighted("css", "/* one\ntwo */")
        assert len(lines) == 2
        assert lines[0] == '<span class="c">/* one</span>'
        assert lines[1] == '<span class="c">two */</span>'

    def test_what_is_in_the_text_is_escaped(self) -> None:
        (line,) = highlighted("python", "x = '<b>'")
        assert "&lt;b&gt;" in line
        assert "<b>" not in line

    def test_a_bare_carriage_return_leaves_the_text_uncoloured_rather_than_misaligned(self) -> None:
        """The one preprocessing step no lexer option turns off is the one that would add a line."""
        lines = highlighted("python", "def f():\rpass\ndone")
        assert lines == ("def f():\rpass", "done")

    def test_a_tab_is_kept_as_one(self) -> None:
        (line,) = highlighted("python", "\treturn 1")
        assert line.startswith("\t")

    @pytest.mark.parametrize(
        ("path", "language"),
        [("a.py", "python"), ("src/x.css", "css"), ("Makefile", "make"), ("AGENTS.md", "markdown"), ("notes", None)],
    )
    def test_a_file_is_coloured_by_the_grammar_its_name_says(self, path: str, language: str | None) -> None:
        assert language_of(path) == language

    def test_a_kind_of_token_the_table_does_not_name_takes_its_nearest_named_ancestors_class(self) -> None:
        assert token_class(Token.Name.Function.Nobody) == "nf"
        assert token_class(Token.Text) == ""


class TestWhichFencesCanBeDrawn:
    """
    The fence's label reaches the page on exactly two labels, as the class the script draws from.

    The label is written by a model, so which labels reach the page is a closed set for the reason
    the token allowlist is: a class per label would be a class of the model's choosing.
    """

    @pytest.mark.parametrize("label", ["mermaid", "svg"])
    def test_a_drawable_label_is_kept_on_the_code_it_wraps(self, label: str) -> None:
        rendered = as_message(f"```{label}\nsomething to draw\n```")
        assert f'<code class="{LANGUAGE_PREFIX}{label}">' in rendered
        assert f"{LANGUAGE_PREFIX}{label}" in DRAWABLE

    @pytest.mark.parametrize("label", ["python", "text", "notalanguage", "language-svg"])
    def test_every_other_label_is_stripped_on_the_way(self, label: str) -> None:
        """
        The formatter puts every label on the page and the sanitiser takes all but two off, so the
        set is closed by the same allowlist that closes the tokens rather than by a second check.
        """
        rendered = as_message(f"```{label}\nx = 1\n```")
        assert LANGUAGE_PREFIX not in rendered
        assert "x" in rendered

    def test_a_label_that_is_markup_cannot_break_out_of_the_attribute(self) -> None:
        rendered = as_message('```svg"><script>alert(1)</script>\nx\n```')
        assert "<script>" not in rendered


class TestWhereANewlineIsALineBreak:
    """
    The whole of what separates the two renderers, and it is a fact about where the text came from
    rather than about how it should look.
    """

    def test_a_message_s_own_newlines_are_kept(self) -> None:
        """Nobody typing into a box knows that Markdown wants two trailing spaces for a line break."""
        assert "<br>" in as_message("one line\nand another")

    def test_a_document_s_newlines_are_its_wrapping(self) -> None:
        """
        A guidance file is soft-wrapped at whatever width its author's editor uses, so honouring
        those newlines would draw one paragraph as a column of ragged lines.
        """
        assert "<br>" not in as_document("one line\nand another")


@pytest.mark.parametrize("render", RENDERERS)
class TestWhatTheSanitiserStops:
    """
    The half that is not about looks. Python-Markdown passes raw HTML through and never inspects a
    URL scheme, so everything here reaches the page from a plain `convert` and is stopped by `nh3`.

    Asked of both renderers, because a second converter is a second way onto the page: the guidance
    one draws a file out of whatever repository a session was pointed at.
    """

    def test_a_script_does_not_survive(self, render: Callable[[str], Markup]) -> None:
        assert "<script>" not in render("<script>alert(1)</script>")

    def test_a_javascript_url_does_not_survive(self, render: Callable[[str], Markup]) -> None:
        assert "javascript:" not in render("[x](javascript:alert(1))")

    def test_an_event_handler_does_not_survive(self, render: Callable[[str], Markup]) -> None:
        assert "onerror" not in render('<img src="" onerror="alert(1)">')

    @pytest.mark.parametrize(
        "smuggled",
        [
            "panel",
            "rail",
            "composer",
            "transcript",
            "search__input",
            "dock__btn",
        ],
    )
    def test_a_reply_cannot_paint_itself_as_the_console_s_own_chrome(
        self, render: Callable[[str], Markup], smuggled: str
    ) -> None:
        """
        The reason the allowlist is a closed set of Pygments names rather than the `class`
        attribute. This text is shaped by whatever was pasted into the box and by files an agent
        read: text free to name any class could draw itself as the rail, the composer, or a panel of
        somebody else's kind.
        """
        assert smuggled not in TOKENS, "this test is meaningless if the name is a real token"
        assert f'class="{smuggled}"' not in render(f'<span class="{smuggled}">looks official</span>')


def test_the_allowlist_is_pygments_own_vocabulary_rather_than_a_copy_of_it() -> None:
    """
    Recovered from `STANDARD_TYPES`, so a token a new Pygments release emits is allowed the day
    it appears. A hand-written list would strip it, and strip it silently: that run of code
    would render unhighlighted with nothing saying why.
    """
    assert {"k", "c1", "s2", "nf", "err"} <= TOKENS
