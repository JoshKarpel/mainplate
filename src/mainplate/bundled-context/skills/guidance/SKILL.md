---
description: Write a repository's AGENTS.md guidance, skills or commands so a session is told the right thing at the right moment
---
# Guidance, skills and commands

Three ways a repository gives a session text, told apart by who decides when it
is read:

| Kind | Where | Read when |
|---|---|---|
| Guidance | `AGENTS.md` in a directory | The root's from the first request; a nested one after a file tool names a path under it |
| Skill | `.mainplate/skills/<name>/SKILL.md` | The model reads it when its description seems relevant, or the person types `/<name>` |
| Command | `.mainplate/commands/<name>/COMMAND.md` | Only when the person types `/<name>` |

Choose by what happens if it is not read. **Anything whose absence would change
the answer is guidance**, because a skill is read only when the model already
suspects it applies, which is exactly when it was not needed. A procedure for a
situation somebody names out loud ("cut a release", "add a migration") is a
skill. Something only the person should start is a command.

`generated/limits.md` beside this file holds the exact names, patterns and
limits, taken from the console's source. `generated/guidance.md` and
`generated/context-loading.md` are the console's own design notes on both,
for the reasoning behind any rule below.

## Guidance

### How it loads

- **The root `AGENTS.md` is in the instructions whole, from the first request,
  on every request.** Its length is paid on every request of every session, so
  it holds what is true everywhere in the repository and nothing else.
- **A nested `AGENTS.md` is listed by path, with its `description:`, and
  handed over whole on the request after a file tool names a path in its
  directory or below.** A path reached only through `bash` never triggers it,
  which is what the listing is for: the model sees that guidance exists and can
  read it.
- **One file per directory.** `AGENTS.md` wins; `CLAUDE.md` is read only where
  there is no `AGENTS.md`.
- **Only files git tracks count**: committed or staged, regular files, UTF-8.
  An untracked or ignored file, or a link, is not read.
- **`paths:` does nothing in a repository's guidance.** A file covers its own
  directory and everything below it; place it where it applies.
- **`@` imports are not expanded.** Keep the text in `AGENTS.md`. Where Claude
  Code also needs to find it, add a `CLAUDE.md` beside it holding only
  `@AGENTS.md`; this console reads the `AGENTS.md` and never sees the shim.
- A session settles the root file and the listing when it starts. An edit
  reaches a new session, or a fork, not the one already running.

### Frontmatter

A nested file opens with one line saying what a reader gets from it, which is
the row the listing shows:

```markdown
---
description: "What must hold while changing a migration: ordering, reversibility, and the lock it takes"
---
```

One line, bare or quoted; quote it when it contains `: `. A folded or nested
value reads as no description. The root file's description is never shown.

### What to write

Write for whoever is about to change the code, not whoever is reading it:

- the commands that build, test and check, and which to run before calling
  anything done;
- what must hold, and what a change that breaks it looks like;
- the obvious approach that was ruled out, and why, so nobody "fixes" the code
  back;
- where the reasoning lives, as a link, rather than the reasoning restated.

Leave out what one command or one file already says (a list of scripts, every
recipe, the contents of a config file): the copy drifts and nothing fails when
it does. Put a constraint in the deepest directory where it is still true.

## Skills and commands

- The name is the directory, and it is what follows the `/`. There is no
  `name:` key.
- `description:` in frontmatter is **required**: an entry without one stops
  discovery with an error rather than vanishing. For a skill it is the whole of
  what the model sees before deciding to read the body, so say when to use it,
  not only what it is.
- A skill's body can point at files beside it, which the model reads only when
  it needs them. Put the procedure in `SKILL.md` and examples, references and
  scripts beside it, so a short task does not pay for a long one.
- Reading a skill runs nothing. A script beside it runs only if the model runs
  it, under the session's ordinary sandbox and network.
- Repository text is untrusted wherever it comes from. Do not write a skill or
  command that asks a session to send something off the machine, or to weaken
  how it is confined.
- A name already taken by a leader the console has (such as `/run` or
  `/handoff`), or by a skill elsewhere, is offered only qualified, as
  `/repository:<name>`. Pick a name nothing else uses.
