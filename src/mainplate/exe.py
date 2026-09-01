# Finding the LLM gateway an exe.dev VM already has, so an install there needs no key at all.
#
# exe.dev attaches integrations to a VM and injects their credentials at its own edge, so a
# process on the box reaches `https://llm.int.exe.xyz` with nothing in its environment and nothing
# on its disk. That is the best version of the secrets story available to this project: there is
# no credential to store, rotate, or leak, because the VM never has one.
#
# Which integrations a VM has is answered by the `reflection` integration, itself attached by
# default. So discovery is one request to a hostname that exists only inside a VM, and everything
# here is written to come back empty rather than to fail when that hostname is not there: off
# exe.dev this finds nothing and the install writes an ordinary template.

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Final
from urllib.parse import urlsplit

import h11
from without_async import timeout
from without_http import ConnectionPool
from without_http import request

from mainplate.forge import Repository

REFLECTION_URL: Final = "https://reflection.int.exe.xyz/integrations"

# Short, because this runs inside `mainplate install` while somebody waits, and its answer is an
# optimisation rather than a requirement: a slow or absent reflection means the install writes a
# template to edit instead of an endpoint that was ready to use.
PATIENCE: Final = timedelta(seconds=5)

# What exe.dev calls an integration that fronts model providers.
LLM: Final = "llm"

# And one that fronts a git repository. Each attached integration is one repository, so the list of
# them *is* the list a session can be started on.
GITHUB: Final = "github"

# What a repository from here is called where a session records it, so an id says which forge
# reached it as well as which attachment it is. Names the path to GitHub rather than just GitHub,
# because a later forge reaching the same repositories through an App is a different answer to the
# same question and a session has to say which one it was started on.
FORGE: Final = "exe-github"

# The clone URL inside an integration's help text, which is where exe.dev publishes it. Anchored on
# the scheme and the `.git` suffix rather than on the surrounding words, so a help string that
# rephrases itself still parses and one that carries no URL at all is skipped.
CLONE: Final = re.compile(r"https://[^\s'\"]+\.git")


@dataclass(frozen=True, slots=True)
class Gateway:
    """One LLM integration attached to this VM: what it is called, and where it answers."""

    name: str
    base_url: str


def integration_url(name: str) -> str:
    """
    Where an attached personal integration answers.

    Personal integrations are `<name>.int.exe.xyz` and a team's are `<name>.team.exe.xyz`. Only the
    personal form is built here, because that is the one a default account gets and because
    guessing wrong writes an endpoint that never answers; a team gateway is two edits to
    `config.yaml` and is named in the file's own comments.

    Used for repositories as well as gateways, and deliberately in preference to the aggregate
    `github.int.exe.xyz` that an integration's help text prints: an integration's own hostname
    reaches exactly the one attachment, which is what tells two attachments of the same repository
    apart and what keeps each one able to reach only its own.
    """
    return f"https://{name}.int.exe.xyz"


def integrations_in(document: bytes) -> tuple[Mapping[str, object], ...]:
    """
    The integration entries a reflection document holds, and nothing else in it.

    Tolerant of shape on purpose, in the direction the error-handling rule asks for: a document
    that is not one, or that carries no integrations, is nothing rather than a failure, because
    reflection is describing a VM this process merely happens to be on. What it will not do is
    invent one.
    """
    try:
        found = json.loads(document)
    except json.JSONDecodeError:
        return ()
    if not isinstance(found, dict):
        return ()
    integrations = found.get("integrations")
    if not isinstance(integrations, list):
        return ()
    return tuple(entry for entry in integrations if isinstance(entry, dict))


def parse_gateways(document: bytes) -> tuple[Gateway, ...]:
    """Every LLM integration in a reflection document, as somewhere an endpoint can be written for."""
    return tuple(
        Gateway(name=name, base_url=integration_url(name))
        for entry in integrations_in(document)
        if entry.get("type") == LLM and isinstance(name := entry.get("name"), str) and name
    )


async def attached() -> bytes | None:
    """
    What reflection says is attached to this VM, or nothing at all anywhere that is not one.

    Every failure is the same answer, and it is not an error: off a VM the hostname does not
    resolve, on a VM without reflection it refuses, and either way what the caller does is carry on
    with nothing. Raising would turn "you are not on exe.dev" into a failed install or a console
    that will not start.
    """
    try:
        async with (
            ConnectionPool() as pool,
            timeout(PATIENCE),
            request(pool, "GET", REFLECTION_URL) as (head, body),
        ):
            if head.status != 200:
                return None
            return await body.read()
    except OSError, TimeoutError, h11.RemoteProtocolError:
        return None


async def discover_gateways() -> tuple[Gateway, ...]:
    """The LLM integrations this VM has, or nothing at all anywhere that is not an exe.dev VM."""
    document = await attached()
    return () if document is None else parse_gateways(document)


def parse_repositories(document: bytes) -> tuple[Repository, ...]:
    """
    Every GitHub integration in a reflection document, as repositories a session can be started on.

    Which repository an integration serves is read out of its `help`, because that is the only
    field carrying it: the integration's *name* is what somebody called the attachment and says
    nothing about what is behind it. An entry whose help holds no clone URL is skipped rather than
    guessed at, in the direction the error-handling rule asks for.

    The URL is then rebuilt on the integration's **own** hostname rather than used as published.
    `help` prints the aggregate `github.int.exe.xyz`, which resolves to whichever attachment
    happens to serve that repository; `<integration>.int.exe.xyz` resolves to exactly one. That
    matters twice over. It is what lets the same repository be attached more than once - one acting
    as your GitHub user, one as the app, one read-only - and be told apart. And it is narrower:
    an integration's own host serves only its own repository and answers "Repository not found"
    for anything else, where the aggregate serves all of them.

    Either way there is no credential here, because there is none to have: exe.dev injects at its
    own edge, so the URL is the whole of what reaching the repository takes.
    """
    return tuple(
        Repository(forge=FORGE, key=name, name=repository, url=f"{integration_url(name)}/{repository}.git")
        for entry in integrations_in(document)
        if entry.get("type") == GITHUB
        and isinstance(name := entry.get("name"), str)
        and name
        and (published := clone_url(entry.get("help"))) is not None
        and (repository := repository_name(published)) is not None
    )


def clone_url(help_text: object) -> str | None:
    """The URL in `git clone <url>`, or nothing where the help does not carry one."""
    if not isinstance(help_text, str):
        return None
    found = CLONE.search(help_text)
    return found.group(0) if found else None


def repository_name(url: str) -> str | None:
    """`owner/repo` as a person reads it, taken from the clone URL's own path."""
    path = urlsplit(url).path.removesuffix(".git").strip("/")
    return path if path.count("/") == 1 and all(path.split("/")) else None


@dataclass(frozen=True, slots=True)
class ExeDevGitHub:
    """
    GitHub as an exe.dev VM already reaches it, which is with no credential anywhere.

    Named for both halves because both matter and a later forge will share one of them: a plain
    `GitHub` reaching the same repositories through an App is a different thing to configure, a
    different thing to trust, and a different set of repositories, so the two are not one class
    with a flag.

    A GitHub integration attached to a VM serves its repository at a hostname that resolves only
    inside that VM, with credentials injected at exe.dev's edge. So this holds nothing, needs
    nothing configured, and off exe.dev simply answers with nothing.
    """

    @property
    def name(self) -> str:
        return FORGE

    async def offers(self) -> tuple[Repository, ...]:
        document = await attached()
        return () if document is None else parse_repositories(document)
