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

```console
$ export ANTHROPIC_API_KEY=...
$ uvx mainplate serve          # once there is a release; from a checkout, `just serve`
```

Then open <http://127.0.0.1:8100>. Everything is stored in `mainplate.db` in the working
directory, so pointing the console at a different project is `--database`, and reading a session
back is opening the same file again.

`mainplate serve --help` lists the options; each one is a field of `Settings`, which also reads
them from `MAINPLATE_`-prefixed environment variables. The provider credential is not among them:
Pydantic AI reads it from the environment itself, so mainplate never holds it.

To see the console without a provider or any spend, `just demo` runs it against Pydantic AI's own
canned model.

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
[`without-html`](https://without.help/without-html/) node trees. Both the stylesheet and htmx
are served from the process rather than a CDN, so a console on a machine with no route out still
renders.

One live region, and it is the transcript: while a turn is unanswered it replaces itself every
second, and the answer comes back carrying no trigger, which is how the polling stops. A console
with nothing in flight makes no requests.

## What it does not do yet

Named plainly, because they are the next things rather than omissions nobody noticed:

- **No tools.** The agent is a model and some instructions. Tool calls become another kind of
  recorded step, which is the shape `Stepping.key` already numbers.
- **No streaming.** A streamed model request inside a session raises rather than running
  unrecorded, so the refusal is loud rather than a silently unrecorded call. Closing it means
  recording the stream's events alongside its response.
- **No Markdown.** A reply renders as escaped text with its newlines kept.
- **One machine.** SQLite means every process sharing this store shares a filesystem. That is the
  deployment this is for rather than a defect; a second machine means another store.
- **Nothing deletes a session.** They accumulate, and the only way to remove one is the file.

## Why the name

The mainplate is the base plate of a watch movement: the flat piece everything else is mounted to
and located by. It does nothing on its own.
