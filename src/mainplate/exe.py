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
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

import h11
from without_async import timeout
from without_http import ConnectionPool
from without_http import request

REFLECTION_URL: Final = "https://reflection.int.exe.xyz/integrations"

# Short, because this runs inside `mainplate install` while somebody waits, and its answer is an
# optimisation rather than a requirement: a slow or absent reflection means the install writes a
# template to edit instead of a profile that was ready to use.
PATIENCE: Final = timedelta(seconds=5)

# What exe.dev calls an integration that fronts model providers.
LLM: Final = "llm"


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
    guessing wrong writes a profile that never answers; a team gateway is two edits to
    `config.toml` and is named in the file's own comments.
    """
    return f"https://{name}.int.exe.xyz"


def parse_gateways(document: bytes) -> tuple[Gateway, ...]:
    """
    Every LLM integration in a reflection document, and nothing else in it.

    Tolerant of shape on purpose, in the direction the error-handling rule asks for: an entry
    missing a name, or a document with no integrations at all, is skipped rather than raised on,
    because reflection is describing a VM this process merely happens to be on. What it will not
    do is invent one.
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
    return tuple(
        Gateway(name=name, base_url=integration_url(name))
        for entry in integrations
        if isinstance(entry, dict) and entry.get("type") == LLM and isinstance(name := entry.get("name"), str) and name
    )


async def discover_gateways() -> tuple[Gateway, ...]:
    """
    The LLM integrations this VM has, or nothing at all anywhere that is not an exe.dev VM.

    Every failure is the same answer, and it is not an error: off a VM the hostname does not
    resolve, on a VM without reflection it refuses, and either way what the caller does is write a
    configuration file with no gateway profile in it. Raising would turn "you are not on exe.dev"
    into a failed install.
    """
    try:
        async with (
            ConnectionPool() as pool,
            timeout(PATIENCE),
            request(pool, "GET", REFLECTION_URL) as (head, body),
        ):
            if head.status != 200:
                return ()
            document = await body.read()
    except OSError, TimeoutError, h11.RemoteProtocolError:
        return ()
    return parse_gateways(document)
