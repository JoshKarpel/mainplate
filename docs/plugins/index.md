# Bundled plugins

The plugins this console ships with, one page apiece. [Plugins](../design/plugins.md) is the
protocol they speak and the reasons it is shaped as it is; these pages are what each bundled plugin
does with it, and why.

**A bundled plugin is not privileged.** Each is an executable in `src/mainplate/plugins/bundled/`
that the console speaks to over a pipe exactly as it speaks to anybody else's, and nothing in one
imports `mainplate`. That is the point rather than an inconvenience: what ships is a worked example
rather than a path no third-party plugin can reach, and if ours could not be turned off in favour of
somebody else's, the protocol would be a description of what we happened to build rather than
something anybody can build against.

**Bundled means default, not fixed.** Every one of these is drawn on [the settings
step](../design/plugins.md#starting-a-session-takes-four-steps) with a switch, so somebody who
writes their own `guidance` installs it beside ours in `config.yaml` and turns ours off with one
press. The two are separate plugins with separate settings, and nothing is replaced out of view.

- **[Handoff](handoff.md)** is a forget whose message the session wrote itself: a tool the model
  calls with a document, a message delivered back carrying a boundary, and a reserve that asks for
  one unprompted. It is the plugin the protocol was read off.
- **[What a session is told](guidance.md)** is the instructions: the operator's own guidance, a
  repository's `AGENTS.md`, an index of the guidance elsewhere in it, and the nested guidance handed
  over as the model reaches into a directory.

Between them the pair exercises nearly every event, effect and contribution the protocol has, which
is [what makes them a test of it rather than two examples](
../design/plugins.md#both-are-ported-and-that-is-the-test).

**This repository carries two more worked examples that are not bundled**, both repository-tier and
both declared by `.mainplate/mainplate.yaml`. `.mainplate/pre-commit` runs this project's own hooks
over what a session has changed whenever the model tries to stop, and sends it back with what is
still failing; `.mainplate/setup` fetches the toolchain a session needs to run those hooks at all,
and prints nothing. Between them they are [the examples to copy](
../design/plugins.md#and-this-repository-carries-two-which-is-the-rest-of-the-proof): one installs
into its own scratch and answers events for the rest of the session, the other installs into the
session's and is never asked anything again.
