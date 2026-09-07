# What a session's plugins are set to, which is the one thing about a session that changes.
#
# A module of its own for the reason it always had, which is a cycle: the columns live on the
# session index and the values are read inside a pass, so `sessions.py` and `conversation.py` both
# want this, and `sessions.py` already reads `conversation.py` for the key scheme. What is left here
# is a small shared vocabulary and the readings over it, which belong to neither of them.
#
# Against `Choice`, which is recorded before the first message and fixed for the session's life:
# that is what a session *is*, and forking is how it changes. This is what is being done *to* a
# running session, so it has to be changeable while the session runs or it is not a setting at all.
#
# **Two mappings rather than one, because they have two owners.** `settings` is each plugin's own,
# declared by its card and read by nothing but that plugin; `enabled` is the console's decision
# about which plugins a session runs at all. Held together they would need a reserved field name no
# plugin could use, which is a rule somebody has to know rather than a shape that cannot be got
# wrong.

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from typing import Final

from mainplate.plugins.protocol import Setting

ENABLED_FIELD: Final = "on"
"""
What the switch turning a plugin on is called on the form that sets it.

One word, on the settings step and in the boundary that parses it. It is not a settings *field*, so
it can share a name with one a plugin declares without anything having to be reserved: the two
arrive on different forms and are read by different routes.
"""

PLUGIN_FIELD: Final = "plugin"
"""Which plugin a posted control belongs to, by qualified name."""

CONTROL_FIELD: Final = "control"
"""Which of that plugin's controls was pressed, by the name its card declared."""


@dataclass(frozen=True, slots=True)
class Tending:
    """
    What this console is doing for a session unasked, as the two mappings behind it.

    Both are keyed by a plugin's **qualified** name, so three plugins called `guidance` hold three
    sets of settings rather than one they take turns overwriting.

    Absent is not off and absent is not the default: absent is *nobody has said*, and what that means
    is the plugin's own answer. A switch nobody has touched follows the name stack, and a setting
    nobody has touched follows the control's declared default. That is what keeps a moved default
    reaching every session nobody has told otherwise.
    """

    enabled: Mapping[str, bool] = field(default_factory=dict)
    settings: Mapping[str, Mapping[str, object]] = field(default_factory=dict)

    def on(self, qualified: str, default: bool) -> bool:
        """Whether this session runs this plugin, with the name stack's answer where nobody has said."""
        held = self.enabled.get(qualified)
        return default if held is None else held

    def of(self, qualified: str) -> Mapping[str, object]:
        """Whatever this session has stored for one plugin, before its card has been consulted."""
        held = self.settings.get(qualified)
        return held if held is not None else {}

    def switched(self, qualified: str, on: bool) -> Tending:
        return replace(self, enabled={**self.enabled, qualified: on})

    def setting(self, qualified: str, values: Mapping[str, Setting]) -> Tending:
        return replace(self, settings={**self.settings, qualified: {**self.of(qualified), **values}})


TENDED: Final = Tending(enabled={}, settings={})
"""
A session nobody has told anything, which is what every session starts as.

Named because three places need the same one and none of them can construct it in a default
argument: `Session.tending` wants a factory, `Service.start` compares against it to decide whether a
column is worth writing, and a fork settles this afresh rather than inheriting it.
"""


def parse_tending(enabled: str | None, settings: str | None) -> Tending:
    """
    A session's own settings, out of the two columns holding them.

    **This is where `STRICT` stopped covering it.** Both columns are unchecked text, so the shape is
    established here: anything that is not a mapping of the right shape reads as nobody having said,
    which is what a hand-edited row deserves and is a better answer than a session that will not run.

    Each independently, because they are two settings and a session that has been told one of them
    and not the other is an ordinary state.
    """
    return Tending(enabled=parse_enabled(enabled), settings=parse_settings(settings))


def parse_enabled(raw: str | None) -> Mapping[str, bool]:
    return {name: value for name, value in mapping_in(raw).items() if isinstance(value, bool)}


def parse_settings(raw: str | None) -> Mapping[str, Mapping[str, object]]:
    return {name: value for name, value in mapping_in(raw).items() if isinstance(value, dict)}


def mapping_in(raw: str | None) -> Mapping[str, object]:
    """One column's JSON as the mapping it holds, with anything else reading as nothing said."""
    if not raw:
        return {}
    try:
        held = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return held if isinstance(held, dict) else {}


def written(held: Mapping[str, object]) -> str:
    """One mapping as the text a column takes, which is the codec every other value here uses."""
    return json.dumps(dict(held))
