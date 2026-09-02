# What a session's agent can do to something other than talk, and the one door the rest of the
# harness comes through.
#
# A toolset is built per session and bound to what that session may reach, so the surface out of
# here is a *constructor* and the value it needs, and never a tool function directly. That is what
# keeps `agent.py` from knowing that editing is anchored, that anchors are four letters, or that
# there is a worktree root to resolve paths against: it asks for the tools a workspace affords and
# passes them on.
#
# One package per tool, holding however many modules that tool is worth splitting into, and only
# its constructor reaching this far out. A second tool is a new package beside `files/` and one
# more name below, rather than an edit to anything that already imports this one.

from mainplate.tools.bash import bash_tools
from mainplate.tools.files import Files
from mainplate.tools.files import Scratch
from mainplate.tools.files import Worktree
from mainplate.tools.files import file_tools

__all__ = ["Files", "Scratch", "Worktree", "bash_tools", "file_tools"]
