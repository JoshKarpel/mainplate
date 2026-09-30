# What runs, and as whom

Who this console trusts, what it does not, and the one boundary everything else here is built to
hold. This page is about *code execution*: which processes run untrusted input and with what
authority. Reaching the network, spending money and reading another session's conversation are all
downstream of that question and are decided on the pages that own them.

## Two sides, and the line between them

There is a **parent** and there is a **sandbox**. The parent is the console process: it holds the
provider credential, the database with every conversation in it, `config.yaml`, and the service
user's whole filesystem. The sandbox is a mount namespace with no network unless the session chose
one, a cleared environment, a session's checkout bound read-write, and the repository's store bound
read-only beside it.
[Where a command runs](sandbox.md) is what builds it.

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
`clean`, `remote.<name>.uploadpack` and `include.path` each do the same on some other command, and
a hook is a program by definition. That list is not closed and never will be: the vendors who patched
[GitSpawn](https://www.manifold.security/blog/ai-coding-agents-git-hijack) patched a key, and the
disclosure names a second one they were not naming yet.

A session's checkout is a complete repository of its own, `.git` and all, and it sits in the one
directory that session's `bash` writes. So its configuration and its hooks are the session's to
write, on purpose: that is what lets `commit`, `rebase` and `stash` work in there. **Any git that
reads that configuration runs whatever the session last put in it.** Run in the parent, that is a
program running as the service user, with the database and `config.yaml` in reach, and it is silent:
git treats a file-system monitor that exits non-zero as a reason to scan normally, so a capture
would succeed, the tree would be correct, and nothing anywhere would report that a program ran.

## The parent never runs git against a checkout

**Once a checkout is planted, every git that touches it runs in its sandbox, and only bundles come
back.** That is the whole rule, and it replaces any attempt to decide which settings are dangerous.

- **`Checkout.git` is the only way git reaches a checkout.** It runs `bwrap` with the checkout
  read-write, the store read-only and, for a capture or a push, one directory this console made for
  the purpose. Snapshots go through it and so does `list`, which is why `GitTracked` holds a
  `Checkout` rather than a path. A poisoned configuration there can do exactly what the session's
  own `bash` could already do, and no more.
- **The parent runs git only against the `Store`**, the bare clone this console made and nothing in
  a sandbox can write, and it names the directory with `--git-dir` rather than letting git look for
  one. `git_at` is the one other entry point, for the directory stores are cloned under and for a
  checkout still being built, before any session has had a moment to write it.
- **What crosses is a bundle, read as data.** A capture or a push packs a commit into a file in that
  per-operation directory, and the store fetches it with `transfer.fsckObjects`. The store refuses a
  path that is a link or anything but a regular file, since a sandbox could leave a link to a file
  only the parent can see. The store's own configuration is the only one read.
- **The store believes the store.** The tree a capture records is read back out of the store after
  the fetch, and a checkout whose git reported one tree and sent another is refused rather than
  recorded. A diff between two trees is asked of the store as well, so a diff driver a session named
  in its own configuration is never consulted.

Two properties make this the right shape rather than a patch:

- **It is not a list.** Nothing enumerates which settings can run a program, so nothing goes stale
  when git adds the next one. That is [the mount-namespace argument](sandbox.md) about commands, one
  layer in: say what a process may reach, not which of its features are dangerous.
- **It is where the session already is.** The sandbox exists for the session's commands; running its
  git in there adds no second boundary to keep in step with the first.

The costs, stated. **A machine with no `bwrap` offers no repository at all**, because it has nowhere
safe to snapshot one; such a console is a place to talk and nothing else, since without a sandbox a
session that chose no repository gets no scratch and no tools either. **A changed capture is a
bundle and a fetch** rather than a `write-tree` in place, which is a few more processes per model
request on a checkout that changed. And **`list` spawns a namespace per call**, where a git in the
parent would have spawned one process.

`test_snapshots.py::TestWhatAPoisonedCheckoutCanRun` pins capturing, listing and diffing against a
checkout whose `core.fsmonitor` writes outside the sandbox, and `TestWhatTheStoreBelieves` pins the
two checks on what crosses. The control runs the same payload through an unconfined `git status`,
because an assertion that nothing ran is worth nothing unless something *would* have.

**A console-tier plugin runs as the operator, outside every sandbox**, and is handed the checkout's
path, so it is the one other place git can meet a checkout in the parent. The bundled [guidance
plugin](../plugins/guidance.md) lists the files it reads out of the checkout's index without reading
the checkout's configuration at all: it points `GIT_INDEX_FILE` at `<checkout>/.git/index` and runs
`ls-files --cached` with `--git-dir` naming an empty bare repository it makes in a temporary
directory, so the only configuration git finds is one nothing wrote.

**A checkout flattened to a path is the way this decays**, and it is worth naming because it does
not look like a git question at all. Anything that carries a session's checkout as a `Path` and runs
git against it at the far end has left the sandbox without anything at the call site saying so. So
the plugin runner is handed the session's own `Checkout` rather than the path on the payload it is
about to send, and `Live.checkout` holds the value for the same reason. The payload carries a string
as well, because that is what a plugin parses; the two are not the same thing.

## The file tools and `.git`

`read`, `edit` and `create` write from the parent and pass through no sandbox, so they are the other
way a session reaches its checkout. `GitTracked.sealed` names `.git` and `Files.resolved` refuses it
and everything under it. That is not a security boundary, since the same bytes are one `git config`
away in `bash`; it keeps an `edit` from rewriting a ref or a config line underneath git's own
locking. It is `.git` at a root's *top level* and nothing else: a `.gitignore`, a `.github/`, and a
fixture carrying a nested `.git` are all ordinary files, and a `Scratch` seals nothing because it is
not a repository.

## What a plugin gets that a command does not, and why each is safe

A repository's plugin and a session's `bash` run behind the same namespace, and then differ in five
places. Each difference is deliberate and each is narrow, and **three of the five are `setup`'s
alone**: they are gone at every event that runs during the conversation.

- **A network, at `setup` and never again.** A plugin that needs a program has to fetch one, so the
  event that runs before the first message is connected and every event during the conversation is
  not. On exe.dev a connected process holds [this console's
  credential](#on-exedev-the-network-is-the-credential), so a `setup` can push to the repository,
  force included. What makes that acceptable is *when* and *who said so*: the plugin runs only
  after somebody pressed the button on the [settings
  step](plugins.md#starting-a-session-takes-four-steps). In a new session the checkout then holds
  the commit the repository supplied and nothing the model has written exists, so what can act with
  the network is the repository's own code, run because a person said yes to it. **A fork is the
  exception**: it plants at a tree a model wrote, `.mainplate/` included, so its `setup` runs code a
  model may have written, with the network, and on exe.dev that includes pushing. What licenses it
  is the press in the branch, and nothing else: a fork carries no press from its parent. A turn
  boundary has neither the press nor the moment, which is why it is shut.
- **A scratch directory of its own**, rather than the session's. The model writes the session's, so a
  plugin that installed a program there would be running whatever the model last left at that path.
  Confinement is no answer, because both are confined the same way: what a shared directory would
  give the model is not privilege but *voice*, a way to have its own output delivered into the
  transcript as this repository's checks having failed. **Nothing below `app.py` can check this**:
  `Workspaces` is handed a root and `Spawned` is handed a root, and neither can see what the other
  was given, so the two roots being different is a property of that one file and of the test that
  asserts it there. It is `$MAINPLATE_PLUGIN_SCRATCH` inside the namespace and deliberately not
  `$MAINPLATE_SCRATCH`, which is the name a command finds the session's directory under: one word
  for both would be the same confusion in the environment that the shared root was on disk.
- **`$HOME` pointing at that directory**, rather than at a tmpfs. It is what lets a plugin keep what
  it fetched, and it is per plugin per session, so nothing one plugin caches is readable by another.
  It stays pointed there at `setup` as well, which is what keeps the bullet below from undoing this
  one.
- **The *session's* scratch bound read-write, at `setup` and never again.** A plugin that gets the
  repository ready installs a toolchain for the session's own commands, and that has to land where
  they look for it, which is their `$HOME`. What makes it safe is the same *when* as the network, plus
  one more thing: it is a directory the plugin **fills** rather than one it **runs out of**, since its
  own `$HOME` is still its own scratch. The laundering the bullet above describes needs a plugin
  executing at a turn boundary out of a path the model can rewrite, and there is no event at which
  both halves of that are true. It is `$MAINPLATE_SCRATCH`, which is the name a command finds the same
  directory under, because it is the same directory.
- **`$MAINPLATE_ENV`, naming a file of `KEY=value` lines, at `setup` and never again.** Those lines
  are set for the session's commands and for nothing else, no plugin's namespace included. **The
  parent reads that file as a value and never as a program**, which is the line this page draws
  everywhere else: it is parsed into a mapping, and a line that is not `KEY=value` fails the setup
  loudly rather than being passed over. What crosses is only what the plugin wrote, so it is an
  allowlist by construction rather than a filter somebody maintains.

**The trust switch still governs all five**, because it governs whether the plugin runs at all. What
it does not do is scale with them: a session that says no gets none of this, and a session that says
yes gets all of it. There is deliberately no finer control, for [the reason there is no per-repository
grant](plugins.md#the-control-is-the-refusal-not-the-permission).

**What the last two amount to, said plainly: any repository plugin left on can stage the environment
its session's commands run under.** The answer to that is not a check. It is that the same
repository's code already runs in that session's `bash`, that two switches stand in front of it, and
that the staging happens once, before the conversation, after a person pressed for it: over the
commit the repository supplied in a new session, and over a tree the model wrote in a fork, whose
own press is what says yes to that. [Getting the repository ready is a plugin
too](plugins.md#getting-the-repository-ready-is-a-plugin-too) is the whole of it.

## On exe.dev, the network is the credential

exe.dev's GitHub integration answers at a hostname that resolves only inside the VM and adds the
credential at exe.dev's own edge, so `env -i git ls-remote https://<integration>.int.exe.xyz/...`
answers with an empty environment, and a push there needs no token either: that is how the store's
own push works. **Any process on this machine that can reach the network holds this console's
authority over the repository**, force-push included, and the same host serves the API.

So on exe.dev what keeps a session from moving the remote is not a missing credential. It is the
network being off: a confined sandbox cannot resolve the host at all, and the store, which can, is
driven by the parent and pushes only what `/push` names. Three things have the network, and each is
somebody's choice rather than an accident:

- **A session started with the network on.** Its `bash` and every `Run` can push. The picker offers
  it because some work needs it, and choosing it gives up the push gateway for that session.
- **[`Online`](composer.md#online)**, one command with the network on in a session that otherwise
  has it off. A person typed it, and the panel says it ran online for as long as it is there.
- **A repository plugin at `setup`**, after the press on the settings step; see [what a plugin
  gets](#what-a-plugin-gets-that-a-command-does-not-and-why-each-is-safe).

A forge that hands out a credential rather than being one would change this section and nothing
else here: the store would hold the token, and a connected sandbox would reach the network without
reaching the repository.

## A person's command, and a push

**[`Run`](composer.md#run) is confined like the model's `bash`**, and that is forced rather than
chosen. A person's `git commit` in the checkout runs the checkout's hooks, which the model can
write; run as the service user it would run them with everything that user holds. So a command a
person types gets the session's sandbox, its network answer and the environment its setup recorded,
exactly as the model's would. What that costs is the person's own `$HOME` and credentials: nothing
typed there can reach them, and with the network off `git push` in there reaches nothing.
[`Online`](composer.md#online) is the same sandbox with the network on for one command, which on
exe.dev means `git push` in there does reach the repository.

**Pushing is a control of its own.** [`/push`](composer.md#push) takes the branch the session
*recorded*, never whatever the checkout's `HEAD` is on, carries that branch's commit into the store
as a bundle, and has the store push it, never forced, with the store's configuration. So what moves
on the remote is the branch the page names beside the button, and the git that pushes reads no
configuration a session wrote.

Two mitigations were considered for running a person's git outside the sandbox and are not here,
which is worth recording because both read well:

- **`GIT_CONFIG_COUNT`** puts configuration in the environment, where it is inherited by arbitrarily
  nested git, and it does override a repository's own config. It is defeated by `git -c`, by any
  script that clears the environment, and by every setting nobody has thought to name yet. A
  denylist bought with a version floor.
- **Checking the configuration before running a person's command.** There is no correct content to
  check against once the session owns its `.git`: a hook the session wrote on purpose and one it was
  talked into are the same file.

## An artifact is served from this origin, and runs in none

An artifact is HTML a model wrote, and the console serves it from its own origin, which is the
origin its cookies and pages are on. What keeps that from mattering is the response rather than the
page framing it: every version is served under `Content-Security-Policy: sandbox allow-scripts` with
nothing allowed out, so the document is an opaque origin however it is opened, in the preview frame
or typed into a tab, and its scripts can reach neither this console nor the network. The frame's own
`sandbox` attribute says the same again and is not what anything relies on.

The file a person downloads is the same bytes with none of that around it, and a browser opening a
local file gives it whatever it gives local files. The page says so beside the link, and nothing here
can do more. See [Artifacts](artifacts.md#serving-a-version).

## What is deliberately not defended

Naming these is the point of the page. Each is a decision, and each is somewhere the argument above
does not reach.

**Every session on a repository can read every other session's snapshots.** The store is bound
read-only into each sandbox, because a checkout borrows its objects and `origin` is the store, so a
session can walk another session's snapshot chain under `refs/mainplate/sessions/`. None of them can
write it. Separating them would mean a store per session, and a fork from one session into another
would then be a copy rather than a checkout of an object that already exists.

**An operator's own console-tier plugin.** It runs unconfined and is handed the checkout's path, so
a plugin that runs `git -C <checkout>` the ordinary way reads the configuration the session wrote and
runs whatever it names, as the service user. Nothing here can stop that, since the plugin is a
program the operator installed; what the bundled one does is the pattern to copy.

**What a session pushes.** `/push` sends the session's recorded branch as the session left it, and
the content of every commit on it is the session's. A person pressing it is the review; nothing here
inspects what a commit holds.

**A connected sandbox and the repository.** On exe.dev, a session with the network on, an `Online`
command and a `setup` can each push to the repository without `/push`, [for the reason
above](#on-exedev-the-network-is-the-credential). Nothing filters the network by host, so the
network switch is the whole of the control.

**A session on `NOTHING` runs what a model writes.** It reaches no file that was on the machine
before it, but wherever there is a `bwrap` it has a scratch and `bash`, so it executes a model's
commands like any other session. With the network on that shell can dial out, which on exe.dev is
[this console's authority over every attached
repository](#on-exedev-the-network-is-the-credential), and its scratch stays on the disk until the
session is archived. Where there is a `bwrap`, a session with no shell at all is not an arm anybody
can pick.

**A session on `EVERYTHING`.** It binds `/` read-write and can read `config.yaml` and the database.
That is what choosing it means and the card says so.

**The person at the console.** There is no authentication here and no authorisation model. Anybody
who can reach the console can start a session on the whole machine, so what actually guards this is
[who can reach it](deployment.md).
