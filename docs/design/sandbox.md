# Where a command runs

The mount namespace `bash` runs behind, the places a session can reach by name, and the two
isolation axes a session picks once and is bound to for life.

## A mount namespace, not a denylist

`sandbox.py` is the boundary `bash` runs behind, and it is a **mount namespace** rather than a list
of commands that are allowed. A denylist over commands loses on contact with reality: `git stash`
reads as safe and reverts every tracked edit in the checkout, `git config` can set `core.hooksPath`,
and a release next year adds something nobody has classified. A mount says what a process can
*reach*, so it is already right about commands nobody has thought of, including whatever a
repository's own build script runs, and it is why every git in here may do whatever git does:
what it can touch is the session's checkout and nothing else.

**Per call, never a long-lived executor**, and the reason is replay rather than cost. A pass re-runs
the conversation body from the top and `Stepping.call` replays recorded results instead of
re-running them, so a sandbox holding state between calls would offer that state on a first pass and
withhold it on a resumed one, with nothing to tell the agent which it is in. State that survives
*sometimes* is worse than state that never survives, because it invites reliance and then breaks
only under crash-resume. A fresh namespace costs a couple of milliseconds against a call that costs
hundreds, and leaves no process to supervise, reap, or reconstruct. The tool's own description says
nothing persists, and `test_sandbox.py` asserts it.

Six things about the policy are decided rather than incidental:

