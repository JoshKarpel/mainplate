# mainplate

A coding agent that keeps its own sessions.

A chat console in front of a [Pydantic AI](https://ai.pydantic.dev) agent, where a conversation is
a durable workflow rather than a process's memory. Ask it something, kill the server, start it
again: the session is where you left it, and the reply that was in flight is answered rather than
lost.

It is early and it is experimental. A session that picks a repository gets a git worktree of its
own and the agent can read, edit and create files in it; the work so far is mostly about the
substrate underneath, because a coding agent that forgets what it was doing when its process dies
is the failure worth designing out first.

## Running it

Write `$XDG_CONFIG_HOME/mainplate/config.yaml` (usually `~/.config/mainplate/config.yaml`), then
start it:

```yaml
default: anthropic

endpoints:
  anthropic:
    format: anthropic
    api_key: sk-ant-...
```

No models are listed, because none are configured: mainplate asks each endpoint what it serves and
offers whatever comes back.

```console
$ uvx mainplate serve          # once there is a release; from a checkout, `just serve`
```

Then open <http://127.0.0.1:8100>. Sessions are stored in `mainplate.db` in the working directory,
so pointing the console at a different project is `--database`, and reading a session back is
opening the same file again.

`mainplate serve --help` lists the flags. They are the few worth reaching for at a shell; every
other setting is a field of `Settings` read from a `MAINPLATE_`-prefixed environment variable, so
`MAINPLATE_REFRESH` sets how often the models are re-read and `MAINPLATE_PASSES` how many sessions
are answered at once. Read `settings.py` for the whole set, each with what it is for.

### Endpoints

An **endpoint** is where requests go, which API format is spoken there, and how to authenticate. The
models are deliberately not part of one, and are not written down anywhere: mainplate asks the
endpoint's own model-list API what it serves. That is the same call whether the endpoint is
`api.anthropic.com` or a gateway fronting five vendors, and it means the picker is never a list
somebody has to remember to update.

`format` names the API shape rather than the vendor, because one hostname often answers both and
each reaches models the other does not. It also decides what `url` has to be: the Anthropic SDK
appends `/v1/messages` to what it is given, so it wants the host, and the OpenAI SDK appends
`/chat/completions`, so it wants the host and `/v1`.

A new session starts on whichever model the default endpoint listed first, which for most gateways
is their newest. Set `default_model` at the top level to name one instead; a name the endpoint has
since dropped falls back to the first rather than stopping the console. `default_thinking` names the
level, and defaults to saying nothing about thinking at all.

## Starting a session

You pick what a session is answered on when you create it, ordered widest first: its **workspace**,
which is a repository this console can reach, or no files, or this whole machine; whether its
commands may reach the **network**; whether the repository's own **plugins** run; the **endpoint**
and **model**; and a **thinking level**. All of it is fixed for the session's life, and **forking is
how it changes**.

Creating one takes you to its page, where it plants its worktree and reads what each tier of plugins
*declares* out of files. It runs none of them: a plugin is a program, so the step you pass through
next is where you say which ones may be executed. Every declared plugin is listed with its path,
grouped by where it came from, with a switch apiece; **Load plugins** runs exactly the ones left on,
all at once, and lands you in the conversation. Which plugins a session runs is settled from there,
because a tool definition leaving the cached prefix invalidates the whole conversation beneath it,
and **forking is how it changes**. What each plugin is *set to* stays changeable, on its own card in
the rail.

Pick a repository and two more fields appear: **where in it to start** and **what branch to start
there**, both optional. Left blank the worktree is checked out at the repository's default branch as
it stands now, on a branch named after the session (`mainplate/349e2f1e`), so a `git commit` from
the box under the conversation has somewhere to live and `git push origin HEAD` does the obvious
thing. The starting point is a search over the branches the repository actually has, read from the
repository rather than from this console's copy, so it works on the very first session you start on
one.

Starting a session is also when this console's copy of a repository catches up: it clones once and
nothing else refreshes that, so planting a worktree fetches first.

**Forking keeps the original readable.** Every turn opens with a rule carrying a `fork` link:
following it makes a new session that inherits the turns before that one, on whatever endpoint,
model and thinking level you pick, and asks that turn's own question again with the message editable.
The sidebar draws the result as a tree. A fork inherits its parent's repository, because re-asking a
turn against different files is a different question wearing the same words; a session working in
*no* repository is the exception, and forking one is how you pick a repository up.

## What a model costs

No gateway reached so far publishes a price anywhere in its model list, and what it does publish is
uneven: an endpoint describes its own vendor's models in detail, forwards somebody else's record for the
ones it resells, and says nothing at all about the rest. So the facts on a model card come from a
reference database rather than from the listing, which is what lets two models on one page be
compared.

It is off unless you ask for it. Name one at the bottom of `config.yaml`:

```yaml
model_reference:
  source: https://models.dev/api.json
  format: models.dev
```

`source` is fetched when it is a URL and read when it is a path, so a machine with no outbound
access can point at a file it already has. Delete it and mainplate calls nobody but the gateways
your own endpoints name.

It can never stop the console starting: a database that will not load costs a card its numbers and
nothing else. A model it has no record of says so on its card, and a console with no reference
configured says nothing, because nothing was looked up.

Both the reference and the model list are read before the console takes traffic and refreshed on a
timer after that (`MAINPLATE_REFRESH`, fifteen minutes by default), so rendering a page never causes
a request to a gateway and a model that appears at the provider reaches the picker without anybody
restarting anything.

Credentials live in that file rather than in the environment. A key read from a `0600` file and
handed to the SDK never becomes an environment variable, so it is not inherited by child
processes, not in `/proc/<pid>/environ`, and not in a crash dump of anything but this process. An
endpoint that names neither `api_key` nor `url` falls back to the SDK's own environment
variable, which is what the SDK does for itself.

### On exe.dev, no key at all

An [exe.dev](https://exe.dev) VM with the built-in
[LLM integration](https://exe.dev/docs/integrations-llm.md) reaches Anthropic, OpenAI, Fireworks,
and xAI through `https://llm.int.exe.xyz` with **no credential on the box**: exe.dev injects one at
its own edge. That is the best version of the secrets story available here, because there is
nothing to store, rotate, or leak.

`mainplate install` finds it for you. It asks the
[reflection integration](https://exe.dev/docs/integrations-reflection.md) which integrations are
attached, writes keyless endpoints per LLM integration it finds, and says so:

```console
$ just install
mainplate is installed and restarted
  found    exe.dev llm integration 'llm' at https://llm.int.exe.xyz
  console  http://127.0.0.1:8100
  endpoints /home/you/.config/mainplate/config.yaml
```

One hostname gets two endpoints, one per API format, because each reaches models the other does not.
`llm-anthropic` offers every Claude and every Fireworks model, all answered over `/v1/messages`;
`llm-openai` offers GPT, Grok, and Fireworks again over `/v1/chat/completions`. Between them a
default VM offers around seventy models with nothing configured.

Off exe.dev the lookup finds nothing and the install writes a template to edit. Either way an
existing `config.yaml` is never overwritten.

`just demo` runs the same console on a throwaway database, for poking at a page without touching
real sessions.

## Leaving it running

`mainplate install` converges a user systemd unit and restarts the service onto the interpreter
that ran the command, so an install means "the running service is this installation". Run it again
after changing anything; from a checkout, `just install` syncs first so the unit points at an
environment that has what you just added.

```console
$ just install         # or `mainplate install --port 8100`
$ just logs            # journalctl --user -u mainplate -f
$ just uninstall       # keeps the settings and the sessions
```

The install prints where its files are. `config.yaml` is the one to edit, and
`environment` beside it carries any `MAINPLATE_*` process setting. Both are created `0600` on the
first install and neither is ever overwritten.

With no usable endpoint, or with one no endpoint will answer a model list for, the service fails at
startup and restarts every five seconds: the endpoints are built and asked what they serve before
anything binds. That is deliberate: a console that could answer nothing has nothing honest to
serve, and failing at boot is louder than failing on the first message.

Sessions live at `$XDG_DATA_HOME/mainplate/mainplate.db` rather than in whatever directory you
installed from, since a service has no meaningful working directory.

## How a session survives

The whole design is one sentence: **the checkpoint is the conversation**. There is no messages
table, and the server holds no session state. What has been said is what has been recorded, so a
page renders the checkpoint, a crash resumes from it, and a second process reading the same file
sees exactly what the first one did.

A session is a durable workflow under
[`without-durability`](https://without.help/without-durability/), over its
[SQLite store](https://without.help/without-durability-sqlite/), and it has an **inbox**: everything
you do to it from the page is an append, a message or a command to run in its worktree. The pass
answering it suspends until there is something there, so nothing polls, no pass is held open, and
the wait outlives the process that was waiting.

**Nothing decides in advance which turn a message lands in.** Type while a reply is coming and the
pass folds your message into the request it is about to make; type a moment later and it opens the
next turn. Neither the page nor the handler has to guess, because the pass is the only thing reading
at the instant the answer is true.

A pass is **one live model request and the tool batch behind it** rather than a whole turn, so a
turn of forty round trips is forty passes and the lease bounds one round trip instead of betting on
how long the longest conversation might run. Every model request and every tool call is a recorded
step, so a pass that reaches the provider and then dies does not pay for that answer twice, and a
tool that already read a file is not run again against a directory that has moved since.

## How the agent edits files

What a session's tools reach is one of the things it picks when it is created. A session working in
a repository gets `list`, `read`, `edit` and `create` over its own git worktree and a scratch
directory beside it, refusing any path outside the two. One working on the whole machine gets the
same four with no such boundary. One reaching nothing gets no tools at all, which is what this
console was before there were repositories: a place to talk.

Anywhere there are tools there is also `bash`, wherever `bubblewrap` is installed to confine it.
Every command runs in a mount namespace of its own holding exactly what that session reaches and a
read-only system, so there is no home directory and no configuration of the console in it, and the
network is off unless the session asked for it. Inside a worktree the repository's git objects go in
read-only: `status`, `diff`, `log` and `blame` all answer, while `commit` and `stash` fail. That is
deliberate, because the conversation is how work is recorded here and committing is yours to do.
**Run** in the composer is where you do it: the same command from there runs outside all of this, as
you, in the same worktree.

`list` asks git what is there rather than walking the directory, so a `.gitignore` is obeyed and an
installed environment never reaches the model, while a file the agent itself just wrote does. A
directory past the depth you asked for is summarised by a count rather than opened.

**A line is addressed by a hash of its own content, not by its position.** A read puts a four-letter
anchor in front of every line:

```text
app.py, 6 lines

cxec│def greet(name):
infr│    return f"hello {name}"
----│
----│
vhvn│def farewell(name):
kxpe│    return f"bye {name}"
```

A line number is the one address that cannot fail: an edit above shifts everything below it and `47`
still resolves, so a stale line number silently edits the wrong place. An anchor either resolves to
exactly one line or does not resolve at all, so the same mistake is a refusal that says to read the
file again. It also means the model never retypes the text it is replacing, which is the expensive
half of a search-and-replace edit. Nothing is stored between calls, and an `edit` takes a list of
operations applied against one reading of the file, so a batch that contradicts itself is refused
entire rather than half-applied.

There is deliberately no tool that overwrites a whole file. `create` refuses a path that already
exists, because a tool that rewrites a file wholesale is the escape hatch that makes all of this
pointless: the first refused edit becomes a full rewrite, discarding whatever had not been read.

## The console

Server-rendered HTML with [htmx](https://four.htmx.org/), built from
[`without-html`](https://without.help/without-html/) node trees. The stylesheet, the script, and
htmx are all served from the process rather than a CDN, so a console on a machine with no route
out still renders.

A page holds **one connection**, open for as long as the page is, and the server sends the
conversation down it whenever the session records anything. Every message is a whole current render
rather than a delta, which is what makes a dropped connection cost nothing and a reconnect need no
replay, and each names the region it is for, so a second region joins the same connection rather
than opening another. A render is *morphed* into the page rather than replacing it, so what a reader
has done to the conversation, a panel or a tool call they unfolded, a command they put away, a
search, the place they had scrolled to, survives an update arriving.

**A turn is drawn as it happens.** The responses and tool results behind a running turn are already
in the checkpoint, recorded step by step so a resumed pass does not pay for them twice, so the page
reads those rather than waiting for the turn to finish: reasoning appears, then a call with its
arguments, then its result, then the next request. Nothing is stored to make this work and nothing
is streamed from the provider; it is the same checkpoint, read sooner.

A turn is drawn as **panels**, a coloured edge per run of one kind within one request, with the
person's message, the model's reasoning, its calls and its answer each in their own. The palette
runs on one axis, cool for what reached the model and warm for what it produced. **Every panel
folds, from its own row**, so the dock's fold-everything button turns a finished conversation into
its own outline; shut, a row carries the front of what is in it.

A **rule** stands at every round trip, carrying what is true of that request rather than of any
panel in it: the worktree it was made against, how long it took, what it spent in tokens and money,
and a fold showing the JSON the checkpoint actually holds for it. Since the checkpoint *is* the
conversation, that is the state itself rather than a debug view of it. The rule's own line is a
**gauge** of how much of the model's context window the request carried, filled from the left and
shading toward red, so scrolling down a long conversation shows the line lengthen and warm. What a
turn cost is an estimate from published rates rather than a bill, since no gateway reports what it
actually charged; the session's total sits under the message box, and above the box is whether the
provider still holds this conversation's prefix and what re-sending it costs with none of it cached.

Three panels say what the model was *told* rather than what anyone in the conversation said: the
**system prompt** every request in a stretch of context carried, **guidance**, a repository's own
`AGENTS.md` for a directory handed over at the moment a tool reached into it, and a **note**, which
is a message a plugin asked for. A note carries the word its plugin put on it and the weight of ink
it asked for, so a pre-commit failure and a handoff document do not read alike halfway down a
transcript.

Beside the conversation is a rail: find-and-step search, a key that filters by kind and doubles as
the colour legend, a dock that steps through the transcript, a shelf for text you have written and
not sent, when this session hands itself off, and a light/dark/system theme. Everything there is an
enhancement: with JavaScript off the console still renders, still posts messages, still hands off,
and every panel is still a fold that opens.

**It reads on a phone.** The rail folds away behind a clasp, and the session list becomes a strip of
chips across the top so the conversation gets all but about a twentieth of the height.

### What you can do with what you typed

**Send** puts it into the conversation now. If a reply is already coming, that means **steering**:
the message is put to the model in the turn it is answering, so it shapes that answer rather than
the one after it. You are not asked which, because you could not answer: the page you typed on was
drawn from a checkpoint that has moved since. It goes in the session's queue and the reply takes it
if it is still running when it looks; a message nobody took is still in the queue, and the next turn
opens on it.

The caret beside Send opens the rest. Each has a name you can type instead: `/` at the start of an
empty box opens the same list, and a space after the whole word takes it, so `/fork ` puts the box
in that answer's mode with the button beside it saying `Fork` rather than `Send`. Nothing is ever
inferred from what you typed, so what you are about to press always says what it does.

- **Next** queues the message behind the reply that is coming instead of putting it to the model
  now. It is the one thing the record cannot decide for you.
- **Forget** asks it with the model's context cleared, for when a conversation has wandered and the
  backlog costs more than it is worth. What is cleared is the context and nothing else: everything
  said so far stays on the page, keeps counting toward what the session has cost, and still comes
  across if you fork.
- **Handoff** is the same family one step along. Where `Forget` drops the backlog, this has the
  session write it down first, checking the working tree rather than recalling it, and start again
  from that document. It also happens without being asked: a session keeps a **reserve** of the
  window free for writing one, and hands itself off once the conversation reaches it. On by default,
  which is safe only because a handoff destroys nothing. All of it is a plugin, so every word of it
  can be replaced with your own.
- **Aside** steps out into a side conversation you mean to come back from and **Fork** starts one
  you do not; from either, **Parent** sends what is in the box back into the conversation you left.
  That is a message rather than a merge, which is what makes it honest: the turns you took on the
  side were asked against a different history.
- **Keep** puts it on the shelf and clears the box. Pressing a kept note adds it back rather than
  replacing what is there, so several assemble into one message. It lives in your browser, so it
  does not follow you to another machine yet.
- **Run** is the one that is not a message. It runs what is in the box in this session's worktree,
  as *you* rather than as the agent, and the model is never told, so committing at the end of a
  session costs it no context and reaches no provider. It is still recorded, so it draws as a
  `command` panel with what it exited with, survives a reload, and a fork carries it. `! ` into an
  empty box is its own shorter key, and the box stays a command box after each run.

Shift-Enter sends; plain Enter breaks the line. That way round because a message here is prose that
often wants a second paragraph and a fenced block, and a box where the obvious key sends is a box
you cannot write one in.

## What it does not do yet

Named plainly, because they are the next things rather than omissions nobody noticed:

- **No allowlist for the network, only on or off.** An allowlist holding a code forge holds every
  gist on it and one holding a package registry holds a package anybody can publish, so what it
  would buy is a defence against a repository's own build script and little against anything
  deliberate, at the price of a proxy in front of every command.
  Narrowing it would be that proxy, not a longer setting.
- **Nothing serialises `bash` against an edit.** Two `edit` calls at one file are serialised, so a
  batch of them cannot lose each other's work, but a shell command writing a file while an edit
  writes it is outside what that can see: the paths a command touches are not knowable before it
  runs.
- **Two API formats, not every format.** An endpoint's `format` takes `anthropic` or `openai`, which
  between them cover most gateways. A third is one `Wire` class saying how to name a model over that
  format, how to ask it what it serves and what it has to be told to reuse a conversation's prefix,
  plus an extra on `pydantic-ai-slim`.
- **A session cannot be moved to another endpoint.** Removing an endpoint that sessions use leaves
  them readable and stuck; the page names the endpoint so putting it back is obvious. Forking one
  onto an endpoint that still exists is the way out. A model dropping out of the picker is *not* that
  case and does not stop a session, since an endpoint routes more ids than it advertises.
- **Going back means forking, never rewinding.** A session that picked a repository works in a
  worktree of its own, the tree is recorded before every model request, and a fork is checked out at
  the tree the forked turn saw. There is deliberately no way to put an *existing* session's files
  back: the branch gets the old files and the original stays readable beside it, where truncating a
  session in place would destroy history that its own branches point into.
- **One forge, and it is exe.dev's.** `ExeDevGitHub` reads the GitHub integrations attached to a
  VM. Anywhere else it reaches nothing, so the picker does not appear and the console is a place to
  talk. Reaching GitHub through an App, so this works off exe.dev, is another class behind the same
  interface.
- **Nothing removes a worktree.** A session's clone and worktree stay after it, because nothing
  deletes a session either.
- **No streaming.** A streamed model request inside a session raises rather than running
  unrecorded, so the refusal is loud rather than a silently unrecorded call. Closing it means
  recording the stream's events alongside its response.
- **One machine.** SQLite means every process sharing this store shares a filesystem. That is the
  deployment this is for rather than a defect; a second machine means another store.
- **`install` is Linux only.** It renders a user systemd unit and knows no other service manager.
  `serve` itself is portable, so elsewhere it is a foreground process and whatever you already use
  to keep one running.
- **Nothing deletes a session.** They accumulate, and the only way to remove one is the file.

## Why it is built this way

The reasoning is written down rather than left to be inferred from the source:

| | |
|---|---|
| [Philosophy](https://joshkarpel.github.io/mainplate/philosophy/) | The one idea everything rests on, and the rules new work is measured against |
| [Design](https://joshkarpel.github.io/mainplate/design/) | How each part works, what it costs, and which alternatives were tried and are not worth trying again |
| [Maintaining](https://joshkarpel.github.io/mainplate/maintaining/) | The toolchain around the source, for working on the repository itself |

## Why the name

The mainplate is the base plate of a watch movement: the flat piece everything else is mounted to
and located by. It does nothing on its own.
