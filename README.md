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

The **provider** of a model (`anthropic`, `fireworks`, `xai`) is a third word and a different thing
again. It is discovered rather than configured, and it is not a level of the hierarchy: the same
provider turns up under more than one endpoint, since every Fireworks model on exe.dev's gateway is
listed by both of its formats under one id. So the shape is `endpoint -> model`, and the provider is
the heading the model cards are grouped under.

A session records what it is answered on at the moment it is created: its workspace, whether its
commands may reach the network, the endpoint, the model, and a thinking level. All of it is fixed for
its life. The workspace is one question rather than two: a repository this console can reach, or no
files, or this whole machine, and picking one settles both what the session works in and what its
tools may touch. The endpoint is what carries the API format, which is why it is recorded rather than
looked up later: the same model id genuinely does sit behind two formats, and the two serialize a
conversation differently.

Once you have picked a repository, you can say **where in it to start** and **what branch to start
there**. The two fields appear with the repository and are not there before it, because neither is a
question a session on no files has. Both are optional. Left blank, the worktree is checked out at the
repository's default branch as it stands now, on a branch named after the session
(`mainplate/349e2f1e`), so a `git commit` from the box under the conversation has somewhere to live
and `git push origin HEAD` does the obvious thing. Name one yourself and that wins.

They are two questions rather than one because **starting at `main` cannot put the worktree on
`main`**: git refuses a branch another worktree already holds, so the second session you started
there would fail to get files at all. One says where to begin, the other says what to begin.

The starting point is a **search over the branches the repository actually has**, read from the
repository itself rather than from this console's copy of it, so it works on the very first session
you start on one. Typing narrows them under the box, matching anywhere in a name rather than at the
front, and the arrow keys step what is left. It is the one control in the picker that is not cards,
because it is the one question with no closed set of answers: a tag, a hash or `main~3` is still
typed, and with JavaScript off the browser completes from the same names.

**Starting a session is also when this console's copy of a repository catches up.** It clones once
and nothing else ever refreshes that, so planting a session's worktree fetches first: a new session
begins at the repository as it is now, whether you named a starting point or left it alone. A fork is
the exception, because it begins at the files its forked turn actually saw, which is what makes it
the same question.

You pick all of it on the new-session page, ordered widest first. The endpoints are cards naming the
API format each speaks and the URL each points at, and the models are cards carrying what they cost,
how much they read, and what they can do, grouped by the vendor each comes from. Every one of those
questions is the same component: a group of cards **folded down to the one you picked**, with the
count of what else is on offer beside it and a box that narrows the group as you type. A gateway
serves seventy models, and a wall of seventy cards is not a page you can see the rest of your choices
on; shut, the whole of what a session is decided by is five lines. Opening a group is a checkbox and
the folding is a CSS `:has()` rule, so it works with JavaScript off and a shut group can never name
something other than what is actually checked. After that the session says what it is on rather than
offering a control that could not change it. A conversation that switched model halfway would replay
its recorded answers from one and continue on another, so what the transcript shows and what the next
turn reasons from would have different authors.

You can name a session there too, in the field above the box. Left empty it is named after its first
message, which is what every session was named after before the field existed.

**Forking is how you change your mind**, and it keeps the original readable. Every turn opens with a
rule carrying a `fork` link: following it makes a new session that inherits the turns before that
one, on whatever endpoint, model and thinking level you pick, and asks that turn's own question
again. The message comes across editable, so a fork is equally a way to rephrase. The sidebar draws
the result as a tree, each fork nested under what it came from and labelled with the turn it left at.

The repository is the one part a fork will not change. It inherits its parent's, because re-asking
a turn against different files is a different question wearing the same words. A session working in
*no* repository is the exception, and forking one is how you pick a repository up: think something
through first, then fork it into the code.

A fork carries neither the starting point nor the branch its parent was given. It is checked out at
the files the forked turn actually saw, which is what makes it the same question, so a starting point
beside that would be a second answer to where its files come from; and a branch its parent's worktree
still holds is one git will not check out twice.

Each row in the sidebar names the repository its session works in, which is what tells two
conversations apart once you are working in more than one. It reads `owner/repo` while a forge still
reaches it, and the recorded id once none does, so a detached integration leaves the row saying
where the session is rather than saying nothing.

A new session starts on whichever model the default endpoint listed first, which for most gateways is
their newest. Set `default_model` at the top level to name one instead; a name the endpoint has
since dropped falls back to the first rather than stopping the console. `default_thinking` names the
level, and defaults to saying nothing about thinking at all.

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

