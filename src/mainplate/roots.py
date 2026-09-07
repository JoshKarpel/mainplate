# What a bound place is called, in the one vocabulary the file tools and the sandbox both read.
#
# A model reaches the same directory two ways - by naming it to `read`, `edit` or `create`, and
# through an environment variable inside `bash` - and those are two surfaces of one answer. Written
# separately they are two lists to keep in step, and the failure is quiet: the instructions would say
# `scratch` while a command found `MAINPLATE_SCRATCHPAD` and nothing would report it. This is the same
# move `StepKind` makes for the key builders and `thinking.py` makes for a vocabulary three layers
# read, and it lives in a module of its own for the same reason `thinking.py` does: the file tools
# know nothing about sandboxes and the sandbox knows nothing about `Files`, so neither can own it.
#
# Most of these are a *session's* places and reach a model both ways. `plugin_scratch` is neither: it
# is one plugin's own directory, named only in a namespace no model is inside. It is here all the same,
# because what the list is for is that two places never answer to one word, and that is a question
# about every name this console binds rather than only the ones a model may type.

from __future__ import annotations

from typing import Literal

type RootName = Literal["worktree", "scratch", "machine", "plugin_scratch"]
"""
The name a place answers to, which is the word on the tool argument and in the environment.

Named after what the place *is* rather than after the session's own id, which is the whole point: a
worktree is at `/var/lib/mainplate/worktrees/<32 hex characters>` and a model that has to reproduce
that from memory eventually reproduces it wrong, which is a refusal it then has to recover from.

`plugin_scratch` is the one arm no file tool returns, because it is nobody's place but the plugin's:
what a model reaches under `scratch` is the session's own, and the two are different directories on
purpose. It is here rather than named in `running.py` so that the console has one derivation of what
a place is called in an environment, and so that the collision is visible - `scratch` meaning two
directories depending on whose process is reading it is exactly what this vocabulary exists to stop.
"""


def environment_named(name: RootName) -> str:
    """
    What one root is called in a command's environment, derived rather than written down twice.

    Prefixed because a command's environment is `--clearenv`'d and then built by this console, so
    everything in it is ours and should say so rather than colliding with whatever a build script
    expects to find.
    """
    return f"MAINPLATE_{name.upper()}"
