# Finding, reading and editing the files one session may touch, naming lines with anchors.
#
# What leaves here is the constructor, the value it binds to, and the kinds of place that value may
# hold, because a toolset is built per session against that session's own roots. Everything else is
# internal to the two modules below, which say for themselves where the line between them falls; a
# test that wants one of those names imports the module that defines it.
#
# The root kinds are out here rather than internal because whoever builds a session decides what it
# may touch, and that decision is not this package's: a `GitTracked` is files a conversation is about
# and is the only kind git can be asked about, a `Scratch` is what is nobody's repository, and a
# `System` is the whole machine for a session that chose it over a repository. What each
# kind *affords* stays in here, so adding one is a case in `listing` rather than a condition
# somewhere upstream.

from mainplate.tools.files.tools import Files
from mainplate.tools.files.tools import GitTracked
from mainplate.tools.files.tools import Scratch
from mainplate.tools.files.tools import System
from mainplate.tools.files.tools import file_tools

__all__ = ["Files", "GitTracked", "Scratch", "System", "file_tools"]
