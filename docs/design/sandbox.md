# Where a command runs

The mount namespace `bash` runs behind, the places a session can reach by name, and the two
isolation axes a session picks once and is bound to for life.

## A mount namespace, not a denylist

`sandbox.py` is the boundary `bash` runs behind, and it is a **mount namespace** rather than a list
of commands that are allowed. A denylist over commands loses on contact with reality: `git stash`
reads as safe and reverts every tracked edit in the worktree, `git config` can set `core.hooksPath`,
and a release next year adds something nobody has classified. A mount says what a process can
*reach*, so it is already right about commands nobody has thought of, including whatever a
repository's own build script runs. `test_sandbox.py` pins that with `git stash` specifically.

**Per call, never a long-lived executor**, and the reason is replay rather than cost. A pass re-runs
the conversation body from the top and `wrap_tool_execute` replays recorded results instead of
re-running them, so a sandbox holding state between calls would offer that state on a first pass and
withhold it on a resumed one, with nothing to tell the agent which it is in. State that survives
*sometimes* is worse than state that never survives, because it invites reliance and then breaks
only under crash-resume. A fresh namespace costs a couple of milliseconds against a call that costs
hundreds, and leaves no process to supervise, reap, or reconstruct. The tool's own description says
nothing persists, and `test_sandbox.py` asserts it.

Five things about the policy are decided rather than incidental:

- **The clone is bound read-only, and that is the load-bearing half.** Every read still works,
  `ls-files`, `status`, `diff`, `log`, `blame`, while `add`, `commit`, `stash` and `checkout` fail
  on a read-only `index.lock`. What that buys is not tidiness: a git write from in there would be a
  second history that no panel shows, no fork inherits and no rewind restores, which is the second
  copy of state this whole console exists to refuse. Snapshots keep working because they run in the
  parent, where the clone is writable, so the agent physically cannot rewrite the history
  `refs/mainplate/snapshots` is chained onto. The invariant `snapshots.py` used to hold by being
  careful is now one no tool can break, including tools that do not exist yet.
- **The *common* directory is what is bound, not the worktree's own.** A linked worktree's `.git` is
  a file holding an absolute pointer into the clone, and the per-worktree directory sits inside the
  clone with a `commondir` pointing back out at it for objects and refs. So `--git-common-dir`
  reaches both and `--absolute-git-dir` reaches neither: bind the wrong one and there is no git in
  the sandbox at all, which silently takes `list` with it.
- **Both are bound at their own absolute paths**, never remapped to a tidy `/workspace`. That is
  forced by the same pointer being absolute. The alternative is a `GIT_COMMON_DIR` that every
  consumer has to carry and any subprocess is free to unset, bought for a shorter path.
- **The session binds come after `--tmpfs /tmp`.** bwrap applies arguments in order, so a workspace
  root that happens to live under `/tmp` is covered by the tmpfs and disappears if the binds come
  first, leaving a command that cannot change directory into its own worktree. That is not
  hypothetical: it is where every test in the suite puts a worktree.
- **`--unshare-pid` is teardown as much as isolation.** Killing the namespace's init reaps whatever
  the command left running, which is what makes the timeout and a cancelled turn leave no orphan
  build behind.

## The scratch directory

**A session gets one, and nothing captures it on purpose.** `workspaces/scratch/<session>` is bound
read-write beside the worktree, so a build cache, a downloaded artifact or a note to itself survives
from one call to the next and from one turn to the next. That it is *not* snapshotted is the same
decision as snapshots honouring a `.gitignore`, arrived at one level out: going back to before a
call should not uninstall what was installed since. The cost is the one an ignored path already
carries, that what is in there goes stale while the source around it moves back.

Outside the worktree rather than under it, and that is not tidiness. `list` passes `--others`, so a
directory inside the worktree is in every listing and every `git status` until something excludes
it, and the only place to write that exclusion is a git directory read-only wherever a command can
see it. `test_sandbox.py` pins this by asserting that nothing in the scratch reaches either.

It is made on the first command rather than when the session is planted, because bwrap will not bind
a source that does not exist and the tool is the one thing that knows a command is about to run. A
fork gets its own, empty: copying it would be copying mutable state, and sharing it would be two
sessions writing one directory. That matches the worktree, which a fork plants fresh at a recorded
tree and therefore without any ignored file either.

**`read`, `edit` and `create` reach it; `list` does not.** The point of extending them at all is a
plan or a notes file kept across turns, which is the one thing in a scratch directory that wants a
line editor; a build cache never does.

## Roots

Where the file tools may reach. What they *are* is [how a model reaches a file](tools.md).

`Files` holds `roots`, a tuple of *typed* places rather than one path and a list of extras. The type
is what decides: a `GitTracked` is files a conversation is about and is the only kind git can be
asked about, so it owns `entries` and answers `list`; a `Scratch` answers no question git answers,
which is why it exists, so it carries no way to enumerate itself and `listing` refuses it in its own
arm of a `match` that `assert_never` closes. Adding a kind is one arm, and adding a *second
worktree* is one more element, where a `root` plus an `also` would have hardcoded exactly one.

`resolved` returns a `Located`, which is the resolved path **and** the root it landed in. Both
halves, because the caller needs both and working the second one out twice is how they come to
disagree: a tool holding one of these has proof the path is reachable and proof of what kind of
place it is, so nothing downstream re-asks either question. That is what turned `list`'s restriction
from a condition inside the tool into a property of the root.