- **The checkout is bound read-write whole, `.git` included.** It is a repository of the session's
  own, so `add`, `commit`, `merge`, `rebase`, `stash` and `fetch` do what they say, against this
  session's refs and index and nobody else's. What a commit in there is *not* is the record: the
  conversation's snapshots are taken out of the checkout into the store, so a rebase that rewrites
  the session's branch rewrites nothing a panel shows or a fork plants from. The cost is that the
  checkout's configuration is the session's to write, which is why [the parent never runs git
  against it](security.md#the-parent-never-runs-git-against-a-checkout).
- **The store is bound read-only beside it.** The checkout borrows the store's objects through
  `alternates`, and `origin` is the store, so without the bind there is no git in the sandbox at all,
  and with it `git fetch` brings the repository's refreshed branches with no network. Read-only is
  what keeps one session's git from reaching another's; what it costs is that every session on a
  repository can read every tree any of them snapshotted. Nothing can push to `origin` from in here,
  since the store cannot be written, and with the network off nothing can push at all. With it on,
  on exe.dev, a command can push to the repository itself, which [what runs, and as
  whom](security.md#on-exedev-the-network-is-the-credential) is about.
- **Both come from `checkout_places`**, which `Sandbox.around` and `Checkout.confined` both call, so
  a session's commands and the git this console runs to snapshot them see one repository bound one
  way. Two copies of two binds would agree until one of them changed.
- **Both are bound at their own absolute paths**, never remapped to a tidy `/workspace`. That is
  forced by `alternates` naming the store by its absolute path. The alternative is a
  `GIT_ALTERNATE_OBJECT_DIRECTORIES` that every consumer has to carry and any subprocess is free to
  unset, bought for a shorter path.
- **The session binds come after `--tmpfs /tmp`.** bwrap applies arguments in order, so a workspace
  root that happens to live under `/tmp` is covered by the tmpfs and disappears if the binds come
  first, leaving a command that cannot change directory into its own checkout. That is not
  hypothetical: it is where every test in the suite puts a checkout.
- **`--unshare-pid` is teardown as much as isolation.** Killing the namespace's init reaps whatever
  the command left running, which is what makes the timeout and a cancelled turn leave no orphan
  build behind.

## The scratch directory

**A session gets one, and nothing captures it on purpose.** `workspaces/scratch/<session>` is bound
read-write beside the checkout, so a build cache, a downloaded artifact or a note to itself survives
from one call to the next and from one turn to the next. That it is *not* snapshotted is the same
decision as snapshots honouring a `.gitignore`, arrived at one level out: going back to before a
call should not uninstall what was installed since. The cost is the one an ignored path already
carries, that what is in there goes stale while the source around it moves back.

**A session with no repository gets one too, and it is the whole of what its commands reach.**
`NOTHING` is nothing *of the machine*: the scratch is bound alone, a command starts in it, and a
relative path to the file tools means it, since there is no checkout for one to mean instead. That
is `InAScratch` beside `InACheckout`, and the same one bind is what `Sandbox.within` makes. It is
what lets a conversation that is not about a repository run a script or keep a plan across turns
without being handed the whole machine to do it, which is the only other arm that has a shell.

The cost, stated, is three things the arm that reaches nothing takes on by having one. It runs
commands a model wrote, where a session with no shell would run none. Its network switch means
something: with it on, that shell can dial out, and on exe.dev that is [this console's authority
over the repositories](security.md#on-exedev-the-network-is-the-credential). And every session with
no repository keeps a directory on the disk, counted on its row, until it is archived.

**It is `$HOME` for a session's commands**, rather than the tmpfs, because that is where every tool
that fetches keeps what it fetched: a toolchain [a repository's plugin
installs](plugins.md#getting-the-repository-ready-is-a-plugin-too) lands under `$HOME`, and a shell
whose `$HOME` is anywhere else cannot find its own tools. `home_in` is where that is decided, and it
makes what a shell leaves in a home directory scratch by intent rather than by accident; the cost is
that a stray dotfile survives the call. A session over the whole machine has no scratch and keeps
the tmpfs.

Outside the checkout rather than under it, and that is not tidiness. `list` passes `--others`, so a
directory inside the checkout is in every listing and every `git status` until something excludes
it, and the only place to write that exclusion is the checkout's own `.git/info/exclude`, which
anything the session runs could take out again. `test_sandbox.py` pins this by asserting that
nothing in the scratch reaches either.

It is made on the first command rather than when the session is planted, because bwrap will not bind
a source that does not exist and the tool is the one thing that knows a command is about to run. A
fork gets its own, empty: copying it would be copying mutable state, and sharing it would be two
sessions writing one directory. That matches the checkout, which a fork plants fresh at a recorded
tree and therefore without any ignored file either.

**`read`, `edit` and `create` reach it; `list` and `grep` do not.** The point of extending them at all
is a plan or a notes file kept across turns, which is the one thing in a scratch directory that wants
a line editor; a build cache never does.

It is also most of [what a session takes on disk](workspace.md#what-a-session-takes-on-disk), which
is the figure on the session's row: a toolchain fetched in here is tens of thousands of files, where
a checkout is one copy of the repository's files.

**A plugin gets a different one, and `$HOME` points at it.** `workspaces/plugins/<session>/<tier>/
<name>` is bound in place of the session's for a repository's plugin, because the session's is a
place the *model* writes: a plugin that kept an executable in there would be running whatever the
model last left at that path, at every turn boundary, and reporting the result into the conversation
as this console's own. `$HOME` rather than only a bound path, for the reason a session's scratch is
its commands' `$HOME`: that is where anything that fetches keeps what it fetched, so a `uv run
--script` plugin resolves an interpreter at `setup` and finds it again at the next event, with the
network shut. `Sandbox.argv`'s `home` argument is how each namespace is told which directory that
is, and its `environment` argument is what a session's commands are additionally told, which nothing
hands to a plugin's own namespace.

## Roots

Where the file tools may reach. What they *are* is [how a model reaches a file](tools.md).

Bundled and user skills are read-only binds in a session command's namespace,
under the same paths its file tools call `bundled_skills` and `user_skills`.
Repository skills are already in the checkout. The bind names only the skill
directory, not the operator's configuration directory; the model's `read`
uses the same roots without entering bwrap. See [loading
context](context-loading.md).

`Files` holds `roots`, a tuple of *typed* places rather than one path and a list of extras. The type
is what decides: a `GitTracked` is files a conversation is about and is the only kind git can be
asked about, so it owns `entries` and answers `list` and `grep`; a `Scratch` answers no question git
answers, which is why it exists, so it carries no way to enumerate itself and both tools refuse it
before asking git. Adding a kind is one arm, and adding a *second checkout* is one more element, where
a `root` plus an `also` would have hardcoded exactly one.

`resolved` returns a `Located`, which is the resolved path **and** the root it landed in. Both
halves, because the caller needs both and working the second one out twice is how they come to
disagree: a tool holding one of these has proof the path is reachable and proof of what kind of
place it is, so nothing downstream re-asks either question. That is what turned `list`'s restriction
from a condition inside the tool into a property of the root.

The **first** root is where a relative path lands, and that stays well defined however many roots a
session ends up with. So a bare `notes.md` is about the repository, because that is what a
conversation is about. Refusing `list` or `grep` in the tool rather than leaving it to `entries` is
the usual reason: "not a repository" arrives from git as a `ListingFailed` fault and ends the turn,
where a `Refused` tells the model to reach for `bash` instead.

**Anywhere else is reached by naming the root, not by writing its path out.** `read`, `edit` and
`create` take a `root`, which says which place a *relative* path joins and nothing else: an absolute
path still lands where it points, so naming one cannot redirect a path that already says where it
goes. A checkout sits under 32 hex characters of session id, and a model reproducing those from
memory eventually reproduces them wrong, which is a refusal it then has to recover from at the cost
of a round trip. Four things there are decided:

- **The names are `roots.py`, which both the tools and the sandbox read.** A model reaches the same
  directory two ways, by naming it to a tool and through `$MAINPLATE_SCRATCH` in a command, and
  those are two surfaces of one answer. Written separately they are two lists to keep in step and
  the failure is quiet. It is the `StepKind` move, and it lives in a module of its own for
  `thinking.py`'s reason: the file tools know nothing about sandboxes and the sandbox knows nothing
  about `Files`.
- **A root owns its own name**, as a property on each arm beside `GitTracked.entries`, so adding a
  kind of place brings its name with it rather than needing an entry somewhere else.
- **An unknown root is refused with the ones this session has.** That is what lets one tool
  description serve every session: what a session's places are called varies and a toolset's
  descriptions do not, so the vocabulary is taught at the one moment it is got wrong.
- **What comes back names the root only where it is not the first.** A bare `notes.md` in a return
  is two different files once a session has two roots; a root named on every line stops being read.
  Same rule as `Reachable.labelled`.

**A root also says what in it is out of reach**, which is `Root.sealed`, a property on each arm
beside `name`. A `GitTracked` seals `.git` and everything under it, because that is git's own state
and a line edit of a ref or a config line would bypass git's locking and its formats; a `Scratch` and
a `System` seal nothing, having no git of their own. `Files.resolved` refuses a sealed path after
it has established the path is in reach, so being inside a root is necessary and not sufficient.

It is about correctness rather than reach: `bash` runs git against the same directory, confined like
every other command, so the same bytes are one `git config` away. It is a root's *top level* only: a
`.gitignore`, a `.github/` and a fixture carrying a nested `.git` are ordinary files.

In the sandbox the name is a `Bind` field, so an environment variable is only ever a name for a path
that sandbox actually has. The store gets none deliberately: it is bound so borrowed objects resolve,
not so anybody addresses it, and a name would invite a write to the one place the read-only bind
exists to refuse. `/` gets none either, since a variable holding `/` names what every path already starts
with.

The file tools reach the scratch only where `bash` is offered, since without a command to make the
directory exist `read` would name a path nothing ever creates.

## Network

**Binding a port works; reaching it does not.** `--unshare-net` gives a namespace with loopback up,
so a command can start a server and curl it within one call, which covers integration tests. What it
cannot do is make that port visible to a person, and that is deliberate: a dev server somebody
watches is the harness's to run, outside the sandbox, not something an agent tool call should leave
behind. `Venue.CONNECTED`, the arm a session picks with the network on, is the whole network for a
command, and a server one starts still ends with the call.

Network is **off** unless a session picks it, and off or on rather than allowlisted. An allowlist
containing github.com contains gists, one containing a package registry contains a package anybody
can publish, and a DNS query to `<secret>.attacker.example` leaves through any resolver that is
allowed. It would buy a MITM proxy, a CA inside the sandbox, and every tool that pins certificates
breaking, for a defence against the malicious-repository case and almost none against a determined
injection. Landlock cannot help here either: its network rules key on a *port*, never an address,
and do not cover UDP at all.

`--clearenv` is what keeps the parent's environment out, and the credential is out of the sandbox
structurally rather than carefully: the agent loop that holds it stays in the parent and only the
command crosses.

**A missing sandbox is reported, not refused.** `open_console` resolves `bwrap` once and logs what
it found; without it no session is offered `bash` and **no repository is offered at all**, since a
checkout's git reads configuration the session writes and there is nowhere safe to run it to take a
snapshot. What is left is a place to talk. That is [the promise rather than the
refusal](../philosophy.md#refusing-at-startup-or-promising-not-to-raise), because nothing here
leaves somebody holding a choice they cannot use. It is logged because a shell and a repository list
that quietly are not there are the state nobody can diagnose.

## Isolation, as two axes a session picks

`Choice.isolation` is an `Isolation`, holding a `filesystem` and a `network`, recorded once before
the first prompt and fixed for the session's life like the rest of the choice. Forking is how it
changes. One value rather than two fields spread across `Choice`, because they are answered
together, recorded together and read together by the one thing that builds a session's tools, and a
third axis, what a command may *spend* in a cgroup, lands as a member rather than as a parameter
threaded through four signatures.

**The two axes are independent, and `EVERYTHING` still being a sandbox is what keeps them so.** The
network switch is `--unshare-net` on the same namespace, the credential is kept out by `--clearenv`
and teardown is `--unshare-pid`, so an arm that dropped the sandbox would silently take all three
with it and make "the whole machine with no network" unrepresentable. `Sandbox.everywhere` binds `/`
read-write instead of a checkout and changes nothing else, which is why there is one `argv` rather
than two.

**The repository and the filesystem level are one question, asked once.** It is the card you press
**New session** on, on the dashboard: every repository a forge reaches, plus `only scratch` and
`whole machine`. Picking one settles `Choice.repository` and `isolation.filesystem` together, so
they cannot disagree at the source, and a fork inherits both. `posted_workspace` is where one posted value becomes the two recorded ones, told apart
without a prefix because a repository's id is `forge:key` and so always holds a colon.

That is a correction rather than the first design, and the reason is worth keeping. They were two
groups, with the checkout level drawn greyed until a repository was picked. Keeping the two in step
then wanted a swap to refresh the greying, a fix so the narrowing box would not check a disabled
card, and a card in the completion list that could not be chosen: three pieces of machinery for one
answer stored in two places. It is the worked example behind [the rule about controls that need
keeping in step](../philosophy.md#controls).

**An enum whose members are recorded cannot be renamed freely, because the *value* is what is in the
database.** `Filesystem.CHECKOUT` was `WORKSPACE`, and renaming the member changed the recorded string
too, so every session written until then became a page that answered 500. Nothing had been released,
so those records were only ever in a development database and the rename stands as it is.

**Where that is not true, the answer is a migration at startup, not a branch on the read path.**
`parse_isolation` describes the shape this console writes *now*; a retired value bridged inline
never goes away, and a file accreting them stops saying what the record is. Defaulting an *absent*
field is a different thing and stays: that is ordinary parsing of an optional, the way `thinking` is
read, and it is what lets every session written before `isolation` existed read back as what it
already had.

`Isolation.settled` is applied by `Service.start` and `Service.fork` all the same, because a fork's
repository is *inherited* rather than posted and a form is not the only way in. What it does not
have to do is correct the new-session page, which cannot express a contradiction.

Whether the workspace can be chosen at all is the caller's answer, given to `picker` as `None`
rather than as an empty `Reachable`. The difference is load-bearing because the group holds more
than repositories: empty means no forge reaches anything, which still leaves two answers worth
offering, where `None` means a fork already works somewhere and a control would be a lie about what
the page does.

The network sits under the workspace and above the endpoint, following the picker's order of
breadth: what a session's files are decides what it can touch, whether it can dial out decides what
it can do with them, and the endpoint and model only decide who answers.

**A session on `EVERYTHING` can read `config.yaml` and the database**, which is to say the credentials
and every other conversation. That is what choosing it means rather than an oversight, and the card
says so.

**The same axes bind a command the *person* runs**, which is [the composer's
`Run`](composer.md#run): it gets the session's sandbox, its network answer and the environment its
setup recorded. The checkout's hooks are the model's to write, so a person's `git commit` run
anywhere else would run them with the service user's authority. What that costs the person is their
own `$HOME` and credentials inside the command, which is why pushing is [a control of its
own](composer.md#push) rather than something typed.

## What the parent still runs

The binds above say what a *command* reaches. The parent also has git to run against a session's
files, to snapshot them and to answer `list` and `grep`, and the checkout's configuration is the
session's to write. So the parent runs none of it itself: `Checkout.git` runs every one of those
behind this same namespace, and what comes back to the parent is a listing or a bundle, read as
data. The parent runs git only against the store, which nothing in here can write. [What runs, and
as whom](security.md) is the whole of it, and it is the page to read before adding anything to the
parent that touches a checkout.
