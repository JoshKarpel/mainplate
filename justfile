#!/usr/bin/env just --justfile

set ignore-comments

# Not the default port, so a console started for a quick look never takes down one already
# serving on this machine, and a `just serve` can run beside a `just screenshot`.
DEV_PORT := "8101"

[default]
[doc('List available recipes')]
list:
    just --list

alias l := list

[doc('Prepare a fresh clone: dependencies, and pre-commit as a git hook')]
setup:
    uv sync
    uv run pre-commit install

[doc('Run type checking and tests')]
test *args:
    uv run mypy
    uv run pytest {{ args }}

alias t := test

[doc('Format and lint')]
check:
    uv run pre-commit run --all-files
    uv run mypy

[doc('Run the console in the foreground, on a database of its own')]
serve *args:
    uv run mainplate serve --port {{ DEV_PORT }} {{ args }}

[doc("Run it against Pydantic AI's canned model, so a page renders with no provider and no spend")]
demo *args:
    MAINPLATE_MODEL=test uv run mainplate serve --port {{ DEV_PORT }} --database mainplate-demo.db {{ args }}
