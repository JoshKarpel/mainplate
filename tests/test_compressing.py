from __future__ import annotations

import random
from compression import zstd

import brotli  # type: ignore[import-untyped]  # the bindings ship no types
import pytest
from calling import BASE
from calling import calling
from conftest import started
from without_asgi import ASGIApp
from without_asgi.compression import MAX_ZSTD_WINDOW_LOG
from without_http import request

from mainplate.compressing import COMPRESSORS
from mainplate.compressing import compressible
from mainplate.service import Service


def transcript_shaped(size: int) -> bytes:
    """
    Lines of markup over a vocabulary, the shape a transcript is: each line compresses against the
    ones near it, while nothing repeats at the distance one render is from the next.

    Not random bytes, which is the obvious choice and measures nothing: on data with almost no
    matches, both codecs' fast match finders stride ahead and pass over the second copy entirely, so
    a widened window and a default one both send it again in full.
    """
    chance = random.Random(2031)
    words = ["".join(chance.choices("abcdefghijklmnopqrstuvwxyz", k=chance.randint(2, 9))) for _ in range(5000)]
    lines: list[str] = []
    written = 0
    while written < size:
        line = f'<span class="line" data-gutter="{len(lines)}">{" ".join(chance.choices(words, k=10))}</span>\n'
        lines.append(line)
        written += len(line)
    return "".join(lines).encode()


# Wider than either codec's default window and narrower than the widest each takes: 5 MiB, where
# zstd's default reaches back 2 MiB and brotli's 4, and the windows here are 8 and 16. Through the
# defaults the second copy costs within a percent of the first; through these, under one.
RENDER = transcript_shaped(5 * 2**20)


class TestWhatIsCompressed:
    @pytest.mark.parametrize(
        "content_type", [b"text/event-stream", b"text/event-stream; charset=utf-8", b"Text/Event-Stream"]
    )
    def test_the_live_connection_is(self, content_type: bytes) -> None:
        """What `without` leaves out by default, taken back in; `compressible` says why."""
        assert compressible(content_type)

    @pytest.mark.parametrize("content_type", [b"text/html; charset=utf-8", b"application/json"])
    def test_a_page_and_a_document_still_are(self, content_type: bytes) -> None:
        assert compressible(content_type)

    @pytest.mark.parametrize("content_type", [b"image/png", b"font/woff2", None])
    def test_what_is_already_compressed_or_says_nothing_still_is_not(self, content_type: bytes | None) -> None:
        assert not compressible(content_type)


class TestTheWindow:
    """
    The whole point of the table: one whole render after another down one connection costs the
    second time only what changed. gzip is not here, because its window is 32 KiB and reaches no
    earlier message of any size worth sending.
    """

    @pytest.mark.parametrize("coding", [b"zstd", b"br"])
    def test_a_render_sent_again_down_one_stream_costs_almost_nothing(self, coding: bytes) -> None:
        compressor = COMPRESSORS[coding]()
        first = compressor.compress(RENDER) + compressor.flush_block()
        again = compressor.compress(RENDER) + compressor.flush_block()
        assert len(again) < len(first) / 50

    def test_zstd_is_preferred_to_brotli(self) -> None:
        """A tie between codings a browser offers at one weight goes to zstd; `COMPRESSORS` says why."""
        assert list(COMPRESSORS) == [b"zstd", b"br", b"gzip"]


class TestWhatABrowserIsSent:
    async def test_a_page_comes_compressed_in_the_coding_offered_and_decodes_to_itself(
        self, app: ASGIApp, service: Service
    ) -> None:
        session = await started(service, "what is a mainplate")
        async with calling(app) as caller:
            plain = await caller.get(f"/sessions/{session.id}")
            encoded = await caller.send("GET", f"/sessions/{session.id}", headers=((b"accept-encoding", b"br"),))
        assert "content-encoding" not in plain.headers
        assert encoded.headers["content-encoding"] == "br"
        assert "accept-encoding" in encoded.varies_by
        assert brotli.decompress(encoded.body) == plain.body

    async def test_the_live_connection_comes_compressed_and_its_first_message_decodes(
        self, app: ASGIApp, service: Service
    ) -> None:
        """
        Read raw, a chunk at a time, because the stream has no end: the first message is whole once
        the decoded bytes hold the blank line that closes an event, and that is where the test stops.
        """
        session = await started(service, "what is a mainplate")
        sent = ((b"cookie", b"zone=UTC"), (b"accept-encoding", b"gzip, deflate, br, zstd"))
        async with (
            calling(app) as caller,
            request(caller.client, "GET", f"{BASE}/fragments/stream?session={session.id}", headers=sent) as response,
        ):
            coding = dict(response.head.headers).get(b"content-encoding")
            # Held to the window a browser takes, so a frame declaring more fails here as it would there.
            decompressor = zstd.ZstdDecompressor(
                options={zstd.DecompressionParameter.window_log_max: MAX_ZSTD_WINDOW_LOG}
            )
            decoded = b""
            async for chunk in response.body:
                decoded += decompressor.decompress(chunk)
                if b"\n\n" in decoded:
                    break
        assert coding == b"zstd"
        assert b"what is a mainplate" in decoded