It is read before the console takes traffic and re-read on a timer, and unlike model discovery it
can never stop the console starting: a database that will not load costs a card its numbers and
nothing else. A model the database has no record of says so on its card. A console with no reference
configured says nothing, because nothing was looked up.

The list is read once before the console takes traffic and refreshed on a timer after that
(`MAINPLATE_REFRESH`, fifteen minutes by default), by a task that answers no requests. So rendering
a page never causes a request to a gateway, and a model that appears at the provider reaches the
picker without anybody restarting anything. A refresh that fails keeps the models discovered
earlier and logs why; a *first* read that fails is a startup failure naming the endpoint, because a
console with an empty picker can answer nothing.

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
[SQLite store](https://without.help/without-durability-sqlite/). The workflow's body is the
whole of what a session is:

```python
async def converse(run: Run) -> Progressed:
    at = reached(run.recorded)
    while True:
        asked = await opening_turn(run, at.turn)  # takes the next message off the session's inbox
        # The endpoint and model this session recorded at creation, and what the repository's own
        # `AGENTS.md` says, composed once per stretch of context and recorded as a step: what sits
        # in front of the cached prefix must not move under a conversation that is still going.
        agent = agent_for(endpoints, chosen, instructions)
        with stepping(run, turn_prefix(at.turn), allowance=spending, draining=draining_inbox(run, at.turn)):
            try:
                answered = await agent.run(asked.said, message_history=list(at.history))
            except AllowanceSpent:
                return Progressed()  # one live model request per pass; the next one carries on
        said = await run.step(messages_key(at.turn), recording(answered), parse_messages)
        at = Reached(turn=at.turn + 1, history=(*at.history, *said))
```

A session has an **inbox**, and everything you do to it from the page is an append: a message, or a
command to run in its worktree. `run.receive` suspends the pass until there is something in it, and
records which entry the turn took. Nothing polls the store, no pass is held open waiting, and the
wait outlives the process that was waiting: the workflow is a row, and whichever worker picks it up
next runs the body again from the top and reaches further than the last one did.

**Nothing decides in advance which turn a message lands in**, which is what a queue removes the need
for. Type while a reply is coming and the pass folds your message into the request it is about to
make; type a moment later and it opens the next turn. Neither the page nor the handler has to guess,
because the pass is the only thing reading at the instant the answer is true.

A pass is **one live model request and the tool batch behind it**, rather than a whole turn, so a
turn of forty round trips is forty passes. What that buys is a lease that bounds one round trip
instead of a bet on how long the longest conversation might run: a pass that spends its allowance
hands the turn back, the session is made ready again, and the next pass replays what is recorded
and reaches one request further. Nothing is paid for twice, because a replayed request is read out
of the checkpoint.

The two things a pass writes are what make the second run cheap and the first one safe:

- **`run.step(messages_key(turn), ...)`** records the messages a turn produced, so resuming reads
  the history back instead of re-driving the agent over every past turn.
- **`stepping(run, ...)`** puts the agent's *model requests and tool calls* through the checkpoint,
  one recorded step each, so a pass that reaches the provider and then dies does not pay for that
  answer twice, and a tool that already read a file or wrote one is not run again against a
  directory that has moved since.

That second one is a Pydantic AI **capability**, `StepwiseDurability`, in the same shape as the
bundled Temporal, DBOS, and Prefect ones: attach it to an agent and, inside a session, every model
request and every tool call becomes a recorded step. Outside one it does nothing at all, so the
same agent is an ordinary agent in a script or a test.

It is built on `AbstractCapability` and `WrapperModel`, the surface Pydantic AI documents for
third-party integrations, rather than on the internals the bundled three share. Most of what that
base class carries is about crossing a *serialization* boundary, and there is no such boundary
here: the workflow body runs in this process, only a step's result is ever encoded, so the model
instance is simply in scope.

## How the agent edits files

What a session's tools reach is one of the two things it picks when it is created. A session
working in a repository gets `list`, `read`, `edit` and `create` over its own git worktree and a
scratch directory beside it, refusing any path outside the two. One working on the whole machine
gets the same four with no such boundary. One reaching nothing gets no tools at all, which is what
this console was before there were repositories: a place to talk.

Anywhere there are tools there is also `bash`, wherever `bubblewrap` is installed to confine it.
Every command runs in a mount namespace of its own holding exactly what that session reaches and a
read-only system, so there is no home directory and no configuration of the console in it, and the
network is off unless the session asked for it. Inside a worktree the repository's git objects go in
read-only: `status`, `diff`, `log` and `blame` all answer, while `commit` and `stash` fail. That is
deliberate rather than incidental, because the conversation is how work is recorded here and
committing is yours to do. **Run** in the composer is where you do it: the same command from there
runs outside all of this, as you, in the same worktree.

`list` takes a directory and a depth, and a directory at that depth is summarised by a count rather
than opened, so the depth bounds the answer instead of hinting at it:

```text
., 8 files within 2 levels

.gitignore
README.md
pyproject.toml
src/
  demo/ (4 files)
tests/
  test_app.py
```

It asks git what is there rather than walking the directory, so a `.gitignore` is obeyed and an
installed environment or a build directory never reaches the model, while a file the agent itself
just wrote does. The tree is assembled here: git records files and not directories, so what it
answers with is a flat list of paths and an empty directory does not exist as far as this is
concerned.

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

The `│` is what says where the tool stops talking and the file starts, and it is worth the token per
line it costs over a space: with a space, `cxec def greet(name):` gives a model nothing to tell the
name from the line, and one that guesses wrong writes the anchor back into the file as content.

A line number is the one address that cannot fail: an edit above shifts everything below it and
`47` still resolves, so a stale line number silently edits the wrong place. An anchor either
resolves to exactly one line or does not resolve at all, so the same mistake is a refusal that says
to read the file again. It also means the model never retypes the text it is replacing, which is
the expensive half of a search-and-replace edit.

Nothing is stored between calls. Anchors are recomputed on every read, and where two lines would
share one, each takes in the line above it until they differ. Blank lines get no anchor: they are
17% of the lines in a typical file and none of them is unique on its own content, so they were the
largest single source of both cost and instability. They keep the `----` marker and the bar anyway,
so the gutter is a column that never breaks and no line of a read is parsed by a different rule than
the one above it.

An `edit` takes a **list** of operations, resolved against one reading of the file and applied
together, so operations in one call cannot shift each other and a batch that contradicts itself is
refused entire rather than half-applied. Which lines a span covers is said by the field name rather
than by a flag:

```json
{"op": "splice", "from": "vhvn", "before": "kxpe", "text": ""}
```

`from` and `to` are inside the span; `after` and `before` are outside it. One of them alone inserts
at that point. That is also how a span reaches blank lines: deleting a function and the blank lines
after it names the *next* code line with `before`, so it neither names a blank nor retypes the line
it stops short of. A `substitute` operation replaces text inside one anchored line, for when
retyping a whole paragraph to change a word is the wasteful part.

Every reply shows the changed regions with their new anchors, and names any anchor elsewhere in the
file that changed as a result, so a run of edits needs no re-read in between.

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
in the checkpoint, recorded step by step so that a resumed pass does not pay for them twice, so the
page reads those rather than waiting for the turn to write its messages: reasoning appears, then a
call with its arguments, then its result, then the next request. A call still out is drawn working on
its own panel, and while one is, nothing else claims a reply is being written; a message you steered
into the turn is drawn the instant you send it rather than when the turn ends. Nothing is stored to
make this work and nothing is streamed from the provider; it is the same checkpoint, read sooner.

A turn is drawn as panels: a coloured edge per run of one kind within one request, with the person's
message, the model's reasoning, its calls, and its answer each in their own. The palette runs on one axis, cool
for what reached the model and warm for what it produced, so a reader scrolling can tell the sides
apart before reading a word. Messages are rendered as Markdown and sanitised before they reach the
page.

**Every panel folds, from its own row**, with the mark just right of the title. What you want put
away is yours to decide; the console only says where each kind starts, which is a message, a reply, a
stretch of reasoning and a batch of calls open, and reference shut. Shut, a panel's row carries the
front of what is in it, clipped to whatever width the panel has - and for a batch of calls or
commands, the names of what ran. So the dock's fold-everything button turns a finished conversation
into its own outline, one row per panel, and a third button beside it puts every fold back where the
console had it. A tool call and a command keep a fold of their own inside the panel, because what
their summary says is the outcome and the status rather than the first line of the body, and a panel
holds a whole batch of either.

Two more panels say what the model was *told* rather than what anyone said. The **system prompt** is
what every request in a stretch of context carried, drawn under the rule that opens that stretch, and
**guidance** is a repository's own `AGENTS.md` for a directory, handed over at the moment a tool
reached into it. They are two kinds rather than one because they sit in different places in the
request - the first is re-sent whole on every request in front of the cached prefix, the second is
appended once into the history - so the key can quiet either without the other, and so that a reader
can see which is which. How much authority the second one carries is the provider's answer rather
than this console's, and it varies by model, which is another reason not to draw them as one thing.
Both are drawn shut, with the character count beside the opening line on the row, because what is in
them is paid for on every request from there on.

A **rule** stands at every round trip to the model, carrying what is true of that request rather than
of any panel in it: the worktree it was made against, how long it took, what it spent in tokens and
money, and an `r1.0` fold showing the JSON the checkpoint actually holds for it. Since the checkpoint
*is* the conversation, that is the state itself rather than a debug view of it, and it is fetched
only when you open it so the transcript never carries it, and it opens in place, below the rule, so
the record and the reply it came from can be read together. A request is recorded the moment the
provider answers, so a record can be opened while the turn is still running.

The figures are symbols, because a rule is one line that must not wrap: `↑` and `↓` are the tokens
sent and returned, `▣` is how much of the first came out of the provider's cache, and `Δ` against `Σ`
is what this exchange cost against what the conversation has cost so far. The words are in the
titles. The sent figure is the *context* the request carried rather than a sum, since every request
of a turn carries the whole conversation again, so beside it is what fraction of the model's context
window that is - and the rule's own line is a gauge of the same fraction, filled from the left and
shading toward red as it fills, so scrolling down a long conversation shows the line lengthen and
warm. The window is what the reference database says about the model; with none configured the counts
are drawn and the fraction is not.

The rule that opens a turn carries the turn's own facts besides: which turn it is, the `fork` link,
and what the whole turn spent. That costs no new idea, because every rule already stood at a request
boundary - a turn opens with its first request. The spend fills in as the turn runs, because each
response is priced and timed as it is recorded rather than when the page is drawn: what a turn came
to is settled the moment it is answered, where pricing it again later from a database that has since
moved would change what an old session appears to have cost. It is an estimate from published rates
and not a bill, since no gateway reports what it actually charged; a model nobody publishes a price
for shows its token counts and no money. The time is what the turn spent waiting on the provider,
summed over its round trips; the calls it made in between carry their own, beside the tool's name on
the panel that drew it. The session's own total sits under the message box.

A panel that arrives, or whose blocks say something different, is **marked for a beat** in its own
kind's hue. A turn fills in over several renders, and a reader watching one needs to be told which
part moved rather than left to spot it. It is worked out from what a panel says, so unfolding a
call or laying a search mark over one is not mistaken for news, and a conversation just opened does
not flash itself top to bottom.

The page opens **pinned to the end** and stays there as answers arrive. Scrolling away releases it,
scrolling back to the bottom re-enters it, and so does sending a message: whatever you had scrolled
up to check, what you want to see now is the answer to what you just sent.

Beside the conversation is a rail: find-and-step search, a key that filters by kind and doubles as
the colour legend, a dock that steps where the model's history starts again, whole turns, every
panel, or only what the model said, and folds everything at once or puts every fold back where the
console had it, a shelf for text you have written
and not sent, a follow-the-end toggle, and a light/dark/system theme. All of it is an enhancement. With JavaScript
off the console still renders, still posts messages, and every panel and every tool call is still a
fold that opens; what goes is the rail and the keyboard send.

**Send** puts what is in the box into the conversation now. If a reply is already coming, that means
**steering**: the message is put to the model in the turn it is answering, appended to the next
request it makes, so it travels up with whatever tool results are going the same way and shapes that
answer rather than the one after it. It shows in the transcript as a `steer` panel the instant you
send it, and sits below the results it travelled with and above the answer it shaped.

You are not asked which of those it is, because you could not answer: the page you typed on was drawn
from a checkpoint that has moved since. Neither is it decided when the message is written, because
that reading can go stale between the read and the write. It goes in the session's queue, and the
reply that is running takes it if it is still running when it looks.

Sending as a turn finishes cannot lose the message either, and nothing has to be raced for that to
hold: a message nobody took is still in the queue, and the next turn opens on it.

The caret beside Send opens everything else you can do with what you typed. **Next** is the one thing
the record cannot decide for you: it queues the message behind the reply that is coming instead of
putting it to the model now.

**Forget** asks the message with the model's context cleared, which is what to reach for when a
conversation has wandered and the backlog is costing you more than it is worth. What is cleared is
the context and nothing else: everything said so far stays on the page, keeps counting toward what
the session has cost, and still comes across if you fork. The transcript draws a line where it
happened saying so, and the dock's leftmost column steps between those lines. To carry on the
conversation one of them closed, press `fork` on that line: the branch takes the whole backlog with
it and leaves the boundary behind.

**Aside** steps out into a side conversation you mean to come back from, and **Fork** starts one you
do not. Both carry the whole conversation and leave the original where it is; the only difference is
what you meant, which is recorded so the sidebar can draw a digression as a digression. From either,
**Parent** sends what is in the box into the conversation you left. That is a message rather than a
merge, which is what makes it honest: the turns you took on the side were asked against a different
history, and splicing them in would leave the original holding an exchange that never happened.

Each of those has a name you can type instead of reaching for the menu. `/` at the start of an empty
box opens the same list, narrowed as you type, and a space after the whole word takes it: `/fork `
puts the box in that answer's mode, with the button beside it saying `Fork` rather than `Send` and a
line above it saying what will happen. The space is what commits, so until you press it the word is
ordinary text with the menu open beside it, and Enter takes whichever row you have arrowed to if you
would rather not finish typing. Nothing has been sent, so what you write next is what goes there, and
Escape puts the box back. Nothing is ever inferred from what you typed - a message that opens with a
slash is a message, and one that names nothing is ordinary text - so what you are about to press
always says what it does.

Sending leaves the mode, except where the answer is one worth staying in: `Run` stays, because a
command is rarely the only one, and everything else puts the box back to `Send`.

**Keep** puts what is in the box on the shelf and clears it, so you can write the next thing. The
shelf itself is in the rail; pressing a kept note adds it back to the box rather than replacing what
is there, so several of them assemble into one message. That is also how a long aside comes home:
keep the conclusions as you go, then send them back together. The shelf is scoped to the conversation and
a fork inherits its parent's. It lives in your browser, so it does not follow you to another machine
yet.

**Run** is the one answer there that is not a message. It runs what is in the box in this session's
own worktree, as *you* rather than as the agent, and the model is never told. That last part is the
point: the agent's shell runs behind a mount namespace with the repository's git objects bound
read-only, so no tool can write a history no panel shows, and `git commit` and `git push` are exactly
the things that boundary is meant to keep for you. Nothing about the run enters the conversation the
model is given, so committing at the end of a session costs it no context and reaches no provider.
The run is still recorded, though, so it draws as a `command` panel with the command, how long it
took and what it exited with, it survives a reload, and a fork carries it. What it said is drawn
open, where a tool call's output is folded: you asked for this one, so reading it should cost no
clicks, and a command whose whole answer was its exit status says `said nothing` rather than showing
an empty pane. Being open makes putting one away the press you make most, so the frame around the
output folds it as well as the line at the top does: reading to the end of a long output does not mean
scrolling back up to shut it. That holds for everything here that folds, since a tool call's return
and a stretch of reasoning are the other two things that run to hundreds of lines. Pressing what is
in the box does nothing, since that is where you select from. The status is shown as the number rather than as "failed", because `git diff --quiet`
exits 1 to say there *are* changes.

`/run ` reaches it like any of the others, and `! ` into an empty box is its own shorter key: either
way the box becomes a command box, set in the terminal's own face, with `Run` beside it, and stays one
after each command runs. Escape puts it back. Inside one a slash is just a slash, since it is the
front of half the paths anybody types.

Shift-Enter sends; plain Enter breaks the line. That way round because a message here is prose that
often wants a second paragraph and a fenced block, and a box where the obvious key sends is a box
you cannot write one in.

**It reads on a phone.** The rail folds away behind a clasp once it cannot stand beside the
conversation without taking the width from it, and on a narrow screen the session list stops being a
column and becomes a strip of chips across the top: every session is still one swipe away, and the
conversation gets all but about a twentieth of the height. What a screen that narrow mostly buys is
one scroller at a time, so the new-session page's choosing becomes a single scroll with the message
box pinned under it, and the fork page scrolls as the one long thing it is.

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
  between them cover most gateways. A third is one `Endpoint` class saying how to name a model over
  that format and how to ask it what it serves, plus an extra on `pydantic-ai-slim`.
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

## Why the name

The mainplate is the base plate of a watch movement: the flat piece everything else is mounted to
and located by. It does nothing on its own.
