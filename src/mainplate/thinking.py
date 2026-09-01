# How hard a model is asked to think, as the closed set of things a person can ask for.
#
# Its own module because three layers need the same vocabulary and none of them should own it: the
# configuration file names a default, a form posts a name, and a page renders the list. `agent.py`
# would be the natural home, except that `profiles.py` has to validate a configured name and
# `agent.py` already imports `profiles.py`, so putting it there is a cycle. What is left is a small
# shared vocabulary, which is what this is.
#
# The effort names are recovered from Pydantic AI's own type rather than restated, so a level it
# adds reaches the picker without an edit here. The three that are not efforts are spelled out
# because they are not efforts, and they are three genuinely different requests rather than
# gradations of one: `None` leaves the setting off the request entirely, `False` asks for thinking
# to be switched off, and `True` asks for it at whatever budget the provider picks. On the
# Anthropic wire today those are no parameter at all, an omitted block, and ten thousand tokens.

from __future__ import annotations

from collections.abc import Mapping
from typing import Final
from typing import get_args

from pydantic_ai.settings import ThinkingEffort
from pydantic_ai.settings import ThinkingLevel

# The level a session starts on with nothing configured, which is the one that says nothing at all
# about thinking. Chosen because it is what every session asked for before this setting existed, so
# adding the knob changes no behaviour until somebody turns it.
DEFAULT_THINKING: Final[str] = "default"

THINKING_CHOICES: Final[tuple[tuple[str, ThinkingLevel | None], ...]] = (
    (DEFAULT_THINKING, None),
    ("off", False),
    ("on", True),
    *((effort, effort) for effort in get_args(ThinkingEffort)),
)

BY_NAME: Final[Mapping[str, ThinkingLevel | None]] = dict(THINKING_CHOICES)

# Keyed by the level rather than the name, for the one direction a rendering needs: a choice read
# back out of a checkpoint holds a level and a page has to call it something.
BY_LEVEL: Final[Mapping[object, str]] = {level: name for name, level in THINKING_CHOICES}

THINKING_NAMES: Final[tuple[str, ...]] = tuple(name for name, _ in THINKING_CHOICES)


class UnknownThinking(ValueError):
    """
    A thinking level that is not one of the ones on offer.

    Its own type so the two boundaries that read one can answer in their own terms: a configured
    name is a startup failure naming the file, and a posted name is a refused form.
    """


def thinking_named(name: str) -> ThinkingLevel | None:
    """A form or config value as the level it names, or a loud failure listing the ones there are."""
    try:
        return BY_NAME[name]
    except KeyError:
        raise UnknownThinking(
            f"{name!r} is not a thinking level; it must be one of: {', '.join(THINKING_NAMES)}"
        ) from None


def name_of_thinking(level: ThinkingLevel | None) -> str:
    """
    What a level is called where somebody reads it, which is the inverse of `thinking_named`.

    Total over what a checkpoint can hold, because `parse_thinking` refuses anything else before a
    value reaches here: every level that parses is one of these names.
    """
    return BY_LEVEL[level]
