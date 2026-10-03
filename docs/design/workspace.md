# The workspace

The git side: where a repository is reached from, the checkout a session gets and what it is made
of, where in it a session starts, and how a tree is snapshotted at every model request.

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

`Clones` keeps one **bare** clone per repository, the **store**, so there is no "main" checkout to
confuse with a session's and every session's checkout borrows one set of objects. `Workspaces` is
the five of them as one value, the stores, the checkout root, the scratch root, what the forges
reach, and the `bwrap` every git against a checkout runs behind, because they only mean anything as
a set: a checkout is of a store, a store is of something a forge reached, a scratch is what sits
beside a checkout, and a checkout without a sandbox is one nothing may safely run git in. It is the
one place the word "workspaces" means anything, naming the storage area rather than a collection of
`Checkout`, which is what `MAINPLATE_WORKSPACES` and `Settings.workspace_root` call it.

**The worker clones, never a request handler.** `Service.start` records the choice and returns; the
session's first pass clones the repository and plants the checkout. A clone is a network fetch that
can take minutes and creating a session is a POST somebody is waiting on, so putting it there is
exactly the coupling the control-plane rule argues against. Both halves are idempotent, so every
later pass reaches the same call and does nothing. It is an effect *outside* a step deliberately:
what it does is make a directory exist, which is the same on every pass, so there is no result to
record and nothing for a replay to disagree with.

`FORGES` in `app.py` is the list, declared rather than configured. A forge that needs configuring
will carry its own settings; one that does not needs no switch, because `ExeDevGitHub` reaching
nothing off exe.dev is the correct behaviour there rather than something to turn off.

## A checkout apiece

