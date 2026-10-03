# Loading context

Guidance, skills and commands differ in when their text enters a conversation.
[What a session is told](../plugins/guidance.md) describes the guidance plugin;
[the composer](composer.md#leaders) describes the manual modes they share.

## The trigger is the distinction

These are three ways to give the model text, not three kinds of executable
plugin. The difference is who chooses *when* to load the body:

| Kind | Visible before loading | Body loaded by |
|---|---|---|
| Guidance | Unscoped text; index of scoped files | Plugin, after a matching file tool call |
| Skill | Name, description and path | Model's `read`, or person's `/` leader |
| Command | Name and description in composer | Person's `/` leader only |

**Guidance is triggered by scope**, not by the model deciding that a rule would
be useful. It belongs in the instructions when it can change that decision;
scoped guidance is handed over on the next request after a matching file tool
call. The index advertises it earlier, but a file reached only through `bash`
does not trigger the handover. These are costs of the [existing guidance
mechanism](../plugins/guidance.md#guidance-that-names-its-files), not a reason
to call a scoped rule a skill.

**A skill is offered for the model to choose.** Its short description and path
are in the standing index, while its body is not. The model reads `SKILL.md`
with `read` when it judges the skill relevant. `SKILL.md` may point at other
files beside it, which the model reads separately if it needs them. Disclosure
is therefore index, then body, then selected supporting files; a skill with
many examples does not spend context on examples the task never needs. The
cost is that a skill can go unread when the model does not recognise its
relevance. A rule that would change that judgement belongs in unscoped
guidance, not in a skill whose discovery depends on it.

**A command is a person's invocation.** Its body is not advertised in the
model's index and no model-facing tool resolves a command name. This is a
meaningful distinction even if its directory looks like a skill directory:
only the person can invoke it as a command. The model can still read a
repository file it knows the path to; command-only is a rule about discovery
and invocation, not a filesystem secrecy claim. The cost is that the person
has to know when to invoke it.

## One read tool, not a skill loader

`read` already asks for a file and records the answer as a tool result. A
`load_skill` tool would duplicate the question while introducing a second
path for reading the same bytes. Repository skills belong inside the checkout,
where the existing file tools can already reach them; they need no second
mount of that checkout. The model reads `SKILL.md` like any other file, and a
resumed pass replays the recorded return rather than consulting an edited
file for an answer it already gave.

Bundled and user skills do not live in the session's checkout. They are
addressable under read-only `bundled_skills` and `user_skills` roots in the
same vocabulary that `read` and `bash` share. A bwrap bind alone is
insufficient: the ordinary `read` tool runs in the parent, not in `bash`'s
namespace. File tools refuse edits and creates in these roots. This costs
two roots and mount policy for non-repository skills, rather than a new tool
or a second copy of repository files. A session without file tools does not
advertise skills it cannot read; manual invocation still delivers their
bodies. Each file-tool root admits only skill names discovered for the session.

Read-only is an interface rule as well as a mount flag. A skill may describe
running a supporting script, but reading it does not execute it. If the model
runs one, that is an ordinary `bash` call under the session's isolation and
network choice, not authority granted by the skill. In particular, a skill
cannot acquire a plugin's connected `setup` event merely by sharing its tier.
A repository skill's working files are not read-only: the session may edit
them like any other checkout file. The extra root is only for bundled and
user files that would otherwise be outside its reach.

## A slash leader is the manual path

The person can invoke a skill or a command with `/name`, where `name` comes
from its directory, rather than choosing a second verb such as `/skill name`.
Like [the composer's existing leaders](composer.md#leaders), a leader commits
visibly in the page before submission. The server does not strip `/name` off
an ordinary message: without the page's explicit mode, that text is just text.

A manual skill invocation reads `SKILL.md`; a manual command invocation reads
its command file. Each sends the selected body together with the person's
input, and records the text actually sent and its source in the checkpoint.
The transcript must show that expansion rather than only the `/name` that
selected it. A later edit to the file cannot change what an earlier turn
heard. Neither path executes a script. A skill can still be read by the model
without the person invoking it; a command is not advertised for model
invocation.

This use of command means a context recipe the person sends to the model.
The existing composer's [`Run`](composer.md#run) is a shell command, recorded
but never told to the model. Giving both a `/` leader does not turn the former
into a shell process or the latter into prompt text.

A slash word can mean only one thing in a session. Existing leaders such as
`/run` keep their meanings. Names are qualified by provenance when needed so
two directories called `review` do not silently shadow one another; an
unqualified `/review` is offered only when it has one meaning. The index, the
menu and the resolver must read the same settled names, rather than each
implementing a precedence rule. This costs longer leaders for collisions and
avoids a repository changing the meaning of a control the person already
knows.

## What is settled, and what can change

Use the plugin tiers as *provenance*, not as a plugin transport: bundled, user
and repository context can all be listed, without launching programs or
returning plugin effects. Bundled entries live under
`src/mainplate/bundled-context/`, user entries under `<config home>/mainplate/`,
and repository entries under `.mainplate/`. Each has `skills/<name>/SKILL.md`
and `commands/<name>/COMMAND.md`; frontmatter supplies a one-line
`description`. An entry that cannot be described or is too large to send
refuses discovery rather than silently disappearing from the menu. The name
is its directory, not a second field in frontmatter. Discovery fixes names,
descriptions, paths and available leaders for the session. A file appearing
mid-session cannot add a leader or change the index in cached instructions.
A fork rediscovers them.

Do not copy every skill body into that index. A model's `read` sees the file
when called, and the resulting tool return is recorded. A manual invocation
records its expanded message instead. An edit to an unread skill can therefore
reach a running session, while an edit after a read cannot retroactively
change that read. This gives the repository the same useful property as its
ordinary working files, at the cost that a session's *available names* are
settled but their unread bodies are not. The index is a map of where to look,
not a promise that a file has stayed unchanged since setup.

Repository files are untrusted text, whether they hold guidance, a skill or a
command. Reading a repository definition in the parent must not follow a link
out of the checkout, and discovery must not run git against the checkout in
the parent. The existing guidance plugin's [index and no-follow
reads](../plugins/guidance.md#guidance-elsewhere-in-the-repository) show why
both constraints matter. For user and bundled skills, the file tool refuses
names not in the session's index and the bwrap bind exposes the skill directory
read-only, not the operator's whole configuration directory. A command with
access to that bind can see unindexed files in the same skills directory; this
is a cost of binding a live directory rather than copying it per session.
These checks bound what the *parent* reads, not whether repository text is
trustworthy instructions.
