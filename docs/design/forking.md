# Forking

A session's choice is fixed for life, so **forking is how it changes**. `Service.fork` copies every
recorded key belonging to a turn before the branch point into a new session, writes a new `choice`,
and records an `Origin` on the row. The tree in the sidebar is emergent from those origins; there is
no tree inside any checkpoint, and a session stays a flat run of turns.

A fork copies what was said, and that is [not the second copy the one idea
refuses](../philosophy.md#a-fork-copies-what-was-said-and-the-turns-it-copies-are-settled).

## What comes across

**`before` needs two rules rather than one, and that is what the inbox costs.** The turn-prefixed
keys come across by *shape*, so a `turn:3:approval:0` nobody has written yet is turn 3 already; an
entry says nothing about which turn it is in, so what decides is where it sits against the entry the
branch point opened on. `branch_at` is that boundary, and it counts against the turns a *page*
counts rather than the ones a pass has opened: a reader forking at turn 3 of a conversation whose
third message is still queued means the message.

**The keys come across unchanged**, which is what makes the copied cursors resolve: a fork appends
nothing, it supplies the parent's own entry keys, so `turn:2:heard:0` still names an entry the
branch holds. The store mints keys that only ever rise, so a message delivered to the branch
afterwards still sorts after everything copied.

Three things there are load-bearing:

- **A turn boundary is the only place a fork can happen**, and it is not a simplification. What a
  fork hands the next model is a conversation with no half-finished exchange in it, and that is only
  true between turns: inside one there is a call awaiting its result, or reasoning signed by the
  model that produced it, and neither survives being handed to another. So only a person's panel
  offers the link.
- **The branch point is *before* the forked turn's message.** That message comes across into an
  editable box and is asked again on the new model, because the usual reason to fork a turn is to
  see it answered differently and a fork that made you retype the question first would be answering
  a different one. Forking the *end* of a conversation has nothing to re-ask and waits instead.
- **The picker starts on the parent's own choice, not the configured default.** A fork is the one
  moment a choice *may* differ and deliberately not the moment it must.

The confirm page is a page rather than a control in the transcript, because the transcript is
re-rendered every time a turn in flight records anything: a picker per person panel would be rebuilt
under the reader's hand, and there would be one per turn.

## Forking the end

A fork at `turns` carries every turn and re-asks none, so the branch opens on an empty box.
The rule under the last turn offers it on an archived session or a completed live end.
Typing into the original continues one conversation; forking preserves that position while two
sessions continue independently, with their own model and settings.
The cost is another checkout and another settings step, rather than changing the original in place.

The files come from the completed boundary, not the repository's head or the last pre-request tree.
Every turn records an ending snapshot after its end plugins return, before its completed messages
are published. Commands run outside model turns and record their own ending snapshots before their
results are published, including commands that exit unsuccessfully: failure does not undo writes.
The archived capture takes precedence where present.

The end control waits until all visible work has an ending capture and no message is queued for a
new turn. It also waits while a command is queued or running. A failed command capture preserves the
output with the failure stated, but does not offer the live end as though its files were recorded.
Historical checkpoints without ending captures retain their pre-request tree behavior for an end
reached by URL; that approximation is not offered as a live end control.

Checkout work is serialized per session across processes. A pass owns the checkout while it runs,
and an opened, unfinished turn keeps commands waiting even between passes. Commands run in inbox
order and own the checkout through their ending capture. Different sessions remain independent.
The cost is that a command cannot inspect or fix files during a model turn; the person's way to
regain them is [Stop](durability.md#stop-is-cooperative-control-input), which finishes the current
request and tool batch, captures the ending state and releases the checkout.

## What a fork does not inherit

- **The base**, which is `settled(forked=True)`. A fork plants at the commit of the turn it
  re-asks, so a base beside that is a second answer to where its files come from. The *branch* is
  the fork page's to ask, and it starts out as the parent's; see [the
  workspace](workspace.md#what-a-forks-checkout-is).
- **What its plugins are set to**, which start on their own declared defaults. A reserve is a
  decision about how much room one conversation's context has left, and a branch's context is not
  that conversation's. See [handing off without being
  asked](../plugins/handoff.md#handing-off-without-being-asked). The *switches* are the other column and are
  inherited: which plugins run is not a fact about one conversation's context, and left behind, a
  branch would set up and execute a program somebody had turned off in the session it branched from.
- **What its plugins *are*, at every tier**, which a fork declares and sets up afresh. Nothing about
  the parent's comes across, so describing them again is how a conversation picks up an edited one.
  See [plugins](plugins.md#setup).
- **Different files.** A fork works in its parent's repository, or with its parent's reach where
  there was none; see [the workspace](workspace.md#a-fork-works-in-its-parents-files).

What it *does* inherit, besides the turns, is the checkout state: a fork's checkout stands on the
commit the forked turn started on, with that turn's uncommitted changes on top, so a branch re-asks
its question against the repository that question was asked about.

## A fork answers the settings step again

**A branch holds a conversation and still owes an answer to that step**, which is the one place a
fork is not simply a session with a past. It carries no declaration, no registration and no press, so
its first pass reads what the tree it is planted at declares, its page draws the step over the turns
it carries, and the press in the branch is what runs `setup`.

That is read off the registration alone rather than off the turn count. What a cached prefix cannot
survive is a plugin set changing under a request already made, and a branch has made none: it holds
*recorded* turns, which is a different thing.

**Editing `.mainplate/` and forking is therefore how a session iterates on its own plugins**,
including [the one that installs its
toolchain](plugins.md#getting-the-repository-ready-is-a-plugin-too) - which a branch has to run again
anyway, since it plants a fresh checkout with a scratch of its own and neither carries what the
parent installed.

**And the press is asked for again rather than inherited**, because a branch is planted at a tree a
model wrote, so what licenses running what it names is the decision to fork plus the confirmation in
the branch. The parent's switches come across as the step's defaults. Why that is the boundary, and
what a snapshot has to do with it, is
[reading it once](plugins.md#read-once-and-never-from-a-tree-this-console-wrote).

The cost, stated: **every fork stops at a screen before it answers anything**, a fork made only to
re-ask one turn included, and its transcript is withheld until it does.

## There is no rewind

**That is settled rather than pending.** `Checkout.restore` existed for one and was deleted unused,
because forking already delivers the whole of what a rewind was for: going back to before turn 3
with the files as they were is `fork(at=3)`, which plants a new checkout at the state recorded in
`turn:3:tree:0` and leaves the original readable beside it.

Putting a session back *in place* would cost two things this console is built on. `Service.token`
counts recorded rows and is sound only because the checkpoint is append-only, "the only way this
moves is a record that did not exist before", and truncation makes the count fall, so rewinding ten
steps to five and then running five more returns it to ten and a live connection polling either side
of that window sends nothing and silently stops updating. And a fork records `Origin(session,
turn)`, so rewinding a parent past a turn some branch left from leaves the sidebar drawing a fork
off a turn that no longer exists. A fork is a copy of an immutable prefix precisely so two sessions
can never disagree; making the prefix mutable is what that rests on.

pi.dev reaches the same place from a different design: its sessions are trees inside one file
(`id`/`parentId`, the active leaf is the position), and even there `/tree` navigation branches
rather than destructively editing a path. Its three operations map onto ours: `/fork` is the link on
a turn's rule, `fork(at=turn)`; `/clone` is the link on an archived session's last rule,
`fork(at=turns)`; and `/tree` is the sidebar, which already draws branches nested under their parent
labelled with the turn they left at.

## Sending back is a disposition, not a merge

Splicing a branch's turns into its parent is the appealing reading and the wrong one. Those turns
were asked against the history at the branch point, so a parent that has advanced would end up
holding request parts whose context never existed, durably and invisibly, because `messages` records
the request as well as the answer.

What comes back is a **message** whose text happens to have been written elsewhere. Nothing is
falsified, `Origin.session` already names where it goes, and there is no merge machinery to write.
See [the `parent` disposition](composer.md#the-disposition).
