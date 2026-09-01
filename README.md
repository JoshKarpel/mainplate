# mainplate

A coding agent that keeps its own sessions.

A chat console in front of a [Pydantic AI](https://ai.pydantic.dev) agent, where a conversation is
a durable workflow rather than a process's memory. Ask it something, kill the server, start it
again: the session is where you left it, and the reply that was in flight is answered rather than
lost.

It is early and it is experimental. Today the agent has no tools, so what it does is chat; what
the work so far is about is the substrate underneath, because a coding agent that forgets what it
was doing when its process dies is the failure worth designing out first.

## Running it

Write `$XDG_CONFIG_HOME/mainplate/config.toml` (usually `~/.config/mainplate/config.toml`), then
start it:

```toml
default = "anthropic"

[profiles.anthropic]
provider = "anthropic"
api_key  = "sk-ant-..."
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

### Profiles

A **profile** is where requests go, which wire is spoken there, and how to authenticate. The models
are deliberately not part of one, and are not written down anywhere: mainplate asks the endpoint's
own model-list API what it serves. That is the same call whether the endpoint is `api.anthropic.com`
or a gateway fronting five vendors, and it means the picker is never a list somebody has to
remember to update.

`provider` names the wire rather than the vendor, because one hostname often answers both and each
reaches models the other does not. It also decides what `base_url` has to be: the Anthropic SDK
appends `/v1/messages` to what it is given, so it wants the host, and the OpenAI SDK appends
`/chat/completions`, so it wants the host and `/v1`.

A session records the profile, the model, a thinking level *and* a repository, at the moment it is
created, and all four are fixed for its life. The profile is what carries the wire, which is why it is recorded
rather than looked up later: the same model id can sit behind two wires, and the two serialize a
conversation differently. You pick all three under the composer on the new-chat page, where the
models are grouped by the vendor each comes from; after that the session says what it is on rather
than offering a control that could not change it. A conversation that switched model halfway would
replay its recorded answers from one and continue on another, so what the transcript shows and what
the next turn reasons from would have different authors.

**Forking is how you change your mind**, and it keeps the original readable. Every message carries a
`fork` link: following it makes a new session that inherits the turns before that one, on whatever
profile, model and thinking level you pick, and asks that turn's own question again. The message
comes across editable, so a fork is equally a way to rephrase. The sidebar draws the result as a
tree, each fork nested under what it came from and labelled with the turn it left at.

A new session starts on whichever model the default profile listed first, which for most gateways is
their newest. Set `default_model` at the top level to name one instead; a name the endpoint has
since dropped falls back to the first rather than stopping the console. `default_thinking` names the
level, and defaults to saying nothing about thinking at all.

The list is read once before the console takes traffic and refreshed on a timer after that
(`MAINPLATE_REFRESH`, fifteen minutes by default), by a task that answers no requests. So rendering
a page never causes a request to a gateway, and a model that appears at the provider reaches the
picker without anybody restarting anything. A refresh that fails keeps the models discovered
earlier and logs why; a *first* read that fails is a startup failure naming the profile, because a
console with an empty picker can answer nothing.

Credentials live in that file rather than in the environment. A key read from a `0600` file and
handed to the SDK never becomes an environment variable, so it is not inherited by child
processes, not in `/proc/<pid>/environ`, and not in a crash dump of anything but this process. A
profile that names neither `api_key` nor `base_url` falls back to the SDK's own environment
variable, which is what the SDK does for itself.

### On exe.dev, no key at all

An [exe.dev](https://exe.dev) VM with the built-in
[LLM integration](https://exe.dev/docs/integrations-llm.md) reaches Anthropic, OpenAI, Fireworks,
and xAI through `https://llm.int.exe.xyz` with **no credential on the box**: exe.dev injects one at
its own edge. That is the best version of the secrets story available here, because there is
nothing to store, rotate, or leak.

`mainplate install` finds it for you. It asks the
[reflection integration](https://exe.dev/docs/integrations-reflection.md) which integrations are
attached, writes keyless profiles per LLM integration it finds, and says so:

```console
$ just install
mainplate is installed and restarted
  found    exe.dev llm integration 'llm' at https://llm.int.exe.xyz
  console  http://127.0.0.1:8100
  profiles /home/you/.config/mainplate/config.toml
```

One hostname gets two profiles, one per wire, because each reaches models the other does not.
`llm-anthropic` offers every Claude and every Fireworks model, all answered over `/v1/messages`;
`llm-openai` offers GPT, Grok, and Fireworks again over `/v1/chat/completions`. Between them a
default VM offers around seventy models with nothing configured.

Off exe.dev the lookup finds nothing and the install writes a template to edit. Either way an
existing `config.toml` is never overwritten.

`just demo` runs the same console on a throwaway database, for poking at a page without touching
real sessions.

### Leaving it running

`mainplate install` converges a user systemd unit and restarts the service onto the interpreter
that ran the command, so an install means "the running service is this installation". Run it again
after changing anything; from a checkout, `just install` syncs first so the unit points at an
environment that has what you just added.

```console
$ just install         # or `mainplate install --port 8100`
$ just logs            # journalctl --user -u mainplate -f
$ just uninstall       # keeps the settings and the sessions
```

The install prints where its files are. `config.toml` is the one to edit, and
`environment` beside it carries any `MAINPLATE_*` process setting. Both are created `0600` on the
first install and neither is ever overwritten.

With no usable profile, or with one no endpoint will answer a model list for, the service fails at
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
[SQLite store](https://without.help/without-durability-sqlite/). The workflow's body is the
whole of what a session is:

```python
async def converse(run: Run) -> Never:
    agent = agent_for(endpoints, chosen, instructions)  # the pair this session recorded at creation
    at = reached(run.recorded)
    while True:
        prompt = await run.awaiting(prompt_key(at.turn), parse_prompt)
        with stepping(run, turn_prefix(at.turn)):
            answered = await agent.run(prompt, message_history=list(at.history))
        said = await run.step(messages_key(at.turn), recording(answered), parse_messages)
        at = Reached(turn=at.turn + 1, history=(*at.history, *said))
```

`run.awaiting` suspends the pass until something outside it records a value under that key, which
is what the console does when you send a message. Nothing polls, nothing is held open, and the
wait outlives the process that was waiting: the workflow is a row, and whichever worker picks it
up next runs the body again from the top and reaches further than the last one did.

The two things a pass writes are what make the second run cheap and the first one safe:

- **`run.step(messages_key(turn), ...)`** records the messages a turn produced, so resuming reads
  the history back instead of re-driving the agent over every past turn.
- **`stepping(run, ...)`** puts the agent's *model requests* through the checkpoint, one recorded
  step each, so a pass that reaches the provider and then dies does not pay for that answer twice.

That second one is a Pydantic AI **capability**, `StepwiseDurability`, in the same shape as the
bundled Temporal, DBOS, and Prefect ones: attach it to an agent and, inside a session, every model
request becomes a recorded step. Outside one it does nothing at all, so the same agent is an
ordinary agent in a script or a test.

It is built on `AbstractCapability` and `WrapperModel`, the surface Pydantic AI documents for
third-party integrations, rather than on the internals the bundled three share. Most of what that
base class carries is about crossing a *serialization* boundary, and there is no such boundary
here: the workflow body runs in this process, only a step's result is ever encoded, so the model
instance is simply in scope.

## The console

Server-rendered HTML with [htmx](https://four.htmx.org/), built from
[`without-html`](https://without.help/without-html/) node trees. The stylesheet, the script, and
htmx are all served from the process rather than a CDN, so a console on a machine with no route
out still renders.

One live region, and it is the transcript: while a turn is unanswered it asks for itself once a
second, and the answer comes back carrying no trigger, which is how the polling stops. A console
with nothing in flight makes no requests. The answer is *morphed* into the page rather than
replacing it, so what a reader has done to the conversation, an unfolded tool call, a search, the
place they had scrolled to, is not thrown away once a second by an answer arriving.

A turn is drawn as panels: a coloured edge per run of one kind, with the person's message, the
model's reasoning, its calls, and its answer each in their own. The palette runs on one axis, cool
for what reached the model and warm for what it produced, so a reader scrolling can tell the sides
apart before reading a word. Messages are rendered as Markdown and sanitised before they reach the
page.

Beside the conversation is a rail: find-and-step search, a key that filters by kind and doubles as
the colour legend, a dock that jumps between the two sides and folds every call at once, a
follow-the-end toggle, and a light/dark/system theme. All of it is an enhancement. With JavaScript
off the console still renders, still posts messages, and every tool call is still a fold that
opens; what goes is the rail.

## What it does not do yet

Named plainly, because they are the next things rather than omissions nobody noticed:

- **No tools.** The agent is a model and some instructions. Tool calls become another kind of
  recorded step, which is the shape `Stepping.key` already numbers. The console reads and draws
  them already, so a toolset is the change; the panel it appears in is not.
- **Two wires, not every wire.** A profile's `provider` takes `anthropic` or `openai`, which
  between them cover most gateways. A third is one `Endpoint` class saying how to name a model over
  that wire and how to ask it what it serves, plus an extra on `pydantic-ai-slim`.
- **A session cannot be moved to another profile.** Removing a profile that sessions use leaves
  them readable and stuck; the page names the profile so putting it back is obvious. Forking one
  onto a profile that still exists is the way out. A model dropping out of the picker is *not* that
  case and does not stop a session, since an endpoint routes more ids than it advertises.
- **Snapshots are kept but not yet restored.** A session that picked a repository works in a
  worktree of its own, every turn records the tree it started on, and a fork is checked out at the
  tree the forked turn saw. What is missing is a rewind: putting an *existing* session's files back
  to an earlier turn.
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

## Why the name

The mainplate is the base plate of a watch movement: the flat piece everything else is mounted to
and located by. It does nothing on its own.
