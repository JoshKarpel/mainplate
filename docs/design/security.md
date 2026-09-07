# What runs, and as whom

Who this console trusts, what it does not, and the one boundary everything else here is built to
hold. This page is about *code execution*: which processes run untrusted input and with what
authority. Reaching the network, spending money and reading another session's conversation are all
downstream of that question and are decided on the pages that own them.

## Two sides, and the line between them

There is a **parent** and there is a **sandbox**. The parent is the console process: it holds the
provider credential, the store with every conversation in it, `config.yaml`, and the service user's
whole filesystem. The sandbox is a mount namespace with no network, a cleared environment, and a
session's worktree bound read-write beside its clone bound read-only. [Where a command
runs](sandbox.md) is what builds it.

Three things are untrusted, and they are untrusted equally:

- **What a model says.** A tool call is a string a model produced, and a model reads whatever the
  repository, the web page or the previous turn put in front of it. Nothing distinguishes a model
  that was asked to do something from one that was told to.
- **What a repository contains.** Its source, its build, its `.mainplate/` and its git configuration
  all arrive from wherever the branch came from.
- **What a plugin does.** A repository's plugin is a program somebody else wrote, and the [trust
  switch](plugins.md#trusting-a-repositorys-plugin) governs whether it runs, not what it may do once
  it does.

All three run in the sandbox. **The rule is that nothing on that side ever decides what happens on
this one.** It is not that the sandbox is escape-proof; it is that the parent must never take an
instruction, a path, or a program name from anything the sandbox can write.

## Nothing trusted may discover its inputs from a writable tree

The general shape, because it is the one that keeps recurring: **a trusted process that finds
something by looking is reading a value an untrusted one can set.** Being handed a value is safe;
going and finding one is not, whatever the finding is called.

The worked example is git, and it is worth reading even if you never touch `snapshots.py`, because
git makes the failure look like nothing at all.

Git reads configuration out of the repository it is working in, and several settings there name a
program git then runs. `core.fsmonitor` runs on the index refresh inside `git add`. `core.hooksPath`,
`core.sshCommand`, `core.alternateRefsCommand`, `diff.external`, a `textconv`, a `filter` driver's
`clean`, `remote.<name>.uploadpack` and `include.path` each do the same on some other command. That
list is not closed and never will be: the vendors who patched
[GitSpawn](https://www.manifold.security/blog/ai-coding-agents-git-hijack) patched a key, and the
disclosure names a second one they were not naming yet.

The reported delivery is a repository that arrives as a directory with `.git` already in it, from a
zip or a shared drive. **That is not a shape this console has.** Repositories only ever arrive
through `Clones.ensure` as `git clone --bare <url>`, and clone builds a fresh local configuration
rather than copying the remote's, so there is no path where the parent is handed somebody's
`.git/config` at all.

The primitive is available a different way, which is worse, and the difference is worth stating
plainly. A session's worktree is a **linked** one, so the `.git` at its root is a one-line pointer
file, and it sits in the one directory that session's `bash` is given write access to. So a session
can put a repository of its own there, either by replacing the file with a directory or by
repointing it at a repository it made, and git validates neither: it does not check the back-link,
and a forged one behaves exactly like a real one. Whatever the parent then ran in that directory ran
as the service user, outside the sandbox, with the store and `config.yaml` in reach. It needed no
malicious repository, only a model that could be talked into four shell commands.

And it was silent. Git treats a file-system monitor that exits non-zero as a reason to scan
normally, so the capture succeeds, the tree is correct, and nothing anywhere reports that a program
ran.

## The fix is to name the directory, not to filter the settings

`Worktree.gitdir` holds git's own directory for a tree, and `Worktree.addressed` renders it as
`--git-dir` with `--work-tree` beside it. Git then reads its configuration from there and never
looks down from the working tree, so the planted repository is not consulted, is not parsed, and
cannot name a program. `staging` writes the shadow index there for the same reason.

**`Worktree.git` is the only way git is run here**, and that is what keeps the fix from decaying.
Snapshots go through it and so does `list`, which is why `GitTracked` holds a `Worktree` rather than
a path. The tempting alternative is to publish the pieces, `addressed` and `environment`, and let
each caller assemble a subprocess out of them; it reads as flexibility and is a second definition of
how to run git safely. The first time the two drift, what gets dropped is the git directory, and the
symptom is a program running rather than an error. So a caller needing something the method does not
do gets an argument on it: `at` for where git runs, `index` for a shadow index, `Ran.stdout` for
output that is not text to strip.

`--work-tree` names the tree's root and never the directory git is run *in*. A listing of a
subdirectory still comes back relative to that subdirectory, exactly as an unpinned call's would;
naming the subdirectory instead is what changes the answer.

Two properties make this the right shape rather than a patch:

- **It is not a list.** Nothing enumerates which settings can run a program, so nothing goes stale
  when git adds the next one. That is [the mount-namespace argument](sandbox.md) about commands, one
  layer in: say what a process may reach, not which of its features are dangerous.
- **It costs nothing to know.** A linked worktree's git directory is `<clone>/worktrees/<session>`,
  so `Worktrees.gitdir` derives it from two paths it already holds and reads nothing out of the tree
  to do it. The trusted half of a session's git state is a directory the sandbox already binds
  read-only, and the untrusted half is a different directory. **That separation is what makes the
  fix possible**, and it is a property of planting linked worktrees rather than something anybody
  designed for this: in an ordinary checkout the configuration lives inside the writable tree and
  there would be no uncorrupted copy to name.

The cost, stated: a `Worktree` naming a session's tree now needs to know which clone it is of, so
`Workspaces.worktree` takes a repository. That is one more argument at three call sites, all of
which had it.

`test_snapshots.py::TestNamingGitsOwnDirectory` pins both callers, and pins each from both ends. The
control fires the same payload through a `Worktree` that looks for its git directory, because an
assertion that nothing ran is worth nothing unless something *would* have.

**What is left is the parent running a program at all.** Naming the directory makes git safe to run
out here; it does not make out here a good place to run things, and running `list` behind the
sandbox instead is the open question. It is cheap now, since pinning removed the reason the parent
had to read the tree to work out what to bind. What it costs is a console with no `bwrap`: that
console keeps its file tools and is offered no `bash`, so a sandboxed `list` would leave it able to
read and edit files and unable to find out which ones exist.

## What is deliberately not defended

Naming these is the point of the page. Each is a decision, and each is somewhere the argument above
does not reach.

**A command the person runs.** [`Run`](composer.md#run) is a shell, in the session's worktree, as
the service user, with the console's environment, outside the sandbox. That is what it is for. It
also means a `git` a person types there is subject to everything above, since what a shell discovers
is not ours to pin. Still open.

**A session on `EVERYTHING`.** It binds `/` read-write and can read `config.yaml` and the store. That
is what choosing it means and the card says so.

**The person at the console.** There is no authentication here and no authorisation model. Anybody
who can reach the console can start a session on the whole machine, so what actually guards this is
[who can reach it](deployment.md).
