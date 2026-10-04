---
description: Write a mainplate plugin, a script the console runs at named events in a session, which can add tools, gate calls, inject text, and keep a card of settings
---
# Writing a plugin

A plugin is **one executable file**. For each event it asked to hear, the
console runs it once, writes one JSON payload to its stdin, and reads one JSON
answer from its stdout. It never acts on the conversation itself: it answers
with what it wants done, from a closed list of effects, and the console does
it.

For getting a repository ready (installing a toolchain, starting a dev server)
read the `setup` skill instead; that is a plugin that answers only `setup`.

Beside this file, generated from the console's source, and authoritative where
this page is less precise:

- `generated/limits.md`: the declaration file, name patterns, timeouts, and the
  table of which effects each event may ask for.
- `generated/receives.schema.json`: every payload, one per event, told apart
  by `event`.
- `generated/describes.schema.json`: the answer to `setup`.
- `generated/answers.schema.json`: the answer to every other event.
- `generated/environment.md`: the environment each kind of plugin runs with.
- `generated/handoff`: a complete plugin the console ships, in Python: a tool,
  a card, a composer answer, and three events. Copy its shape.

## Where a plugin comes from

- **A repository's** are declared in `.mainplate/mainplate.yaml` under
  `plugins:`, a name mapped to a path inside the repository. They run in the
  sandbox, start in the checkout root, have their own scratch as `$HOME`, and
  have a network only at `setup`. The person can switch each one off on the
  session's settings step, and nothing runs before they press the button there.
- **The operator's** are declared the same way under `plugins:` in their own
  `config.yaml`, and run unconfined with the console's whole environment. Write
  one of these only when the person asks for something for their own console
  rather than for a repository.

Either way the file is executed directly: give it a shebang and the executable
bit. A Python plugin should use the standard library only, since it runs on
whatever `python3` the machine has, with nothing installed.

## The conversation with the console

1. **`setup`**, once per session. Answer with what the plugin contributes, all
   optional:
   - `events`: which later events to be asked. Ask only for what you answer:
     each one asked is a process started, and `before_tool` is one per tool
     call.
   - `tools`: each a `name`, a `description` the model reads, and a JSON Schema
     `schema` with `"type": "object"`. A call to one arrives as the `tool`
     event, so list `tool` in `events` too, as `generated/handoff` does. A
     repository plugin's tool reaches the model under a prefixed name
     (`generated/limits.md` says which), so a description should not tell the
     model the bare name.
   - `card`: a `heading` and `rows`, each one `switch` or one `number`, drawn on
     the session's settings step and its rail. Their values come back under
     `settings` on every payload.
   - `answers`: composer leaders the person can type, arriving as `compose`.
   - `instructions`: text added to what the model is told, for the session's
     life.
   - `jobs`: commands to keep running; see the `setup` skill.
2. **Every later event** is answered with effects, and only those
   `generated/limits.md` allows for that event:
   - `return` a value to the model, or `retry` with a correction, at `tool`;
   - `refuse` a call at `before_tool`, with the reason the model reads instead
     of a result. Use it to redirect ("use `edit`, not `sed -i`"), not to
     report a malformed call;
   - `inject` text in the console's voice at `before_request`, or at
     `before_turn_end`, where any injection sends the model back to work
     instead of letting the turn end. `attempt` counts how often this turn was
     sent back; **bound it yourself**, since the console sets no limit;
   - `deliver` a message into the session's inbox, which opens a turn on it
     (with `label`, `title`, and a `tone` of `plain`, `quiet` or `strong`);
   - `end` the turn once this tool call is answered;
   - `set` values to remember.

An empty answer, or printing nothing, is always valid and means "nothing to
do". Printing an effect the event does not allow is refused, naming the
plugin.

## Remembering things

`set` writes names into the session's own store. A name the card declares is
a **setting**, comes back under `settings`, and the person can change it. Any
other name is **state**, comes back under `state`, and is the plugin's alone.
Write `null` to remove one. Both belong to one session: a fork starts with
none. Within one pass the plugin sees its own writes immediately.

Keep memory there rather than in files: it travels with the session's record,
and the console replays recorded answers when it resumes a pass rather than
asking the plugin again.

## Writing it

- **stdout is the answer and nothing else.** Send diagnostics to stderr, which
  is logged. In a shell script, `exec 3>&1 1>&2` at the top and the answer to
  `>&3` at the end; in Python, `json.dump(answer, sys.stdout)` once at the end
  and `print(..., file=sys.stderr)` for anything else.
- **Dispatch on `payload["event"]`** and answer `{}` for anything else, so a
  payload field or event added later never breaks it. `generated/handoff`'s
  `answer` is the shape.
- **Exit 0.** A non-zero exit, output that is not JSON, or running past the
  timeout fails the call: at `setup` that refuses the session's setup until
  the person retries, and at any later event it fails the pass. Raise loudly
  on what you do not understand rather than guessing, but treat an extra field
  as something to ignore.
- **Answer from the payload.** It carries the session, the settings, the
  state, and `checkout` and `scratch` paths; a repository plugin's environment
  has nothing else. Read the repository's files at `checkout` if you need them.
- **Never run git in the checkout from a plugin that runs unconfined.** The
  checkout's configuration is the session's to write, and git runs programs it
  names. A repository plugin is confined already.
- An edited plugin is picked up by a new session or a fork. One already
  running keeps what it was set up with.

## Trying it without a session

Feed it a payload by hand and check the answer against the schemas:

```sh
echo '{"event": "setup", "session": "s", "plugin": "repository:mine"}' | ./myplugin | python3 -m json.tool
echo '{"event": "tool", "session": "s", "plugin": "repository:mine", "tool": "greet", "args": {"name": "Ada"}}' | ./myplugin
```

The payloads in `generated/receives.schema.json` say which fields each event
carries. Then declare it, start a new session on the repository, and press the
button on its settings step.
