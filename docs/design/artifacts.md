# Artifacts

An **artifact** is an HTML document a session made, kept by the console rather than by the session:
it outlives the session that made it, any session can keep a new version of it, and the console
serves it at an address of its own, sandboxed. What the model does with one is move it between a
file and the store; what the person does with one is open it, in the console or as a saved file.

This page covers the store, the three tools, and how a version is served and drawn. What an artifact
may contain beyond "one self-contained document" is not decided here and is not checked.

## Two tables beside the checkpoint

`artifacts.py` keeps two tables in the console's database, beside the checkpoint's and the session
index: `artifacts`, a row per artifact holding its title and which version is current, and
`artifact_versions`, a row per version holding the bytes, when they were kept, and the call that kept
them. **Every version is written once**; the two things that move are the current version and the
title, and both move only inside `keep`, under a write lock.

That is state outside the checkpoint, and [the philosophy argues why it is not a second copy of what
was said](../philosophy.md#an-artifact-is-kept-beside-the-conversation). The short version: a
version is settled the moment it is kept, and the pointer and the title are facts nothing else
records. **What it costs is the thing the checkpoint gets for free**: a session's conversation is its
checkpoint and nothing else, and an artifact is the one thing a session makes that its checkpoint
does not hold. A fork of a session carries the calls that kept versions and not the versions, which
is right, since those versions still exist and the fork's links reach them; but a database with the
checkpoint and without these two tables is a console whose transcripts link to nothing.

Inside a checkpoint was the alternative, as a call's metadata carries an `edit`'s diff. It fails on
the three things an artifact is for: a value in one session's checkpoint cannot be updated from
another session, cannot be listed without walking every checkpoint, and belongs to a session that
may be archived while its artifact is still wanted.

## Keeping a version

**A file goes in and a file comes out.** `file_to_artifact` keeps a file the model built with the
file tools it already has, and `artifact_to_file` writes a version back out as a new file. There is
no tool that takes HTML as an argument, because it would be a second way to write a file with no
anchors, no diff and no lock, and every revision of a page would be the page retyped whole. So
revising an artifact is writing it out, editing the file, and keeping it again.

**A version is kept once per call.** Each version records the session, turn and call that kept it,
and those three are unique together, because they are the call's own key in the checkpoint. A pass
that falls over after the version is written and before the call's return is recorded runs the call
again, and `keep` hands back the version it already kept rather than a second one; the tool asks the
store before it reads the file, so what happens to the file in between does not matter. The same call
arriving with different bytes is refused, since that is a caller reusing an identity rather than a
replay.

**An update names the version it read.** `artifact` and `expected_version` go together, as one
`Updating` value once parsed, and a version that is no longer current is refused and changes
nothing. Two sessions revising one artifact therefore cannot overwrite each other; the second is
told the artifact has moved and to look at it again. The refusal says which version is current, and
that is what the model needs to act on it.

What a document must be is parsed once, where the bytes arrive, into `Html`: UTF-8, at most 2 MiB,
opening with `<!doctype html>` or `<html>`. The bytes are kept exactly as they arrived, byte order
mark and line endings included, because the promise below is that the preview and the download are
the same file.

## Which tools a session gets

`list_artifacts` everywhere, and the pair wherever there are files to move a document between, read
off the same `Files` value the file tools hold. That puts the artifact tools outside [the table of
which tools a session gets](tools.md#which-tools-a-session-gets), the way a plugin's tools are: an
artifact is the console's rather than any session's, so asking what exists is a question with no path
in it. **The cost is three more tool definitions in every session's prefix**, whether or not the
session ever keeps a page, and a first request after upgrading that no cached prefix covers.

## Both ends of a call

A version knows the call that kept it, and the call knows the version it kept: `file_to_artifact`
records `{artifact, version}` under its `metadata`, which the model is never sent, and the page reads
that to link the call to the version. The words the model is sent say the same two numbers for the
model's sake, and nothing parses them.

So the transcript links each call to what it kept, pinned to that version, and an artifact's page
links back to the rule of the turn whose call kept the version on show.

## Serving a version

**The sandbox is on the response, not only on the frame.** A version's bytes are served with a
`Content-Security-Policy` of `sandbox allow-scripts` and nothing else allowed out: no `connect-src`,
no remote images, fonts or styles, no form posts. `sandbox` without `allow-same-origin` makes the
document an opaque origin wherever it is opened, so a page cannot read the console's cookies or
storage or reach its pages, in the preview frame or opened directly in a tab. The frame carries the
same `sandbox` attribute, which is a second statement of one policy rather than the one relied on.

That policy is what makes "self-contained" a rule rather than advice: a page that loads a script from
a CDN is a page whose script does not load. Allowing a CDN would mean a network request on behalf of
a document a model wrote, from a browser holding this console's session, which is a decision to make
deliberately if ever, not a default.

**What the policy cannot stop is the document navigating itself.** A script setting `location`, a
`<meta http-equiv=refresh>`, or a link somebody presses takes the frame, or the tab it was opened
in, to any address at all, and whatever the page holds can ride along in that address. The policy
has no directive for this: `navigate-to` was proposed and never shipped, and the sandbox's own
flags govern only the page *around* the frame and new windows, both of which stay shut. So a page a
model was talked into writing can send what it holds somewhere, one navigation at a time. It cannot
read the console to find more than it was written with, which is what bounds it: what leaves is
what the model already put in the page. The alternative is serving without `allow-scripts`, which
stops the refresh and every navigation but a press, and makes every artifact a static document; an
artifact is meant to be a page that does something, so that is not the default.

**The preview and the download are one function with one header different**, so what is saved is
byte for byte what was shown. What is *not* the same is what runs it: a saved file opened from disk
runs under whatever the browser gives a local file, not under this policy, and the page says so
beside the link. Both addresses require a version, so the bytes at an address never change and a
preview cannot move under the page framing it; an unpinned request is refused.

The document is never put into the console's own markup, not even escaped into `srcdoc`: a `srcdoc`
frame inherits the page's policy rather than the one written for the document.

## The pages

The **catalogue** lists every artifact at its current version, newest first, reached from the
dashboard's section of the most recent few. Its rows are the dashboard card's, so a list of
artifacts reads like a list of sessions.

An **artifact's page** is one version, as a bar over the preview, and the preview takes the rest of
the window. The bar reads as a path at the left, the console's mark, the catalogue, and the title,
then which version of how many; at the right, when it was kept, the turn that kept it, and the
download. The version history is a disclosure hanging from "version 2 of 3", in the same rows, laid
over the preview when open rather than under it, because a list under the frame is what made the
page scroll and capped the frame short of the window.

**It is the one page drawn without the session list.** An artifact is not a conversation, and what
somebody came to it for is the document, as large as the window allows; the console's mark is still
there, and the dashboard is where every other place is reached from. With no list there is nothing
for the live connection to redraw, so the page holds none. The cost, stated: reaching another
session from here is two presses rather than one. An artifact's page might one day want a list of
its own, of artifacts rather than sessions, and that is a separate decision rather than something
this one owes.

Listings page by keyset rather than offset, so a version kept while somebody pages does not shift
the page under them: the catalogue by the order versions were kept in, a history by version. A page
holding a full `LISTED` draws a link to the next, which costs a link to an empty page when there were
exactly that many, and saves a second query on every page to rule it out. `list_artifacts` does the
same for the model: a full page ends by saying there may be more and what to pass as `before`, so
the page size is written nowhere but `LISTED`.

## What is not here

- **Nothing deletes an artifact or a version.** The seeder does, for its own fixtures, with a
  statement of its own, for the reason nothing deletes a session.
- **Nothing restricts who can open one** beyond whoever can reach the console, which is the same
  line a session's address draws: the id is long enough not to be guessed, and there are no accounts.
- **The gallery draws an artifact's page with an empty frame.** It is static files, and the bytes are
  served by a route; the frame shows the static server's 404. The live console, and `just demo` after
  `just seed`, show the document.