A session that picked a repository gets **a checkout of its own**, under `MAINPLATE_WORKSPACES`
(beside the database by default, and never inside any repository, which would put every session's
files in every other session's snapshots). A checkout apiece rather than one shared tree, because
two writers in one directory make a snapshot unattributable and the person is always one of the two.

**It is a complete repository, `.git` and all**, rather than a linked checkout of the store. Its
refs, its index, its reflog and its configuration are its own, so the session's `bash` commits,
rebases and stashes the way git does anywhere, and nothing it does reaches another session. Three
things make that cheap and keep it apart:

- **It borrows the store's objects** through `objects/info/alternates` rather than copying them, so
  a checkout costs its files rather than a clone. The store is bound read-only wherever the
  checkout is, so the borrowing cannot run the other way; a hardlinked copy would have let a session
  `chmod` its way into the store's own object files.
- **`origin` is the store**, with the refspec `git clone` would have written, since the store's
  branches are the remote's (below). Every way of fetching a model reaches for, `git fetch`, `git
  fetch origin main`, `git pull`, brings the repository's current branches with no network and no
  credential, and nothing can be pushed there.
- **It is built beside where it goes and renamed into place**, with the operator's `user.name` and
  `user.email`, read once at startup, copied in so a commit carries the name the person pushing it
  would give it. A crash part-way leaves a directory nothing names rather than a checkout half made.
  Every git that builds it runs in the parent, which is safe for exactly as long as it takes: until
  the rename, no session has had a moment to write its configuration. After it, [no git in the
  parent touches it](security.md#the-parent-never-runs-git-against-a-checkout).

**The store never prunes.** A checkout borrows objects without the store knowing which, so a commit
a session merged from a branch the remote has since deleted is one only that checkout refers to.
`Clones.refresh` sets `gc.pruneExpire` to `never` before every fetch, because a fetch is what drops
the store's last ref to such a commit and then runs git's automatic `gc`; so every store gets it at
its next fetch, whenever it was cloned, before anything that fetch drops can be pruned. The cost,
stated: a store only grows.

**Whether a checkout is planted is read from its directory, never from its `.git`.** Everything
under the directory is the session's to delete or replace, `.git` included, so an answer read from
there is one the session chose, and a session that ran `rm -rf .git` would make every later pass
try to plant over its files. The directory itself is not the session's: planting puts it in place
with one rename, and a sandbox cannot remove it because it is the mount point the checkout is bound
at. So planting stays a no-op for a session that has broken its own git.

**What fails instead is the next capture, and the pass with it.** A capture is `git add -A` inside
the sandbox, which finds no repository and raises `SnapshotFailed` naming the command, so the pass
falls over and [the page says why](durability.md). Nothing repairs it: the files are still there, and
the session's own `bash` can `git init` or re-clone into place, but the base a capture is thin
against is a ref in the store, so what comes back has to hold that commit for a capture to succeed.
The cost, stated: a model "reinitialising" its repository stops its own session until somebody
puts the history back or forks from the last turn that recorded a tree.

`Settings.workspace_root` **resolves that path**, and it is not tidying. Everything under it runs
`git` with a `cwd` of its own: a store is cloned from the clones root, a checkout is initialised
from the checkout root. Hand either a relative destination and git resolves it against *that*
directory, so the store lands at `workspaces/clones/workspaces/clones/…` and the checkout lands
somewhere nobody asked for. The idempotence checks then look at the path that was asked for, never
find it, and let every pass try again, which is a `SnapshotFailed` on a session's second turn. The
default database is `mainplate.db` in the working directory and `just serve`/`just demo` name one
there too, so a relative root is the common case rather than the odd one. Resolved at the setting
because that is where a configured path enters the process, and one absolute value cannot be got
wrong by the next consumer; `Clones` and `Checkouts` take an absolute root as a precondition.
`test_snapshots.py` goes the whole way from a `Settings` with a relative database, because the other
fixtures there hand both an absolute root and so would never notice.

There is deliberately **no setting naming a repository**. What a session works in is picked when it
is created and recorded on the session, so a process-wide answer would be a second answer to a
question each session already answers, exactly as a process-wide model would be. A session may
choose *no* repository, which is what this console was before there were any: a place to talk, with
no files.

## Where in it, and on what branch

`Choice.base` is a commit-ish the checkout is planted at and `Choice.branch` is one started
there; both are recorded with the rest of the choice. A blank base is the repository's default
branch as it stands now.

**A blank branch is not blank: every session working in a repository gets one.** `Choice.branching`
fills it with `mainplate/{session id}` where nobody named one, and that is a correction rather than
the first design. A detached `HEAD` was the default until `Run` put `git commit` in the box below
the conversation, and a commit on a detached `HEAD` is reachable only through the reflog, a way to
lose work that nobody should have to know about, offered by the one control that makes committing
easy. Reading, editing and every question `git` answers are all fine detached; committing is the one
thing that is not, and it is now the thing this console invites.

It is named from the **session id** because [a push](composer.md#push) sends it to the repository
under the same name, so two sessions on one repository must not collide there: a name from the
session's *title* would collide the moment two were opened with the same message, so the id would
have to be in it anyway. A name somebody typed always wins over the generated one. `git branch
--list 'mainplate/*'` on the remote is what finds the pushed ones, which is what the prefix is for.

It is filled by `Service.start` and `Service.fork` rather than by `Choice.settled`, and that split
is the same one `settled` already makes: `settled` is a rule about a choice on its own, where this
needs the session's id. A fork is offered its parent's branch, and takes one of its **own** where
that box was emptied, since leaving it unfilled would put the fork on a detached `HEAD`, which is
exactly where somebody carries on working. A fork that keeps the parent's name is the one case this
uniqueness does not cover; the fork's own bullet below says what that costs.

**They are two questions and not one, because where work begins is not where it goes.** A session
started at `main` and left *on* `main` is one whose push lands on `main`, which is rarely what
naming a starting point meant. So a base says where to begin and a branch says what to begin, and
the words on both controls have to say so: "leave the checkout detached" read as though the first
field answered the second.

Five things there are decided rather than incidental:

- **`Choice.settled` is what stops the form expressing a contradiction**, and it is one call rather
  than a rule per field. A base and a branch are answers *about* a repository, so with none picked
  all three collapse together. That is the same stance `Isolation.settled` already took and it now
  lives in one place with it, applied by `Service.start`, by `Service.fork`, and by
  `scripts/seed.py`, which is the one writer that is not the service.
- **With no repository the two controls are not drawn at all**, so a page never asks a question the
  session does not have, and the record `settled` would drop is never posted in the first place.
  Which fields exist and which branches complete them are one answer, decided in `starting_at` from
  the same `repository`. With `mainplate.js` and htmx absent the fields are still drawn, with no
  completions, so what is given up is only the list of what the repository has.
- **A fork carries no base, and its branch is the one its fork page posted.** A fork plants at the
  commit its parent recorded for the turn it re-asks, so a base beside that is a second answer to
  where its files come from, and `settled(forked=True)` drops one. The branch is a different
  question - what to call that commit - so it stays: the page offers the branch the parent was on,
  and an emptied box is a branch of the fork's own. `Checkouts.plant` ranks its three answers, a
  recorded snapshot first, then a base, then the default branch, and `settled` is what makes sure it
  is never handed two. The cost of the pre-filled name, stated: the fork and its parent are two
  checkouts under one branch, so nothing collides until both push, and then the second is refused,
  since a push is never forced.
- **The store is fetched on a timer while any session works in it, faster while one is being worked
  in, and again when one is planted.** A session's own `git fetch` reads the store and not the forge,
  since `origin` in a checkout is the store, which needs no network and no credential; so the store
  is the whole of how current `origin/main` is in there. `fetching.py` fetches every repository an
  unarchived session works in, off the request path: every `fetch_held_every` (fifteen seconds)
  while a pass or a command holds one of its sessions, and every `fetch_every` (five minutes)
  otherwise. Held is the moment that matters, because a held session is the one about to read
  `origin/main`: a model told to merge the latest `main` fetches seconds into its turn. The loop
  reads which sessions are held off the same live state the reconciler does, rather than being told
  when work starts, so the worker and the command runner know nothing about it; the cost of that is
  that a session waking from an idle stretch is noticed within one short interval rather than at
  once, and a `git fetch` inside that interval gets a copy up to five minutes old. Planting fetches
  as well, because a new session is the moment somebody is waiting on current code. A failed round
  is logged and changes nothing, since the store keeps the refs it last fetched. The cost, stated: a
  round trip every fifteen seconds per repository somebody is working in, whether or not anything
  moved.

    **`Clones.refresh` mirrors the remote's branches over the store's own `refs/heads/`**, so the
    store's `main` is the remote's `main` as of the last fetch, its `HEAD` names it, and a session's
    checkout can carry the stock refspec. Nothing else writes a branch in a store: a session's work
    arrives as a bundle under `refs/mainplate/sessions/`, and a push goes from the store straight to
    the remote. The cost, stated: `--prune` takes a branch the remote deleted out of the store too.

    Fetching *beside* the clone's branches, into `refs/remotes/origin/`, is the design that reads
    as the careful one, since it overwrites nothing, and it is the one to not go back to. It leaves
    two commits answering to every branch name, one current and one frozen at the clone, and git
    resolves a name to the frozen one everywhere it is not told otherwise: the store's `HEAD`
    planted new sessions on a `main` as old as the clone until the no-base arm was taught to look
    the other way, and a session's `git fetch origin main` fetched the frozen `main` and merged a
    copy a day stale, inside a sandbox where nothing of this console's could intervene.
    `test_snapshots.py` still parametrises planting over naming a base and naming nothing, and
    `test_fetching.py` fetches by name from inside a checkout, because those are the two readers
    that found the frozen copy.

    Two cases skip the fetch and both would be round trips that cannot change an answer: a store
    that has just been cloned is current by construction, and a fork plants at a recorded
    snapshot, whose tree and commit a capture already carried into the store. A fork of a parent
    on an orphan branch recorded no commit and stands on the default branch like a new session, so
    it fetches like one.

- **The branches are offered rather than enumerated, and asked of the remote.** The new-session page
  asks for them once it has arrived, swapping the block under its heading through
  `/fragments/branches`, so the page is drawn without waiting on the forge. `Clones.branches` runs `git ls-remote`,
  which transfers no objects, so it needs no store, which is the point, since the very first session
  on a repository is both the case with no store and the case where saying where to start matters
  most. It promises not to raise, `forge.offers`-style, so an unreachable host costs a suggestion
  rather than an ability.

    Asking the remote is a round trip, so the completions *arrive* rather than appear, and on a slow
    remote that is seconds of a field with nothing under it, which reads as a repository with no
    branches. So the block draws the same three dots a turn with no answer yet draws, as the
    request's `hx-indicator`. They stand **inside the block being replaced**,
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

A snapshot is a checkout's whole git state before one model request: the **tree** its files make,
uncommitted changes and untracked files included, the **commit** `HEAD` names, and the **branch**
it is on. `snapshots.py` captures the tree through a *shadow index*, so nothing a reader can see
moves: not their staged changes, not `HEAD`, not a branch, not `git log`. The tree and the commit go
into the **store** rather than staying in the checkout, which is what lets them outlive the checkout
and what a fork plants from.

**A snapshot is never something a person handles.** It is how this console puts a session's
repository back for a fork, and nothing else: no page prints a snapshot's hash, and no checkout's
git names one, since the chain lives under refs in the store that no checkout fetches. What a
person sees of one is a fork standing on the commit, the branch and the uncommitted files its
parent had, which are all things they already know the names of. That is the test for anything
new built on a snapshot: if it would show a person a tree or a chain link, it is exposing the
mechanism rather than the work. What it costs is a hash a person could have copied to go back to a
turn by hand, and `fork` already goes back to a turn.

Six things there are easy to undo:

- **Git against the checkout runs in its sandbox, and only a bundle comes back.** `add -A`,
  `write-tree` and the two questions about `HEAD` run behind `bwrap` through `Checkout.git`. Where
  the store does not already hold that tree or that commit, the sandbox commits the tree onto
  `HEAD` (onto the session's base, on an orphan branch with no commit yet) and bundles it, excluding
  the base, the session's last snapshot, a `HEAD` the store already holds and every branch the store
  last fetched, so what crosses is what the session wrote and committed on top of those, and a
  session that rebased onto `origin/main` does not send `main` back; the store fetches the bundle,
  reads the tree and the commit under it back out of itself and refuses either one the checkout
  misreported. The branch is the one thing taken on the checkout's word, because it is a name and
  not an object: it is read as the full `refs/heads/` ref, since a short name is `heads/v1` wherever
  a tag is also called `v1`, goes through `parse_branch`, and only ever pre-fills a box on the fork
  page. An untouched checkout, or a fork
  nobody has changed yet, sends nothing. The checkout is the one directory a session may write and
  its configuration names programs git runs, so
  [the parent reads none of it](security.md#the-parent-never-runs-git-against-a-checkout). The cost,
  stated: a capture that changed something is a few more processes than a `write-tree` in place.
- **Stopped part-way, a git in the parent is terminated and one in the sandbox is killed.** Git
  removes the lock files it holds on `SIGTERM` and leaves them on `SIGKILL`, and a lock left in the
  store fails every later write to that ref with nothing able to reach the store to delete it; so
  `git_at` asks first and kills only after a grace. `bwrap` passes no signal on, so asking would not
  reach the git inside, and what a killed one can leave is a lock in the checkout, which the
  session's own `bash` can remove.
- **A fresh index per operation, not one per workspace.** It lives in a directory made for that one
  capture, never in `.git`. Two concurrent captures over one path write over each other, and the
  loser's `write-tree` then describes a tree that never existed, in practice the *empty* tree.
- **Snapshots are chained into commits in the store, under `refs/mainplate/sessions/<id>/snapshots`.**
  An unreferenced tree is unreachable and `gc` prunes it, so a bare `write-tree` would be a hash that
  stops resolving later; and a commit the session never pushed is in its checkout and nowhere else.
  So each link holds the tree and has the commit `HEAD` named as its second parent, which keeps both
  alive through the one ref. A link is new wherever either moved, since a commit that changed no
  file still moves `HEAD`. The chain is extended with the tip it read as the old value, so two
  captures racing each other both land. The session's base is `refs/mainplate/sessions/<id>/base`
  beside it, which keeps that commit reachable and is what every bundle is thin against. The cost,
  stated: the chain keeps every commit a session ever stood on, including the ones a rebase then
  abandoned.
- **A diff is asked of the store.** Both trees are objects there, and the store's configuration is
  this console's, so no diff driver a session named runs to draw a batch's change.
- **Capture only where the agent is quiescent**, which means at a model-request boundary and not
  after each tool call. A model can issue several calls in one response and they run at once; while
  they do, `git add -A` walks a tree somebody is still writing to and records a mixture that never
  existed. Between one model request and the next, every tool of the previous batch has returned by
  construction. That is why `Stepping.snapshot` is called from `Stepping.request` and
  nowhere else: it is the one place in the process that stands at that boundary. A replayed request
  replays its snapshot too, so a later pass runs no git at all and the pair cannot drift.

Snapshots are **gitignore-aware**, deliberately. A tree holds what is version-controlled and nothing
else, so what a fork checks out is the source as that turn saw it and never a `.venv`, a build
directory, or an untracked file holding a secret. It is the contract git already offers, so nobody
has to learn a second one.

## What a fork's checkout is

**A fork's checkout is its parent's git state as the forked turn began**: the same commit, so the
same history under it, with the parent's uncommitted edits, deletions and untracked files on top,
unstaged. So a branch re-asks its question against the repository that question was asked about.
Planting at the repository's head instead would ask the new model to redo turn 3 against whatever
the disk holds now, which is a different question wearing the same words and invisible in the
transcript. And planting the files alone, as a commit of their own, would hand the fork a history
with nothing in common with its parent's, and the first `git merge origin/main` in it is refused as
unrelated.

`Checkouts.plant` checks the recorded commit out, then lays the recorded tree over it with
`read-tree -u` and puts the index back with `reset`, so the fork's first `git status` reads as its
parent's did. A parent on an orphan branch recorded no commit, and its fork stands on the default
branch with the files laid over that instead. The cost, stated: whatever the parent had staged comes
back unstaged, since the snapshot's index was its own and not the session's.

**The branch is the person's to pick.** The fork page's box starts out holding the branch the
parent was on then, so carrying the work on under its own name needs nothing typed, and an emptied
box gives the fork one of its own. `fork_branch` reads it out of the same record the fork will be
planted at, through `fork_point`, so the name offered is the one that commit was under.

The mechanism is one extra key rather than planting a checkout in a request handler:
`Service.fork` copies `turn:{at}:tree:0` across on its own, even though that turn's prompt and
messages are *not* inherited, and the fork's first pass plants at whatever state is already
recorded for the turn it is about to run. The `:0` is the point: a turn records a snapshot per model
request, and what a fork wants is the one before the turn did anything.

### A fork works in its parent's files

A fork works in its parent's repository, or with the same reach of the machine where the parent had
none, and its fork page draws no checkout control. Changing them would re-ask the carried turns
against different files, which is a different question wearing the same words and invisible in the
transcript. That holds for a parent with no files too: going to work in a repository after talking
something through with none is a new session, where where to start can be asked. The cost, stated:
what that conversation said comes across by hand. `Service.fork` decides all of it rather than
trusting what the form posted, which is what stops a form with no repository field quietly moving a
branch out of its repository, the bug that shape of trust actually produced.

[There is no rewind](forking.md#there-is-no-rewind), and that is settled rather than pending.

## What a session takes on disk

**Every directory that is one session's is named in one place, `Places.of`**, and derived from the
session id and the roots the console started with rather than found by looking. The checkout, `.git`
and all, where the session works in a repository; the scratch and [the plugins'
scratches](sandbox.md#the-scratch-directory) either way. Not the store, which every session on that
repository shares, and not the snapshots, which are objects in the store under refs of their own.
That last exclusion is what the list is for: what is on it is what taking a session off the disk
removes, and what is not on it is what keeps the checkpoint forkable afterwards.

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
once across the whole set (`uv` links a checkout's venv to the cache in the scratch, and both are
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
while: every session ever started holds a checkout and a scratch, and the one thing a finished
session needs from the disk is nothing.

**The press records a fact and a reconciler acts on it.** `Service.archive` writes one key,
`archived`, and redirects; from that moment [the page has no message box](console.md#the-message-box),
the transcript says why, the rail's card says when, the row is muted and drops below every session
still active, and the routes that would
write to the session answer `422`. The press is offered in three places, the rail's card, under the
settings step, and [on the session's row in the list](console.md#the-session-list), and all three
are one disclosure over one form. Taking the directories away is `archive.py`'s, on a timer: each
round reads the key off every row, finds the sessions still holding something on disk, and takes it
off. A reconciler rather than a job the press queues, because what it does is diff a desired state
against an actual one and converge, so a console that died halfway through, or was pressed while a
pass still held the session, finishes on its next round with nothing to be told. The cost, stated:
a session pressed archived keeps its files for up to `archive_every`, and a console with nothing to
do reads its index once a minute.

**It refuses to take a checkout from under a pass.** A pass reads the checkpoint at its top and never
sees a key written after it started, so a session the worker holds is left for the next round, and
the pass that follows reads the key at its own top and stops - `Archived` is its own arm of `Ended`,
so the log says a session was closed rather than that one stalled. A command a person is still
running is the same case from the other side. Both are read off live state, since both are true only
at the instant they are read.

**The checkout's last tree is captured on the way out**, under `archived:tree`, because the press
cannot know it: files may still be being written when the button goes down, and the reconciler waits
until nothing holds the session. It is what a fork from the end of an archived session plants at, so
the branch carries on with the files the conversation actually ended with, snapshots the checkout
never captured included - what a person ran in it after the last request, and what a plugin fixed
at the turn's end. A checkout whose session broke its own `.git` fails that capture, and its files go
anyway, since no later round would capture it either: the failure is logged, no `archived:tree` is
recorded, and a fork from the end plants at the newest tree a turn recorded instead. The cost,
stated: whatever changed after that turn is lost with the checkout. Then the checkout is a directory
like any other and is removed as one; the snapshots are in the store under the session's refs and
outlive it, which is what keeps every earlier fork point reachable too.

**Nothing un-archives a session, and that is the design rather than a gap.** The key is write-once,
and what a person wants back is the conversation with somewhere to work, which is exactly what
[forking from the end](forking.md#forking-the-end) is: a live session carrying every turn, with a
fresh checkout at the archived tree and a scratch of its own. Putting the archived session itself
back would mean reconstructing a scratch that was deliberately not snapshotted, which is a second
mechanism to keep for a state a fork already reaches.
