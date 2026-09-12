# The workspace

The git side: where a repository is reached from, the worktree a session gets, where in it a session
starts, and how a tree is snapshotted at every model request.

## Where a repository comes from

`forge.py` is the seam, and it exists because there will be more than one answer. A **forge** answers
one question, what repositories can this console reach, and everything below it takes a git
directory without asking how it got there. `ExeDevGitHub` is the one that exists because it is the
one we are on; a plain `GitHub` through an App is a different thing to configure and to trust, so it
will be a second class rather than a flag on this one.

Three things there are decided rather than incidental:

- **`offers()` promises not to raise.** A forge describes an environment this process merely happens
  to be in, so "not on exe.dev" and "nothing attached" are ordinary answers. `discover` logs a forge
  that breaks that promise and carries on with the others, because one forge's mistake is not a
  reason the console cannot start. This is deliberately *unlike* `catalogue.discover`'s refusal; see
  [the philosophy](../philosophy.md#refusing-at-startup-or-promising-not-to-raise) for which stance
  a component takes and why.
- **A repository is one *way of reaching* a repository, not one repository.** `key` is the forge's
  identifier for the attachment and `name` is what a person recognises, because the same repository
  can be attached twice with different rights, one acting as your GitHub user, one as the app, one
  read-only, and those are different things to start a session on. `Reachable.labelled` puts the
  attachment name on a row only where two rows would otherwise read identically.
- **The clone URL is rebuilt on the integration's own hostname**, never the aggregate
  `github.int.exe.xyz` that an integration's `help` prints. The aggregate resolves to whichever
  attachment happens to serve that repository; `<integration>.int.exe.xyz` resolves to exactly one.
  That is what tells two attachments apart, and it is narrower besides: an integration's own host
  answers "Repository not found" for any repository but its own.

`Clones` keeps one **bare** clone per repository, so there is no "main" checkout to confuse with a
session's and every worktree is a linked one off a shared object store. `Workspaces` is the four of
them as one value, clones, the worktree root, the scratch root, and what the forges reach, because
they only mean anything as a set: a worktree is of a clone, a clone is of something a forge reached,
and a scratch is what sits beside a worktree. It is the one place the word "workspaces" still means
anything, naming the storage area rather than a collection of `Worktree`, which is what
`MAINPLATE_WORKSPACES` and `Settings.workspace_root` have always called it.

**The worker clones, never a request handler.** `Service.start` records the choice and returns; the
session's first pass clones the repository and plants the worktree. A clone is a network fetch that
can take minutes and creating a session is a POST somebody is waiting on, so putting it there is
exactly the coupling the control-plane rule argues against. Both halves are idempotent, so every
later pass reaches the same call and does nothing. It is an effect *outside* a step deliberately:
what it does is make a directory exist, which is the same on every pass, so there is no result to
record and nothing for a replay to disagree with.

`FORGES` in `app.py` is the list, declared rather than configured. A forge that needs configuring
will carry its own settings; one that does not needs no switch, because `ExeDevGitHub` reaching
nothing off exe.dev is the correct behaviour there rather than something to turn off.

## A worktree apiece

A session that picked a repository gets **a git worktree of its own**, under `MAINPLATE_WORKSPACES`
(beside the database by default, and never inside any repository, which would put every session's
files in every other session's snapshots). A worktree apiece rather than one shared tree, because
two writers in one directory make a snapshot unattributable and the person is always one of the two.

`Settings.workspace_root` **resolves that path**, and it is not tidying. Everything under it runs
`git` with a `cwd` of its own: a clone is made from the clones root, a worktree is added from the
repository. Hand either a relative destination and git resolves it against *that* directory, so the
clone lands at `workspaces/clones/workspaces/clones/…` and the worktree lands inside the repository.
The idempotence checks then look at the path that was asked for, never find it, and let every pass
try again, which is a `SnapshotFailed` on a session's second turn. The default database is
`mainplate.db` in the working directory and `just serve`/`just demo` name one there too, so a
relative root is the common case rather than the odd one. Resolved at the setting because that is
where a configured path enters the process, and one absolute value cannot be got wrong by the next
consumer; `Clones` and `Worktrees` take an absolute root as a precondition. `test_snapshots.py` goes
the whole way from a `Settings` with a relative database, because the other fixtures there hand both
an absolute root and so would never notice.

There is deliberately **no setting naming a repository**. What a session works in is picked when it
is created and recorded on the session, so a process-wide answer would be a second answer to a
question each session already answers, exactly as a process-wide model would be. A session may
choose *no* repository, which is what this console was before there were any: a place to talk, with
no files.

## Where in it, and on what branch

`Choice.base` is a commit-ish the worktree is checked out at and `Choice.branch` is one started
there; both are recorded with the rest of the choice. A blank base is the repository's default
branch as it stands now.

**A blank branch is not blank: every session working in a repository gets one.** `Choice.branching`
fills it with `mainplate/{session id}` where nobody named one, and that is a correction rather than
the first design. A detached `HEAD` was the default until `Run` put `git commit` in the box below
the conversation, and a commit on a detached `HEAD` is reachable only through the reflog, a way to
lose work that nobody should have to know about, offered by the one control that makes committing
easy. Reading, editing and every question `git` answers are all fine detached; committing is the one
thing that is not, and it is now the thing this console invites.

It is named from the **session id** because `git worktree add -b` refuses a name already in use, so
two sessions on one repository must not collide: a name from the session's *title* would collide the
moment two were opened with the same message, so the id would have to be in it anyway. A name
somebody typed always wins over the generated one.

The cost, stated: one local branch per session in the bare clone, accumulating, with nothing pruning
them, since `Worktrees.uproot` exists and nothing calls it. `git branch --list 'mainplate/*'` is
what finds them, which is what the prefix is for.

It is filled by `Service.start` and `Service.fork` rather than by `Choice.settled`, and that split
is the same one `settled` already makes: `settled` is a rule about a choice on its own, where this
needs the session's id. A fork takes one of its **own** for the same reason it drops its parent's,
that the parent's worktree still holds that name, and dropping without filling would land every fork
on a detached `HEAD`, which is exactly where somebody carries on working.

**They are two questions and not one, because a base cannot check its own branch out.** Git refuses
a branch another worktree already holds, so a session started at `main` and left *on* `main` would
stop the next such session planting at all, and a worktree apiece is the property everything here
rests on. So a base says where to begin and a branch says what to begin, and the words on both
controls have to say so: "leave the worktree detached" read as though the first field answered the
second.

Five things there are decided rather than incidental:

- **`Choice.settled` is what stops the form expressing a contradiction**, and it is one call rather
  than a rule per field. A base and a branch are answers *about* a repository, so with none picked
  all three collapse together. That is the same stance `Isolation.settled` already took and it now
  lives in one place with it, applied by `Service.start`, by `Service.fork`, and by
  `scripts/seed.py`, which is the one writer that is not the service.
- **With no repository the two controls are not drawn at all**, so a page never asks a question the
  session does not have, and the record `settled` would drop is never posted in the first place.
  That is not the greying `workspace_cards` was written to undo, and the difference is that nothing
  is kept in step: which fields exist and which branches complete them are one answer, decided in
  one call from the same `repository`, delivered by the one swap picking a card already makes. The
  block stays as an empty anchor, since it is what the next pick targets. What it costs with
  `mainplate.js` and htmx absent is naming a base by hand: a card cannot then reveal the fields, and
  such a session starts on the repository's default branch under the name this console gives it. The
  completions were always the swap's to deliver, so that page was already the lesser half of this
  control.
- **A fork carries neither**, which is `settled(forked=True)`, and is then given a branch of its
  own. A fork plants at the tree of the turn it re-asks, so a base beside that is a second answer to
  where its files come from; and `git worktree add -b` refuses a branch already in use, so an
  inherited one is a worktree that cannot be planted at all. `Worktrees.plant` ranks its three
  answers, a tree first, then a base, then the default branch, and `settled` is what makes sure it
  is never handed two.
- **Planting a worktree fetches, whether or not a base was named**, and that is where a person says
  when this console's copy of a repository catches up. Nothing else ever refreshes a clone: it is
  made once and would otherwise answer out of whatever the repository looked like the first time
  anybody used it, for as long as the machine lives. Starting a session is both the moment that is
  affordable and the moment somebody wants current code. `Clones.refresh` fetches into
  `refs/remotes/origin/` and never over `refs/heads/`, since a forcing refspec there would walk over
  a branch a session has been committing to.

    **The no-base arm is the one that is easy to get wrong**, and it was wrong first: a fetch writes
    `refs/remotes/origin/` and leaves the clone's own `HEAD` pointing at the stale `refs/heads/`, so
    planting at `rev-parse HEAD` refreshed the refs and then checked out the commit beside them, a
    round trip that changes nothing, which is worse than not making it. `Worktrees.default_branch`
    reads the *name* out of the clone's `HEAD` symref and `resolve` turns that into the current
    commit, so both arms go down one path. `test_snapshots.py` parametrises over naming a base and
    naming nothing for exactly that reason.

    Two cases skip the fetch and both would be round trips that cannot change an answer: a clone
    that has just been made is current by construction, and a fork plants at a recorded tree, which
    is an object this console wrote and already holds.

- **The branches are offered rather than enumerated, and asked of the remote.** Picking a workspace
  card swaps the block under the cards through `/fragments/branches`, which is the shape the model
  group already has under the endpoint cards and for the same reason: what a repository's branches
  are has a different answer per card, so a page that serialized one list would be completing the
  wrong repository's the moment somebody changed their mind. `Clones.branches` runs `git ls-remote`,
  which transfers no objects, so it needs no clone, which is the point, since the very first session
  on a repository is both the case with no clone and the case where saying where to start matters
  most. It promises not to raise, `forge.offers`-style, so an unreachable host costs a suggestion
  rather than an ability.

    Asking the remote is a round trip, so the fields *arrive* rather than appear, and on a cold
    clone that is seconds of a block that has not changed yet: a card pressed and nothing under it,
    which reads as a card that did nothing. So the block draws the same three dots a turn with no
    answer yet draws, as the card's `hx-indicator`. They stand **inside the block being replaced**,
    which is what makes them right rather than a problem: they are shown for exactly as long as the
    thing they stand in for has not arrived, and the swap that ends the request removes them. They
    are hidden by `display` and not by the `opacity` htmx's own indicator rules toggle, which is the
    one place here reaching for `htmx-request` directly - an element hidden by `opacity` still holds
    its row, and a permanent gap above the two fields is a poor price for dots shown for a second.
- **The field is a search over them, and is the only control in the picker that is not cards.**
  Every other question here is a `choosing` group because every other question has a closed set of
  answers; a starting point does not, since a tag, a hash or `main~3` is still typed. So the
  branches are narrowed under the box rather than drawn as cards beside it: cards *are* the answer
  everywhere else, where these only fill in the one answer, and a card posting `base` beside a field
  posting `base` would be two places one value could come from.

    It is rendered **twice** and that is not a copy to keep in step: a `<datalist>`, which is the
    whole of what the field offers with `mainplate.js` absent, and a list the script narrows. Both
    come from one `branches` argument in one call, and exactly one is ever live, because
    `paintBranches` removes the `list` attribute at the moment it takes over, since two dropdowns
    over one box is one more than a reader can use. The narrowing matches anywhere in a name rather
    than at the front, because a branch is called `feature/the-thing` far more often than it is
    called for the word you remember.

    Two things there are decided. The list is `position: absolute`, so typing a letter does not push
    the endpoint and the model down the page. And Enter is swallowed **only** while the reader is
    actually on an entry, because this field's form is the one that starts the session: swallowing
    it whenever the list was open would make the obvious key do nothing on a page whose whole point
    is that form. `TestNarrowingTheBranches` drives all of it in a real Chromium, because the list
    is `hidden` in what the server sends and everything that makes it a search happens after that.

- **The values are refused at the form and re-parsed off the record.** Both become `git` arguments,
  so what they must not be is an *option*: `parse_commitish` and `parse_branch` in `snapshots.py`
  are anchored patterns that refuse a leading `-` along with everything nobody types on purpose.
  Refused rather than dropped at the boundary, because a blank box is somebody taking the default
  where `my branch` is somebody who meant something; and re-parsed on the way out, because a
  checkpoint written by `scripts/seed.py` or edited by hand has been through no boundary at all.

They are recorded as the words somebody typed rather than as what they resolved to, deliberately:
what a session says about itself is the answer it was given, and `main` is a truer record of that
intent than the hash `main` happened to be at that minute. The hash is in `turn:0:tree:0` for
anybody who wants it.

## Snapshots

`snapshots.py` captures a tree through a *shadow index*, so nothing a reader can see moves: not
their staged changes, not `HEAD`, not a branch, not `git log`. Four things there are easy to undo:

- **Git is told which directory is its own, never left to find one.** In a linked worktree `.git` is
  a file holding a pointer, and almost every workspace here is a linked one, so the git directory is
  neither `.git` nor anything derived by reading it: `Worktrees.gitdir` computes
  `<clone>/worktrees/<session>`, `Worktree.git` passes it as `--git-dir` with `--work-tree`, and
  `staging` writes the shadow index there. The worktree is the one directory a session may write, so
  discovering anything out of it is reading a value that session controls, and git configuration
  names programs git runs. [What runs, and as whom](security.md) is why.
- **A fresh index per operation, not one per workspace.** Two concurrent captures over one path
  write over each other, and the loser's `write-tree` then describes a tree that never existed, in
  practice the *empty* tree.
- **Trees are chained into commits under `refs/mainplate/snapshots`.** An unreferenced tree is
  unreachable and `gc` prunes it, so a bare `write-tree` would be a hash that stops resolving later.
- **Capture only where the agent is quiescent**, which means at a model-request boundary and not
  after each tool call. A model can issue several calls in one response and they run at once; while
  they do, `git add -A` walks a tree somebody is still writing to and records a mixture that never
  existed. Between one model request and the next, every tool of the previous batch has returned by
  construction. That is why `Stepping.snapshot` is called from `CheckpointedModel.request` and
  nowhere else: it is the one place in the process that stands at that boundary. A replayed request
  replays its snapshot too, so a later pass runs no git at all and the pair cannot drift.

Snapshots are **gitignore-aware**, deliberately. A tree holds what is version-controlled and nothing
else, so what a fork checks out is the source as that turn saw it and never a `.venv`, a build
directory, or an untracked file holding a secret. It is the contract git already offers, so nobody
has to learn a second one.

## What a fork's worktree is

**A fork's worktree is checked out at the tree the forked turn originally saw**, so a branch re-asks
its question against the files that question was asked about. Planting at the repository's head
instead would ask the new model to redo turn 3 against whatever the disk holds now, which is a
different question wearing the same words and invisible in the transcript.

The mechanism is one extra key rather than a checkout in a request handler: `Service.fork` copies
`turn:{at}:tree:0` across on its own, even though that turn's prompt and messages are *not*
inherited, and the fork's first pass plants at whatever tree is already recorded for the turn it is
about to run. The `:0` is the point: a turn records a tree per model request, and what a fork wants
is the one before the turn did anything.

### A fork may attach a repository and may not swap one

The two look alike and are not. Swapping re-asks a turn against different files, which is a
different question wearing the same words and invisible in the transcript; attaching carries on with
files where there were none, and the turns being inherited were not asked against *other* files,
they were asked against none. So a session in a repository inherits it and its fork page renders no
control, and a session in none is offered the picker. `Service.fork` decides that rather than
trusting what the form posted, which is what stops a form with no repository field quietly moving a
branch out of its repository, the bug that shape of trust actually produced.

[There is no rewind](forking.md#there-is-no-rewind), and that is settled rather than pending.

## What a session takes on disk

**Every directory that is one session's is named in one place, `Places.of`**, and derived from the
session id and the roots the console started with rather than found by looking. The worktree and
git's own directory for it inside the clone, where the session works in a repository; the scratch
and [the plugins' scratches](sandbox.md#the-scratch-directory) either way. Not the clone, which every
session on that repository shares, and not the snapshots, which are objects in the clone's store
under a ref of their own. That last exclusion is what the list is for: what is on it is what taking
a session off the disk removes, and what is not on it is what keeps the checkpoint forkable
afterwards.

It is the one object handed both the workspaces and the plugins root, which the sandbox keeps apart
so a plugin's `$HOME` can never sit under a directory the model writes. Seeing both is not the
confusion that separation prevents: what decides where each namespace's `$HOME` is, is which root it
is *bound*, and this binds nothing. It reads what the others made, and the cost is one more object
that has to be handed the roots at startup rather than deriving them.

**The figure on a row is measured on a timer, not walked when the page is drawn.** A warm walk over
a toolchain came out at about a hundred milliseconds per thirty thousand files on this machine (a
`.venv` of this repository is twenty-eight thousand; a mise directory with a few tools in it is
sixteen thousand), and a session that ran `just setup` into its scratch holds both. The sidebar draws
every session on every page, so a walk per row would put seconds on the request path of a console
with a few working sessions. `footprint.py` walks every session the index knows, in a thread, once
per `measure_every`, and rebinds a holder the page reads by id, the way the catalogue is read. The
cost is that the figure is as old as the interval, and the row says when it was measured rather than
letting a size read as current.

It counts what `du` counts: blocks allocated rather than apparent size, a file linked twice counted
once across the whole set (`uv` links a worktree's venv to the cache in the scratch, and both are
the session's), and a symbolic link counted as itself and never followed, so a link out to the
machine cannot make a session look like the machine. A directory that is not there is nothing, which
is every one of them for a session that has not worked yet, and the page draws nothing for nothing
rather than a zero.

**It is not a column**, and the argument is the [catalogue's](../philosophy.md) rather than the
index's: a reading of the disk that changes under a reader, refreshed by a task that answers no
requests, and not a word of anything said. A column would be a copy of that reading kept in step by
hand, which is the second copy this console is built to refuse, one level down from the checkpoint.

## Archiving

**Archiving a session keeps its conversation and takes its directories away.** The checkpoint stays,
so the session is still readable and still forkable; what goes is everything `Places.of` names, which
is the space a session holds once it is over. It is the answer to a console that has been used for a
while: every session ever started holds a worktree and a scratch, and the one thing a finished
session needs from the disk is nothing.

**The press records a fact and a reconciler acts on it.** `Service.archive` writes one key,
`archived`, and redirects; from that moment [the page has no message box](console.md#the-message-box),
the transcript says why, the rail's card says when, the row is muted, and the routes that would
write to the session answer `422`. The press is offered in three places, the rail's card, under the
settings step, and [on the session's row in the list](console.md#the-session-list), and all three
are one disclosure over one form. Taking
the directories away is `archive.py`'s, on a timer: each round reads the key off every row, finds the
sessions still holding something on disk, and takes it off. A reconciler rather than a job the press
queues, because what it does is diff a desired state against an actual one and converge, so a
console that died halfway through, or was pressed while a pass still held the session, finishes on
its next round with nothing to be told. The cost, stated: a session pressed archived keeps its files
for up to `archive_every`, and a console with nothing to do reads its index once a minute.

**It refuses to take a worktree from under a pass.** A pass reads the checkpoint at its top and never
sees a key written after it started, so a session the worker holds is left for the next round, and
the pass that follows reads the key at its own top and stops - `Archived` is its own arm of `Ended`,
so the log says a session was closed rather than that one stalled. A command a person is still
running is the same case from the other side. Both are read off live state, since both are true only
at the instant they are read.

**The worktree's last tree is captured on the way out**, under `archived:tree`, because the press
cannot know it: files may still be being written when the button goes down, and the reconciler waits
until nothing holds the session. It is what a fork from the end of an archived session plants at, so
the branch carries on with the files the conversation actually ended with, snapshots the worktree
never captured included - what a person ran in it after the last request, and what a plugin fixed
at the turn's end. The worktree goes through `git worktree remove` rather than `rmtree`, since git
keeps its own directory for a linked worktree inside the clone and its own list of them; the
snapshots live in the clone's object store under a ref of their own and outlive the worktree, which
is what keeps every earlier fork point reachable too.

**Nothing un-archives a session, and that is the design rather than a gap.** The key is write-once,
and what a person wants back is the conversation with somewhere to work, which is exactly what
[forking from the end](forking.md#forking-the-end) is: a live session carrying every turn, with a
fresh worktree at the archived tree and a scratch of its own. Putting the archived session itself
back would mean reconstructing a scratch that was deliberately not snapshotted, which is a second
mechanism to keep for a state a fork already reaches.
