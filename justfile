#!/usr/bin/env just --justfile

set ignore-comments

# Not the default port, so a console started for a quick look never takes down one already
# serving on this machine, and a `just serve` can run beside a `just screenshot`.
DEV_PORT := "8101"

# Where the gallery is served from while it is being shot. Its own port again, so a shot taken
# while a console is running does not need either of them stopped.
GALLERY_PORT := "8102"

GALLERY := "build/gallery"
SHOTS := "build/shots"

# Not `mainplate.db`, so seeding fixtures can never reach a database holding real conversations.
DEMO_DATABASE := "mainplate-demo.db"

[default]
[doc('List available recipes')]
list:
    just --list

alias l := list

# Two Chromiums, and for now that is what it costs: the suite drives Playwright's Python binding and
# `shots` still drives its Node one, which pin their own browser builds separately.
[doc('Prepare a fresh clone: dependencies, browsers, and pre-commit as a git hook')]
setup:
    uv sync
    uv run pre-commit install
    uv run playwright install chromium
    npm install
    npx playwright install chromium

# The stylesheet is a deliverable, and no string assertion checks one. These render every page from
# fixture checkpoints and drive a real Chromium over them, so a styling change can be *looked at*.
#
# No server, no database, no provider and no `config.toml`: a page is a pure function of
# already-answered questions, so the whole of what this needs is to answer them with fixtures. The
# assets are copied beside the pages, which is why a static server renders them identically to the
# real console.

[doc('Render every page to build/gallery, as files a browser can open')]
gallery:
    uv run python -m scripts.gallery {{ GALLERY }}

# Extra arguments are pages to shoot, each `file.html` or `file.html#anchor`, defaulting to all of
# them: `just shots 'session.html#panel-0-1'`.
[doc('Screenshot every page, wide and phone, into build/shots')]
shots *args: gallery
    #!/usr/bin/env bash
    set -euo pipefail
    uv run python -m http.server {{ GALLERY_PORT }} --bind 127.0.0.1 --directory {{ GALLERY }} &>/dev/null &
    trap 'kill %1 2>/dev/null || true' EXIT
    sleep 1
    node scripts/shoot.mjs http://127.0.0.1:{{ GALLERY_PORT }} {{ SHOTS }} {{ args }}

# Behaviour rather than appearance is a different question and gets a different check, and those are
# in the suite rather than here: what a still cannot show is that *two* panels are drawn as where the
# reader is, or that a form posts the controls sitting outside it, and `tests/test_browser.py` drives
# a real Chromium over this same gallery to ask. A check nobody runs is a check that catches nothing,
# so they run wherever `just test` does.
[doc('Run type checking and tests')]
test *args:
    uv run mypy
    uv run pytest {{ args }}

alias t := test

[doc('Format and lint')]
check:
    uv run pre-commit run --all-files
    uv run mypy

[doc('Serve the documentation site with live reload')]
docs *args:
    uv run --group docs mkdocs serve {{ args }}

alias d := docs

# `--strict` so a link to a page that does not exist, or a page nothing in the nav points at, fails
# the build rather than shipping. CI runs this same recipe.
[doc('Build the documentation site into ./site')]
docs-build *args:
    uv run --group docs mkdocs build --strict {{ args }}

# Both of these restart the console whenever anything under `src/mainplate` changes, which covers
# the stylesheet and the script as well as the Python. The assets are inventoried once at startup,
# deliberately, so a CSS edit is only visible to a *new* process: without a watcher, looking at a
# styling change means stopping and starting the server by hand every time.
#
# It restarts the server, and does not reload the browser. A page has to be refreshed to be seen
# again, which is one keystroke and is the whole of what this trades for needing no dev-only
# script injected into a page that ships.
#
# `exec` so Ctrl-C reaches the watcher rather than the shell just spawned to run it, and the
# default filter rather than `--filter python`, which would watch the code and ignore the
# stylesheet that is the more common thing to be iterating on.
WATCH := "uv run watchfiles --filter default"

[doc('Run the console in the foreground, on a database of its own, restarting on any change')]
serve *args:
    exec {{ WATCH }} 'mainplate serve --port {{ DEV_PORT }} {{ args }}' src/mainplate

[doc('Run it on a throwaway database, for poking at a page without touching real sessions')]
demo *args:
    exec {{ WATCH }} 'mainplate serve --port {{ DEV_PORT }} --database {{ DEMO_DATABASE }} {{ args }}' src/mainplate

# The same conversations the gallery renders, written into a real store so the console can be
# driven rather than looked at: the sidebar reordering between branches, a fork actually being
# made, the rail projecting onto a transcript that came out of SQLite.
#
# Idempotent and never destructive, so running it against a database that already holds real
# sessions adds to them and rewrites nothing.
[doc('Put the gallery fixtures into the demo database')]
seed *args:
    uv run python -m scripts.seed {{ if args == "" { DEMO_DATABASE } else { args } }}

# `uv sync` first, and it is not a convenience: the unit names this checkout's interpreter, so an
# install from a stale environment points systemd at a venv missing whatever was just added. Run
# it again after any change to put the new code in front of the browser.
[doc('Install this checkout as a user systemd service, on the default port')]
install *args:
    uv sync
    uv run mainplate install {{ args }}

[doc('Stop and remove the user systemd service, keeping its settings and its sessions')]
uninstall *args:
    uv run mainplate uninstall {{ args }}

[doc('Follow the service log')]
logs *args:
    journalctl --user -u mainplate -f {{ args }}
