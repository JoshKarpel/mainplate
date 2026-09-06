# Plugins

How somebody adds to this console without editing it: what a plugin is, what it may contribute, the
two files that declare one, and where a repository's own code runs, which is emphatically not in
this process.

**None of this is built yet.** Every other page here is the authority on the code as it stands; this
one is the design the code is being written to, and the line above goes when it is.

## What a plugin is

**A plugin is a value that answers a handful of questions, and every answer is a function of things
already settled.** It holds no session, no agent, no connection, and nothing live.

```python
@dataclass(frozen=True, slots=True)
class Plugin:
    name: str
    capability: Capabilities | None = None
    card: Cards | None = None
    answers: tuple[Answer, ...] = ()
    settings: type[BaseModel] | None = None
```

`name` is one lowercase word and is four things at once, which is [one name per
thing](../philosophy.md#the-words) rather than four to keep in step: the key in `config.yaml`, the
sub-field its settings live under, the prefix on anything it records, and what a refusal names.

**It is not an `AbstractCapability`, and the temptation to make it one is the first thing to
answer.** Subclassing is one class instead of two and reads as the obvious move. What it complects
is three lifetimes:

- a capability is built **per pass**, inside `agent_for`, and Pydantic AI hands out per-run copies
  of it through `for_agent` and `for_run`
- a card is built **per render**, and has to be a pure function of already-answered questions or
  [the gallery stops
  working](../philosophy.md#a-page-is-a-pure-function-of-already-answered-questions)
- settings are declared **per process**, at startup, before any session exists

An object constructible in all three is an object with no invariants in any of them. And
`AbstractCapability` is a seventy-method abstract base in a package that moves fast, so is-a would
make every plugin somebody else wrote a subclass of a surface we do not control: a Pydantic AI
release that reshapes the base class breaks plugins this repository has never seen.

**Nor does a plugin *hold* a capability, because a capability cannot exist until the session's facts
do.** `StepwiseDurability()` is constructed inside `agent_for`, and anything a plugin contributes to
an agent needs the same things the file tools need: the roots, the confinement, the choice. So the
field is a **constructor and the value it takes**, which is the shape
[`tools/__init__.py`](tools.md) already commits to for exactly this reason.

```python
type Capabilities = Callable[[Reach, Choice], AbstractCapability[None] | None]
```

`None` back is a plugin that contributes nothing to *this* session, which is how a plugin says
"reaching nothing, so nothing to add" without a caller asking it which arm it is on. That is
`Reach`'s own bargain one layer up.

The cost, stated: a plugin whose whole contribution is a capability writes two objects where one
would have done, and has to be told that its constructor is called once per pass rather than once
per session.

## The lifecycle

**The set of plugins in force is a merge of two halves that become readable at two different
moments**, and every mistake available here is a question answered at the wrong one. Nothing knows
the whole set at process start: the repository's half cannot be read until its worktree exists, and
[the worker clones while a request handler never does](workspace.md#where-a-repository-comes-from),
so at the moment somebody presses the button there is no clone, no worktree, and no way to read a
file out of the repository without a network fetch on a POST somebody is waiting on.

| Moment | Where | What is decided there | What it can see |
|---|---|---|---|
| Process start | the lifespan | which **user** plugins are loaded: imported, dependencies checked, names refused for collision | the user's `config.yaml` |
| Session start | `Service.start` | the choice, and any grant somebody gave in the picker | the picker's answers |
| A session's first request | the worker, after planting | which **repository** plugins this session runs, recorded at turn 0 | the planted tree, and the grant |
| Every pass | the worker | the merge, and the capabilities built from it | the loaded half and the recorded half |
| Every render | a request handler | the cards and the sending answers | the same two |

**Only the repository half is recorded, and the asymmetry is the point rather than an oversight.**
They are not two answers to one question, so recording both would be inventing a symmetry the two
halves do not have:

- The **user** half is process state, exactly as the bundled tools already are. Nothing records that
  a session had `read` and `edit`; `Choice.isolation` is recorded and `reaching` derives the tools
  from it against whatever the process holds. A user plugin is the same shape, so adding or removing
  one reaches every session on its next pass, which is what upgrading this console already does.
- The **repository** half is a fact about one session that could only be learned by doing work,
  which is `turn:0:tree:0`'s own shape and the only other thing here like it. It is read once, at
  the first request, out of the planted tree, and replayed from the record on every pass after.

The order inside that first pass is therefore clone, plant, read the repository's file at the
planted tree, record what it declares, and only then build the agent. Nothing is circular: the
discovery finishes before `agent_for` is called, so a repository plugin contributes to the very pass
that found it.

**The merge happens in one place**, the way `reaching` turns an isolation into roots and a note. A
pass holds the loaded half and the recorded half and one call turns them into the plugins in force,
so nothing downstream asks which file a plugin came from. The merge rule is the one
[below](#the-merge-is-over-entries-never-inside-them): whole entries, user wins, and a repository
cannot shadow a name.

Two consequences worth stating rather than discovering:

- **A session started before a repository declared a plugin never gets it**, because its turn 0 read
  a tree that did not carry one. [Forking](forking.md) is how a session changes its mind, and it is
  the same answer `Choice` gives to every other question.
- **A page renders no repository card until turn 0 has landed.** That is an ordinary state rather
  than a gap: it is the same window in which a session has no worktree yet.

## What a plugin may contribute

### A card, declaratively

**A plugin describes a card; it does not draw one.** The contribution is a small closed vocabulary,
rendered by `pages.py` in the console's own idiom:

```python
type Cards = Callable[[Mapping[str, object]], Card | None]
```

where a `Card` is a heading and a sequence of rows drawn from a fixed set: a stated value, a switch,
a number with a unit, a link, an absence said out loud.

The alternative is letting a plugin return an `Element`, and it is worth recording because it reads
better than it works. It makes `markup.py`'s element type this console's public API, which then
cannot change shape again; and a card drawn by a plugin drifts out of step with the console's own
controls the first time a control is restyled, which is the whole subject of [the controls
rules](../philosophy.md#controls). A closed vocabulary keeps them in step by construction rather
than by anybody remembering.

**The cost, stated: a plugin cannot draw a control the vocabulary has no word for**, and the
vocabulary grows by somebody adding a word deliberately. That is the trade, and it is the right one
here, because the failure it prevents is a rail where two cards are visibly two different consoles.

It also keeps the gallery whole: a fixture produces a `Card` with no plugin loaded, no import and no
process, which is what [`scripts/gallery.py`](assets.md) rests on.

### How its tool calls read

A plugin contributing a tool wants its returns to read well, and the same answer applies: it
declares the **summary**, not the markup. What `panel_opening` needs is one line, and what
`block_element` needs is which of the shapes the body takes. A plugin says those; the panel is still
drawn here, so a plugin's call folds, anchors, and shuts from its frame exactly as `read` does.

### An answer in the sending menu

An `Answer` is [already the one value rendered three
times](composer.md#the-disposition), so a plugin adding one is one more entry in `sending_answers`
and nothing else. Three things have to change first, and the first is a blocker rather than a
tidy-up:

- **`mainplate.css` enumerates the answers by name**, one selector per `data-leading` value, because
  CSS cannot ask whether a descendant's attribute matches an ancestor's. A plugin cannot ship a
  ninth line, so a plugin-contributed mode today hides `Send` and reveals nothing: a composer with
  no primary button. The fix is to have the script mark the button it leaves standing rather than
  only the form it is in, so the rule is structural and one line. That stays inside [the leader
  rule](composer.md#leaders), because a presentational mark is not a label, a field name or a
  disposition, which are the three things the script must never hold.
- **`Disposition` is a closed set parsed at the boundary**, with an unrecognised value a refusal.
  With plugins it is built from the loaded set instead. The refusal stays: guessing puts a message
  in a conversation nobody addressed it to.
- **The word is the namespace, and the namespace is one session.** `Answer.named` is
  `leader.capitalize()`, so one word is the menu row, the leader typed after `/`, and the posted
  value. Two plugins claiming `keep` is a collision, refused **when a session's merge is composed**
  and never at startup: two repositories may each carry a `review` and both are correct, because no
  session ever holds both. Refusing process-wide would let the first repository somebody trusts take
  a word away from every other one.

## Two files, and what each may say

**The user's file and the repository's file are two schemas, not one schema read twice.** They hold
opposite invariants, so they are separate types, which is [the default-value
rule](https://lexi-lambda.github.io/blog/2019/11/05/parse-don-t-validate/) applied where it bites
hardest.

| | Where | May declare | Trusted |
|---|---|---|---|
| User | `$XDG_CONFIG_HOME/mainplate/config.yaml` | endpoints, the reference, plugins, their settings | you wrote it |
| Repository | `.mainplate/config.yaml` at the session's tree | plugins and their settings, nothing else | only once granted |

**A repository's file may not name an endpoint, a credential, an isolation level or a reference
database.** Not "does not today": the type has no field for one, so the escalation is
unrepresentable rather than checked. A repository that could raise its own session's isolation would
have found the shortest possible path from cloning a project to running unconfined.

**Both files name Python, and the difference is where it runs**, which is [the tier
split](#a-repositorys-plugin-is-ordinary-python-in-a-process-of-its-own): the user's runs here, a
repository's runs in a process of its own behind the confinement.

**A repository's plugin modules live under its own `.mainplate/`**, so a repository names a file it
carries rather than `/etc/…` or an installed package. That is a much smaller guarantee than it reads
as, and it is not what makes this safe: the process boundary is. It is worth keeping anyway, because
a plugin that reaches outside the tree it was read from is one whose recorded set no longer
describes what ran.

### The merge is over entries, never inside them

**Each plugin is enabled by exactly one file, with the whole of its settings from that file.** Where
both name a plugin, the user's entry wins entire and the repository's is ignored with a log line
saying so.

A deep merge is the tempting alternative and is wrong twice. It produces a configuration nobody can
predict without running it, which is the thing `parse_config` exists to prevent; and it lets a
repository set one field of a plugin you enabled, which is the escalation above wearing a smaller
hat. For the same reason **a repository cannot disable a user plugin**, and a name collision is
*not* a refusal: a repository that could refuse would be a repository that can stop you starting a
session.

The cost, stated: a repository cannot adjust one setting of a plugin you already configured. It is
all of that plugin's configuration or none of it.

## Trusting a repository's plugins

**A repository's plugin is code this console imports into its own process**, which holds
`config.yaml` with the credentials in it and runs outside the sandbox every other thing here runs
behind. That is categorically unlike [the guidance a repository supplies](guidance.md): an
`AGENTS.md` out of the worktree is text told to a model, so the worst it does is talk, where this is
execution.

### Read once, and never from a tree this console wrote

A session's model has `edit` over its worktree, so the file that declares a repository's plugins is
a file the model can write. Two rules keep that from being a way to run code in this process, and
neither is a hardening pass to add later.

**It is read once, at turn 0, and recorded as a step.** Every pass after that replays the record and
reads no file at all, which is `turn:0:tree:0`'s own bargain. Without this, a model writes a Python
file on turn 4 and this console imports it on turn 5 with the console's own privileges, outside the
sandbox: every isolation level in [`sandbox.py`](sandbox.md) undone by a tool call. The read happens
before `agent_for`, so at the instant it happens the worktree is exactly the planted tree and
nothing has run.

**It is read from the commit the repository supplied, and never from a tree this console
snapshotted.** This is the rule that is easy to miss, and reading "out of the recorded tree" sounds
like it covers the case when it does not. A snapshot is a tree a model wrote: it is captured with
`git add -A`, so a `.mainplate/plugins/` file the model created on turn 4 is *in* the tree recorded
for turn 5. [A fork plants at a recorded tree](workspace.md#what-a-forks-worktree-is), so a fork
that re-read its own worktree would load a plugin the parent's model authored, one fork away from
any session with files.

**So a fork inherits its parent's recorded set rather than reading anything**, exactly as it
inherits the choice. Only a session planted at a commit the *repository* provided, its `base` or the
default branch, ever reads the file at all.

**Without a grant nothing is read**, and the recorded set is empty for that session's life. Granting
trust afterwards reaches every session started after it and none started before, which is the answer
`Choice` gives to every other question. Forking is how a session started without one changes its
mind.

The cost, stated: **editing a repository's plugin has no effect on a session already running.** You
start a new session to pick it up.

### The grant is about the repository, and it is asked before anything is cloned

[The worker clones, never a request handler](workspace.md#where-a-repository-comes-from), so at the
moment somebody picks a repository there is no clone, no worktree and no way to know whether the
repository carries plugins at all without a network fetch on a POST somebody is waiting on.

So **the question is asked blind, in the picker, defaulting to off**: may plugins declared by this
repository be loaded. A repository that declares none makes the grant inert, and nothing had to be
fetched to find that out. Asking blind is what a path-scoped trust prompt in any other harness is
doing anyway; this one says so.

It is recorded **per repository**, keyed by `Repository.id`, in a table beside `sessions` in the
store's own file. It has nowhere else to live, by [`Tending`'s
argument](composer.md#handing-off-without-being-asked): the checkpoint keeps the value a key was
first given, so a grant could never be withdrawn, and `localStorage` is in a browser where the
worker that acts on this is another process.

Per repository rather than per session, deliberately. A question re-asked on every session is a
question answered yes without reading it, which is the failure mode a trust prompt has.

**The grant is what may be read; the turn-0 record is what was read.** A grant is mutable state
about a repository and moves when somebody moves it, where the record is a settled fact about one
session and never moves at all, so revoking a grant cannot rewrite what an old session ran under and
a session stays readable on its own. Neither is [a second copy of something that
changes](../philosophy.md#the-one-idea), because they are answers to two different questions.

The wrinkle, stated: it is the one control in the picker that writes a fact about something other
than the session being started, so picking the same repository again finds it already on. The
alternative, a grant scoped to one session, is the re-asking above.

## One process, many repositories

One console answers sessions on many repositories at once, and the grant above is per repository. So
the question the grant does not answer: **what keeps one repository's plugin away from another
repository's session?**

**Nothing does, if the plugin is Python running in this process, and the grant must not be described
as though something did.** It is a *consent* gate, saying somebody meant to run this code, and not
an *isolation* boundary. Once a repository's module is imported, it holds `sys.modules`, and from
there `mainplate.config` with the credentials in it, the store with every other conversation in it,
and every other repository's plugins to monkeypatch before they run. Trusting one repository is
therefore trusting it with all of them.

**That is why a repository's plugin does not run here**, and the next section is the answer. What
follows first is the pair of collisions in-process loading produces, because the console still loads
the user's own plugins that way and one of the two is silent.

### Two collisions, and only one of them is about names

They look like one problem and have different fixes:

- **The name in `config.yaml` is session-scoped**, so two repositories each carrying a `review` is
  not a collision at all: no session holds both, and the merge is [where a genuine one is
  refused](#an-answer-in-the-sending-menu). Nothing about a name needs to be unique across the
  console.
- **The module name is process-scoped, and that one is real.** `sys.modules` has one entry per name
  for the life of the process, so two plugins' `review.py` loaded under the same module name are one
  module: the second import wins, the first plugin silently runs the second one's code, and a later
  plain `import review` anywhere gets whichever landed last. No error is raised at any point.

So **every in-process plugin is loaded under a synthetic name**, through
`importlib.util.spec_from_file_location` and an explicit `sys.modules` entry, never as the bare name
the file happens to have, with its sibling imports resolving inside that synthetic package.

**And `sys.path` is never appended to, which is the rule with the sharpest edge.** Put a plugin's
directory on the path and any file in it shadows any module not yet imported, for the whole process:
a `json.py` beside the plugin becomes *the* `json` for this console and for everything else running
in it. There is no version of the path manipulation that is safe enough to be worth the convenience.

Both rules stop mattering for a repository's plugin the moment it has a process of its own, since
its `sys.modules` is then its own and it may shadow whatever it likes in there. They are kept
because the user tier is still loaded here, and because a plugin process is a thing this console
launches rather than a rule it can forget.

### A repository's plugin is ordinary Python, in a process of its own

**The constraint is on where code runs, not on whether an extension is code**, and running those two
together is the mistake to avoid here. "A repository must not run Python in the console process"
does not imply "a repository must declare itself as data", and taking the second from the first ends
at a schema language in YAML: an argument spec in OpenAPI, a settings model built at runtime out of
it, a card in some template dialect, and a tool body that is a string of shell. That is a worse
programming language than Python, written in YAML, with no type checker and nowhere to put a
breakpoint. Being able to just write code is the thing worth keeping.

So a repository's plugin is **a `.py` file somebody writes normally, run in a process of its own,
behind the confinement `bash` already uses**. The YAML names which modules to load and what their
settings are, exactly as `config.yaml` already names endpoints, and no part of a plugin is expressed
in it.

**The contract that crosses is one this console already serializes.** `turn:{n}:messages` is written
with `ModelMessagesTypeAdapter`, so a message list crossing a pipe is the round trip the checkpoint
already makes on every turn rather than a serialization project. The same holds for the rest of what
the event-shaped hooks take and return: a `ModelResponse`, a `ToolCallPart`, a `ToolDefinition`, the
validated arguments.

**Which is where the surface splits, and the split is found rather than imposed.** The `before_*`
and `after_*` hooks are called and react, so they cross a process boundary intact. The `wrap_*`
hooks take a `handler`, which is a continuation to call inward, and a continuation is the one thing
that cannot be serialized. Flattening those two into one shape to make the tiers look alike is the
[symmetry that is not simplicity](../philosophy.md#the-words), so the asymmetry decides the tiers
instead:

| | Runs | Gets | Dependencies |
|---|---|---|---|
| User plugin | in this process | the whole capability surface, `wrap_*` included | this console's environment |
| Repository plugin | its own process, confined | the event half, cards, answers, and tools it handles itself | its own, per [PEP 723](https://peps.python.org/pep-0723/) |

**The tier is decided by who wrote the file**, which is the question the grant already asks, so
there is no third rule to remember.

**The console holds one `Plugin` either way**, which is what keeps the tier from reaching past this
section. A user plugin's `capability` is the constructor it wrote; a repository plugin's returns a
**proxy** capability implementing the event hooks by forwarding them down the pipe, so `agent_for`
takes a list of capabilities and never asks which tier produced one. `settings` crosses the same
way: the plugin declares an ordinary `BaseModel` in its own process and the console asks it for
`model_json_schema()`, so a schema is data because a schema *is* data, and nothing builds a model
out of YAML at runtime.

Two designs are worth recording as rejected, because both read better than they work:

- **A subinterpreter per repository.** [PEP 734](https://peps.python.org/pep-0734/) is in the
  standard library on the interpreter this runs on and gives each repository its own `sys.modules`
  in one process. It buys nothing over a process here: only picklable data crosses either way, so
  the same hooks are reachable, and what a process adds on top is the confinement and a crash that
  does not take the console with it.
- **A repository contributes data and never code.** It dissolves the isolation problem rather than
  containing it, and the YAML language above is where it ends. Recorded because it stays tempting
  right up until somebody writes the second tool.

The cost, stated: **a repository's plugin cannot wrap a model request or a tool execution**, so
anything that has to sit *around* one rather than before or after it is a user plugin. And an event
hook is now an IPC round trip, which is nothing beside a model request and is worth measuring beside
a tool call before anything is built on it.

**This is also what makes the grant mean something.** In-process it was consent and could never be
containment; with a process boundary and the existing confinement it *is* containment, so a
repository's plugin cannot read `config.yaml`, cannot reach the store, and cannot touch another
repository's session. What the section above describes is then true of the **user** tier alone,
where it is unavoidable and fine, because you wrote that code.

## Settings

Three different things get called settings here and they stay apart:

- **Process configuration** is `Settings` and `config.yaml`, read once at startup into a frozen
  typed value. A plugin's own configuration is this, under `plugins:`, parsed at the boundary.
- **A session's settled choice** is `Choice`, written before the first message and fixed for life.
  It is already one JSON value, so a plugin sub-field there costs nothing new.
- **A session's mutable state** is `Tending`, and it is the one that has to change.

### One blob, and a plugin owns a sub-field of it

`hands_off` and `reserve` are columns, and the columns cannot grow: a plugin cannot run `ALTER
TABLE`, and `ADDED` is five migrations long already. So the two become one `settings TEXT` column
holding a mapping keyed by plugin name, and `SELECTION` reaches into it with `json_extract` exactly
as it already reaches into a session's `choice`.

Two costs, both real:

- **A whole-blob write clobbers.** `tend` writes both columns in one statement so the pair cannot be
  half applied; a blob written whole means two plugins saving at once lose one of the saves. The
  write is therefore `json_set` against one sub-field in a single `UPDATE`, which touches only that
  plugin's key and is atomic in the statement.
- **`STRICT` stops covering it.** The column is unchecked text and the invariant moves to a
  per-plugin parse at the boundary. That is the house pattern anyway, but a typed column really is
  being given up.

**`Tending` moves into the blob by becoming a bundled plugin's settings, not by sitting beside
them.** Two mechanisms for one question is the thing this console removes wherever it finds one.
Auto-handoff is also the proof that the interface is enough, because it is every contribution at
once: two settings, a rail card, a composer answer, a tool, and a decision made at a pass boundary.
An interface that cannot express it is not finished.

## Which parts of the harness become plugins

The reverse implication is that the console ships as default-enabled plugins of its own. That is
right for some of it and wrong for most of it, and one question sorts them: **can you describe
mainplate with this absent and still have mainplate?**

- **`StepwiseDurability`: no.** It is the checkpoint, and the checkpoint is the conversation. It is
  not a plugin, it is what plugins run inside.
- **The file tools' isolation table: no**, and making it one breaks a property that holds today.
  [Which tools a session gets is a pure function of its recorded
  `Choice.isolation`](tools.md#which-tools-a-session-gets). Route it through a registry and it
  becomes a function of the choice *and* whatever the configuration said at the moment of the pass,
  so a replayed pass builds a different agent than the one whose answers are recorded.
- **`hand_off`: no.** A tool definition sits above the system prompt in the cached prefix, so
  [introducing one late invalidates the whole conversation beneath
  it](composer.md#handoff). It is in every prefix on purpose.
- **Auto-handoff policy, the shelf, search, `run`: yes.** Each is a unit somebody could sensibly not
  have.

**For the bundled set, this buys uniformity and not removability**, and saying so is better than
shipping a registry that implies the console can be turned off. What uniformity is worth is that a
plugin somebody else writes is not second-class, and that the interface is proven by the things
already using it rather than by the one example written to demonstrate it.

**Turning a bundled contribution off reaches every session on its next pass**, because it is the
user half and [nothing records that](#the-lifecycle). That is the behaviour this console already
has: a session running while somebody upgrades it gets whatever tools the new process builds. The
cost, stated: a tool definition sits above the system prompt in the cached prefix, so switching one
off mid-conversation invalidates the whole prefix beneath it and the next turn pays a full uncached
read of the window. That is a real bill rather than a warning, and it is the same arithmetic that
keeps `hand_off` unconditional.

## Loading

**The plugins are named in `config.yaml`, one list, and are never found by globbing a directory.**

`parse_config` already refused this reasoning once: it reaches for `safe_load` over `load` because
full YAML can name Python types to construct, so "loading a configuration file with it would make
editing that file equivalent to running code". A glob over `.mainplate/plugins/*.py` makes *dropping
a file into a directory* equivalent to running code in the process holding the credentials. A named
list is still a file whose edit runs code, and that is the cost; the difference is that somebody
wrote the name down.

`Plugins` is built the way `build_wires` is built: **eagerly, in the lifespan, before the store is
opened, refusing by name**, and then injected into `Service` rather than reached for. Entry points
scanned at import are the implicit wiring this codebase does not do, and a plugin that cannot be
imported is [a choice somebody can select and then cannot
use](../philosophy.md#refusing-at-startup-or-promising-not-to-raise), which is the arm that refuses.

**Every import goes through `spec_from_file_location` under a name this console synthesized, and
`sys.path` is never appended to.** Both halves are load-bearing rather than fastidious, and [the
section on one process holding many repositories](#one-process-many-repositories) is why: a bare
module name is a process-wide slot two repositories can land in, and a directory on the path shadows
any module in the interpreter that has not been imported yet.

**A repository's plugins are the exception to eager, necessarily**, since [the tree they are in does
not exist until a pass has planted it](#the-lifecycle). One that cannot be imported there stalls
that session naming the plugin, and never the process: a broken plugin in one repository must not
stop the console starting, which is `forge.offers`'s promise rather than `catalogue.discover`'s
refusal, one layer along. The same sentence covers a plugin recorded at turn 0 that a later pass
cannot load, which is what a revoked grant or a missing distribution looks like from inside a
session.

## Dependencies a plugin brings

**The tier split answers this too, and it answers the two tiers differently**, which is why the
question looked harder than it is while both were in one process.

**A repository's plugin declares its own, with [PEP 723](https://peps.python.org/pep-0723/), and
this works because the plugin is launched rather than imported.** Inline script metadata builds an
environment for a script `uv run` starts, so a plugin that *is* a process gets exactly the mechanism
the block was written for, and its dependencies land in an environment that is nobody else's. That
is worth stating plainly because the opposite is true one tier up: **no arrangement of a PEP 723
block changes what is importable in a process that is already running**, so an in-process plugin
cannot get its dependencies this way, and reaching for the block there is the mistake to catch in
review.

So the repository tier gets dependency management for free out of the boundary it was given for
security, and the cost is a per-repository environment to resolve and cache. `uv` does the caching;
what this console decides is when a plugin process is started and how long it is kept.

**A user plugin's dependencies are the console's own environment**, and that stays the person's to
install, which is reasonable because they are already the person running it. What is worth having
there is the *check* rather than the install: resolve each requirement against `importlib.metadata`
at startup and refuse, naming the plugin and the missing distribution, which turns an `ImportError`
in a worker log into a sentence naming the file to edit.
