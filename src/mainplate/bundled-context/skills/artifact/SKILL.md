---
description: Build or revise an artifact, a self-contained HTML page the person opens in the console, sandboxed with no network
---
# Artifacts

An artifact is an HTML document the console keeps rather than the session: it
outlives this session, any session can keep a new version of it, and the
person opens it at an address of its own. You build it as a file with the file
tools, and move it between a file and the store with `file_to_artifact` and
`artifact_to_file`. There is no tool that takes HTML as an argument.

`generated/limits.md` beside this file is the exact policy the page is served
under, the largest file the store keeps, and how a document must open. It is
taken from the console's source, so where it and this page disagree, it wins.

## Keeping one

1. Write the page as one file. Where the session has a `scratch` root, put it
   there unless it belongs in the repository, so it stays out of the checkout's
   diff.
2. Check it against the sandbox (below) before keeping it.
3. `file_to_artifact` with the path, its `root`, and a short `title` saying what
   the page is. What comes back is the artifact's id and version: say both in
   your reply, since that is what anybody revising it needs.

## Revising one

`artifact_to_file` writes a version out to a path that must not exist yet. Edit
that file, then `file_to_artifact` with `artifact` set to the id and
`expected_version` set to the version you wrote out. If somebody kept a newer
version in between, the call is refused and nothing changes: write the current
version out, read what changed, and carry your edit onto it. Never retry with
the old version number.

`list_artifacts` finds an artifact by title, or lists one artifact's versions.

## What the sandbox means for the page

The page is served as an opaque origin with nothing allowed out. In practice:

- **Everything inline.** Scripts in `<script>`, styles in `<style>`, images as
  inline SVG or `data:` URIs, fonts as `data:` URIs or, better, a system font
  stack. A `<script src>`, a stylesheet link or a web font from a CDN does not
  load, and nothing says so.
- **No requests at all.** `fetch`, `XMLHttpRequest`, `WebSocket` and
  `EventSource` fail. Put the data in the page: a
  `<script type="application/json" id="data">` block read with `JSON.parse`
  keeps data apart from code and is the one block to replace when the data
  changes.
- **No `eval` and no `new Function`.** A library that compiles templates or
  expressions at run time breaks; prefer one that does not, or none.
- **No storage.** `localStorage`, `sessionStorage`, `indexedDB` and cookies throw
  in an opaque origin. Keep state in memory, and wrap any access a library makes
  in `try`/`catch` if it will not run without one.
- **No form submission.** Handle `submit` in script and call `preventDefault()`.
- **A library is pasted in.** Inline its minified build in a `<script>` block,
  keep its licence comment, and count it against the size limit.

The policy cannot stop a page navigating itself, so whatever is written into a
page can leave with it. **Never put a credential, a token, or anything read out
of the environment into an artifact.**

Before keeping, search the file for anything that reaches out:

```sh
grep -nE '(src|href)=["'"'"']?(https?:)?//|@import|url\((["'"'"'])?https?:|fetch\(|XMLHttpRequest|WebSocket|EventSource|localStorage|sessionStorage|indexedDB|new Function|eval\(' page.html
```

A hit is not always wrong (a plain `<a href>` the person clicks is fine), but
every one is worth a look.

## Making it good to look at

The person sees it in a frame that takes the whole window, on a laptop or a
phone, in a light or a dark theme.

- Support both themes with `prefers-color-scheme`, defining colours once as
  custom properties on `:root` and overriding them in the dark query.
- Lay it out for about 400px wide as well as wide screens: a readable column,
  rows that wrap, a wide table or diagram in its own `overflow-x: auto` box so
  the page never scrolls sideways.
- Give it a `<title>`; it is what a saved copy is called.
- Lead with what the page is for. A report starts with its conclusion, a tool
  with its control.
- Prefer inline SVG for a chart or a diagram over a charting library: smaller,
  needs no script, and stays sharp.
