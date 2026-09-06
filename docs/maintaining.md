# Maintaining mainplate

For somebody working on this repository rather than running the console. Putting a console on a
machine is [running it as a service](design/deployment.md); this is the toolchain around the
source.

## Dependencies

Built on [`without`](https://without.help), a workspace of small sans-IO libraries, and the sibling
checkout at `../without` is where its source is. **Read the installed packages in `.venv` rather
than guessing at an API from memory**; the same goes for `pydantic_ai`, which moves fast.

`uv` resolution has a 7-day cooldown (`exclude-newer`), with the `without-*` packages exempted in
`[tool.uv.exclude-newer-package]`. Adding a `without` package means adding it to that exemption list
too, or the whole graph gets held back to a release predating it. The cooldown is also why
`pydantic-ai-slim` is floored a release or two behind its latest.

`pydantic-ai-slim[anthropic,openai]` rather than `pydantic-ai`, which pulls every provider SDK, a
CLI, an MCP server, and an evals framework. Two extras and not more: between them the Anthropic and
OpenAI wires reach almost every gateway, and each further extra is a whole SDK. The `openai` one is
not free, since it brings `openai`, `tiktoken`, `requests`, `urllib3`, `regex`, and `certifi`, which
is the price of the OpenAI-compatible half of a gateway being reachable at all.

## What mypy does not walk

Two directories, and neither is skipped by `.gitignore`, which mypy does not read: `workspaces/`,
which is other people's repositories checked out by a local session and so is somebody else's code
checked against this project's settings, and `site/`, which is the built documentation and carries a
copy of `docs/hooks.py` that collides with it by module name.

How the suite itself is driven, and the rule for what has to be a real browser, is in
`tests/AGENTS.md`; which checks to run before saying anything is done is in `AGENTS.md`.

## The documentation site

MkDocs Material, built by `just docs-build` and served with live reload by `just docs`. The `docs`
dependency group is its own rather than part of `dev`, so running the tests or the console needs no
static site generator installed.

**`--strict`, with `validation.anchors` on.** A broken link, a page nothing in the nav points at, or
a `#anchor` naming a heading that has been renamed all fail the build. The anchors are the half
worth having deliberately: the design pages cite one another's headings often enough that an
unchecked fragment is how a cross-reference rots silently, landing at the top of the right page and
saying nothing about it.

`docs/hooks.py` copies `README.md`, `PHILOSOPHY.md` and `CHANGELOG.md` from the repository root into
the site tree, `README.md` as the home page. They stay at the root because that is where the people
and the harnesses that read them already look: `AGENTS.md` names `PHILOSOPHY.md` as the thing to
read first, and a release process edits the changelog at the root.

**The price is that a link in any of the three has to work from both places.** A relative path
resolves in only one of them, so those pages name the published site by URL.

`docs/stylesheets/extra.css` is the one override, and it exists for the tables: a table's first
column here is an identifier, and left to wrap it breaks mid-token so that `turn:{n}:opened` reads
as two things on two lines.

CI builds the site on every pull request (the `docs` job in `ci.yml`) and `publish-docs.yml` deploys
it to GitHub Pages on a push to `main`. Neither uses the shared toolchain action, because that
installs the Chromium the suite drives and the `bubblewrap` it confines commands with, and a site
build reads Markdown.

## Where the words go

The prose in this repository is split four ways, and the split is worth keeping:

- **`README.md`** is what the console does, for somebody deciding to run it. It is also the site's
  home page, so it describes the project and never itself.
- **`PHILOSOPHY.md`** is the one idea and the rules the design notes cite rather than restate. It is
  the standard new work is measured against, and not the authority on what the code currently does.
- **`docs/`** is the design notes: what each mechanism is, what it costs, and which alternatives
  were tried and are not worth trying again.
- **`AGENTS.md`**, at the root, is the map, and it carries the standard all four are written to
  since it is the one of them every harness loads without being asked. In a directory it is what a
  change *there* must not break, which is a different thing from a design note rather than a shorter
  one: the page argues for four lowercase letters, the file beside `anchors.py` says do not make it
  three. Write a new constraint beside the code and its reasoning on the page, never either in both.
