# The client the console tests drive the server with, which is `without`'s own in-memory one.
#
# `loopback_client` is `serving` minus the kernel: the production connection pool encodes the
# request, the server's own connection loop decodes it and drives the app, and the bytes cross a
# pipe instead of a socket. So the wire is still under test while nothing binds a port, which is
# what lets the suite run these in parallel without port churn.
#
# Every write this console takes is a form post, so that is the only body shape here.

from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlencode

from without_asgi import ASGIApp
from without_http import Client
from without_http import request
from without_http.testing import loopback_client

# The authority an in-memory server answers to, since a pipe has no port to name.
BASE = "http://testserver"
FORM = ((b"content-type", b"application/x-www-form-urlencoded"),)


@dataclass(frozen=True, slots=True)
class Answer:
    """One response, already read."""

    status: int
    headers: Mapping[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode()

    @property
    def location(self) -> str:
        return self.headers["location"]


@dataclass(frozen=True, slots=True)
class Caller:
    """The console as the two request shapes these tests make, and nothing more."""

    client: Client

    async def get(self, path: str) -> Answer:
        return await self.send("GET", path)

    async def post(self, path: str, form: Mapping[str, str] | None = None) -> Answer:
        return await self.send("POST", path, form=form)

    async def send(self, method: str, path: str, form: Mapping[str, str] | None = None) -> Answer:
        headers = () if form is None else FORM
        body = b"" if form is None else urlencode(form).encode()
        async with request(self.client, method, f"{BASE}{path}", headers=headers, body=body) as response:
            return Answer(
                status=response.head.status,
                headers={name.decode().lower(): value.decode() for name, value in response.head.headers},
                body=await response.body.read(),
            )


@asynccontextmanager
async def calling(app: ASGIApp) -> AsyncIterator[Caller]:
    """`app` served in memory for the block, with a caller pointed at it."""
    async with loopback_client(app) as client:
        yield Caller(client=client)
