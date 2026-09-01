from __future__ import annotations

import pytest

from mainplate.markup import HIGHLIGHT
from mainplate.markup import TOKENS
from mainplate.markup import as_markup

PYTHON = """```python
async def handler(session: str) -> int:
    # a comment
    return 42
```"""


class TestWhatReachesThePage:
    def test_prose_is_markdown(self) -> None:
        assert "<strong>bold</strong>" in as_markup("**bold**")

    def test_a_fenced_block_is_highlighted_into_token_classes(self) -> None:
        rendered = as_markup(PYTHON)
        assert f'class="{HIGHLIGHT}"' in rendered
        assert 'class="k"' in rendered, "keywords carry a token class"
        assert 'class="c1"' in rendered, "comments carry a token class"

    def test_an_unlabelled_fence_is_left_alone_rather_than_guessed_at(self) -> None:
        """
        A guess is wrong often enough on a short snippet to be worth not making, and a wrong guess
        colours the text by a grammar it is not written in.
        """
        rendered = as_markup("```\njust some text\n```")
        assert 'class="k"' not in rendered

    def test_a_language_nobody_has_heard_of_renders_rather_than_raising(self) -> None:
        """The fence's label is written by a model, so an unknown one must not be an exception."""
        assert "gibberish here" in as_markup("```notalanguage\ngibberish here\n```")


class TestWhatTheSanitiserStops:
    """
    The half that is not about looks. Python-Markdown passes raw HTML through and never inspects a
    URL scheme, so everything here reaches the page from a plain `convert` and is stopped by `nh3`.
    """

    def test_a_script_does_not_survive(self) -> None:
        assert "<script>" not in as_markup("<script>alert(1)</script>")

    def test_a_javascript_url_does_not_survive(self) -> None:
        assert "javascript:" not in as_markup("[x](javascript:alert(1))")

    def test_an_event_handler_does_not_survive(self) -> None:
        assert "onerror" not in as_markup('<img src="" onerror="alert(1)">')

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
    def test_a_reply_cannot_paint_itself_as_the_console_s_own_chrome(self, smuggled: str) -> None:
        """
        The reason the allowlist is a closed set of Pygments names rather than the `class`
        attribute. This text is shaped by whatever was pasted into the box and, before long, by
        files an agent read: a reply free to name any class could draw itself as the rail, the
        composer, or a panel of somebody else's kind.
        """
        assert smuggled not in TOKENS, "this test is meaningless if the name is a real token"
        assert f'class="{smuggled}"' not in as_markup(f'<span class="{smuggled}">looks official</span>')

    def test_the_allowlist_is_pygments_own_vocabulary_rather_than_a_copy_of_it(self) -> None:
        """
        Recovered from `STANDARD_TYPES`, so a token a new Pygments release emits is allowed the day
        it appears. A hand-written list would strip it, and strip it silently: that run of code
        would render unhighlighted with nothing saying why.
        """
        assert {"k", "c1", "s2", "nf", "err"} <= TOKENS
