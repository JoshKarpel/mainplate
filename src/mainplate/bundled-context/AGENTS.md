---
description: What must hold of a bundled skill or command, which every session reads in somebody else's repository, and which of its files are generated.
---

# The bundled skills and commands

Every session the console answers is offered these, whatever repository it is on: `skills/` in its
index and `commands/` as the person's `/` leaders. How they are found and read is [loading
context](https://joshkarpel.github.io/mainplate/design/context-loading/). What follows is what must
hold while writing one.

## The reader cannot see this repository

A session reading a bundled skill is working on somebody else's code. It has the skill's own
directory and nothing of mainplate's: no source, no design notes, no `.mainplate/`. So a skill names
nothing outside its own directory (another skill's, under the same root, is fine), and whatever it
needs from the console is in that directory.

**A fact that can move with the source is never restated by hand.** A limit, a name pattern, a
policy, the protocol's shape, a script held up as an example: each is written into the skill's
`generated/` directory by `scripts/skills.py`, and the `SKILL.md` points at the file and says it
wins where the two disagree. What a `SKILL.md` states itself is what does not move: what a mechanism
is for, the procedure, the mistakes worth warning about.

- **Never edit a file under `generated/`.** The pre-commit hook rewrites it from the source it names
  in its header, and deletes any file there `generated()` does not list. To change one, change that
  source, or the table in `scripts/skills.py`.
- **A new fact a skill needs is a row in `generated()`**, read from the value the console itself
  uses, not a sentence in the `SKILL.md`.
- **Name every supporting file in backticks in the `SKILL.md`**, generated or not.
  `tests/test_skills.py` fails on a file beside a skill that nothing points at, since no model reads
  it, and on a `generated/` path the script does not write.

## A skill is read only when the model already suspects it applies

`description:` is everything the model sees before deciding to read the body, and it ends the
skill's row in the index exactly as written. Say when to use the skill, in one line. Guidance that
would change an answer the model does not know is at stake belongs in a tool's description or the
instructions, never in a skill, because nothing loads the skill at the moment it was needed; that is
why there is no general skill about the console itself.

Keep the procedure in `SKILL.md` and push the long tail into files beside it (examples, one file per
ecosystem, references), so a short task does not pay for a long one.

## A command is the person's, and only theirs

A `COMMAND.md` is never offered to the model, so it is the place for something only the person
should start. Its name is a leader in every session, so it must not be one the composer or a plugin
already answers to; those are qualified rather than refused, and a bundled command offered only as
`/bundled:name` is one nobody finds.
