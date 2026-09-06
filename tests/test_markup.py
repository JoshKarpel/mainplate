from __future__ import annotations

from collections.abc import Callable

import pytest
from markupsafe import Markup

from mainplate.markup import HIGHLIGHT
from mainplate.markup import TOKENS
from mainplate.markup import as_document
from mainplate.markup import as_message

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