The **first** root is where a relative path lands, and that stays well defined however many roots a
session ends up with. So a bare `notes.md` is about the repository, because that is what a
conversation is about. Refusing `list` in the tool rather than leaving it to `entries` is the usual
reason: "not a repository" arrives from git as a `ListingFailed` fault and ends the turn, where a
`Refused` tells the model to reach for `bash` instead.

**Anywhere else is reached by naming the root, not by writing its path out.** `read`, `edit` and
`create` take a `root`, which says which place a *relative* path joins and nothing else: an absolute
path still lands where it points, so naming one cannot redirect a path that already says where it
goes. A worktree sits under 32 hex characters of session id, and a model reproducing those from
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

In the sandbox the name is a `Bind` field, so an environment variable is only ever a name for a path
that sandbox actually has. The clone gets none deliberately: it is bound so git works, not so
anybody addresses it, and a name would invite a write to the one place the read-only bind exists to
refuse. `/` gets none either, since a variable holding `/` names what every path already starts
with.

The file tools reach the scratch only where `bash` is offered, since without a command to make the
directory exist `read` would name a path nothing ever creates.

## Network

**Binding a port works; reaching it does not.** `--unshare-net` gives a namespace with loopback up,
so a command can start a server and curl it within one call, which covers integration tests. What it
cannot do is make that port visible to a person, and that is deliberate: a dev server somebody
watches is the harness's to run, outside the sandbox, not something an agent tool call should leave
behind. `Venue.CONNECTED` is the arm for that and nothing uses it yet.

Network is **off**, and off rather than allowlisted. An allowlist containing github.com contains
gists, one containing a package registry contains a package anybody can publish, and a DNS query to
`<secret>.attacker.example` leaves through any resolver that is allowed. It would buy a MITM proxy,
a CA inside the sandbox, and every tool that pins certificates breaking, for a defence against the
malicious-repository case and almost none against a determined injection. Landlock cannot help here
either: its network rules key on a *port*, never an address, and do not cover UDP at all.

`--clearenv` is what keeps the parent's environment out, and the credential is out of the sandbox
structurally rather than carefully: the agent loop that holds it stays in the parent and only the
command crosses.

**A missing sandbox is reported, not refused.** `open_console` resolves `bwrap` once and logs what
it found; without it a session keeps every file tool and is offered no `bash`, which is exactly what
this console was before there was one. That is [the promise rather than the
refusal](../philosophy.md#refusing-at-startup-or-promising-not-to-raise), because nothing here
leaves somebody holding a choice they cannot use. It is logged because a shell tool that quietly is
not there is the state nobody can diagnose.

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
read-write instead of a worktree and changes nothing else, which is why there is one `argv` rather
than two.

**The repository and the filesystem level are one question, asked once.** `workspace_cards` is the
group: every repository a forge reaches, plus `no files` and `this whole machine`. Picking one
settles `Choice.repository` and `isolation.filesystem` together, so they cannot disagree at the
source. `posted_workspace` is where one posted value becomes the two recorded ones, told apart
without a prefix because a repository's id is `forge:key` and so always holds a colon.

That is a correction rather than the first design, and the reason is worth keeping. They were two
groups, with the worktree level drawn greyed until a repository was picked. Keeping the two in step
then wanted a swap to refresh the greying, a fix so the narrowing box would not check a disabled
card, and a card in the completion list that could not be chosen: three pieces of machinery for one
answer stored in two places. It is the worked example behind [the rule about controls that need
keeping in step](../philosophy.md#controls).

**An enum whose members are recorded cannot be renamed freely, because the *value* is what is in the
store.** `Filesystem.WORKTREE` was `WORKSPACE`, and renaming the member changed the recorded string
too, so every session written until then became a page that answered 500. Nothing had been released,
so those records were only ever in a development database and the rename stands as it is.

**Where that is not true, the answer is a migration at startup, not a branch on the read path.**
`parse_isolation` describes the shape this console writes *now*; a retired value bridged inline
never goes away, and a file accreting them stops saying what the record is. Defaulting an *absent*
field is a different thing and stays: that is ordinary parsing of an optional, the way `thinking` is
read, and it is what lets every session written before `isolation` existed read back as what it
already had.

`Isolation.settled` survives the merge and is still applied by `Service.start` and `Service.fork`,
because a fork's repository is *inherited* rather than posted and a form is not the only way in.
What it no longer has to do is correct the start page, which can no longer express a contradiction.

Whether the workspace can be chosen at all is the caller's answer, given to `picker` as `None`
rather than as an empty `Reachable`. The difference is load-bearing now that the group holds more
than repositories: empty means no forge reaches anything, which still leaves two answers worth
offering, where `None` means a fork already works somewhere and a control would be a lie about what
the page does.

The network sits under the workspace and above the endpoint, following the picker's order of
breadth: what a session's files are decides what it can touch, whether it can dial out decides what
it can do with them, and the endpoint and model only decide who answers.

**A session on `EVERYTHING` can read `config.yaml` and the store**, which is to say the credentials
and every other conversation. That is what choosing it means rather than an oversight, and the card
says so.

**None of these axes bind a command the *person* runs**, which is [the composer's
`Run`](composer.md#run): that runs outside the sandbox entirely, as the service user, in the
session's worktree. It is not a hole in this, it is what this is for, since the read-only clone
bound here is what stops a *tool* writing a history no panel shows, and `git commit` is not a tool.
The authority it grants is what the paragraph above already grants a model, so what actually guards
it is who can reach the console.

One thing is deliberately still to come. `GitTracked.entries` runs `git ls-files` in the parent
rather than through the sandbox, which is a narrower problem than arbitrary shell (its argv is ours;
the exposure is a malicious repository's git configuration) and a good next step.
