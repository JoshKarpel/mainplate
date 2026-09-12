# Design

How each part of mainplate works, and why it is built that way. These are notes for somebody about
to change the code: each one states the mechanism, what it costs, and which alternatives were tried
and are not worth trying again.

Read [the philosophy](../philosophy.md) first. It carries the one idea every page here rests on and
the cross-cutting rules the pages cite rather than restate.

## The pages

- **[Checkpoints](checkpoints.md)** is the key scheme: the two key spaces, what is written under
  each key and by whom, and what a checkpoint value is allowed to be. Start here, because every
  other page reads or writes something described on it.
- **[Endpoints and models](endpoints.md)** is where an answer comes from: what an endpoint, a wire
  and a provider each are, how the model catalogue is discovered, and where a model's price and
  context window are looked up.
- **[What a turn cost](cost.md)** is the money and the clock: how a response is priced, how a
  request is timed, and the line above the message box that says what re-sending the conversation
  will cost.
- **[Forking](forking.md)** is how a session changes its mind, since a choice is fixed for life.
- **[The composer](composer.md)** is everything the box at the bottom of a conversation can do:
  where a message goes, the shelf, `forget`, handoff, steering, and running a command.
- **[The workspace](workspace.md)** is the git side: where a repository is reached from, the
  worktree a session gets, and how a tree is snapshotted at every model request.
- **[How a model reaches a file](tools.md)** is the toolset: which tools a session gets, and the
  content-addressed anchoring scheme behind `read` and `edit`.
- **[Where a command runs](sandbox.md)** is the mount namespace `bash` runs behind, and the two
  isolation axes a session picks.
- **[What runs, and as whom](security.md)** is the boundary between the parent and the sandbox: what
  is untrusted, why nothing trusted may discover its inputs from a tree a session can write, and
  what is deliberately left undefended.
- **[Durability](durability.md)** is the Pydantic AI capability that records a turn step by step,
  and what one pass of a session actually does.
- **[Plugins](plugins.md)** is how somebody adds to this console without editing it: the protocol a
  plugin speaks, the events it is sent, the effects it may ask for, and what has to be true before a
  repository's own plugin runs. Handoff and what a session is told are both plugins, which is what
  makes the pair a test of the protocol rather than two examples of it, and getting a repository
  ready to work in is a third.
- **[The console](console.md)** is the page: the live connection, the transcript, panels and rules,
  and the controls around them.
- **[The stylesheet and the grid](assets.md)** is what draws it: the three shapes, the one value
  that scales the page, and the vendored monospace face box drawing depends on.
- **[Running it as a service](deployment.md)** is the systemd unit `mainplate install` renders, and
  why the service is not itself confined.

What ships *as* plugins rather than as the console, handoff and what a session is told, has [a
section of its own](../plugins/index.md), one page per bundled plugin.

The toolchain around the source, rather than any part of the console, is
[maintaining mainplate](../maintaining.md): the dependency choices, the checks, and this site.

## What is written beside the code instead

These pages carry the *reasoning*. Several directories also carry a short file of their own naming
what a change there must not break, which is a different thing rather than a second copy: the page
says why four lowercase letters, and the file beside `anchors.py` says do not make it three. Those
are delivered to whatever is working in that directory rather than read from here, so nothing on
this site sends you to one.
