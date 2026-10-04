# How what the console sends is compressed, and why the live connection is compressed at all.
#
# Every message down a page's event stream is a whole current render of the transcript, never a
# delta, and a long session's render runs to megabytes of HTML sent again every time the turn in
# flight records anything. Compressed on its own, each message is about a tenth of that. Compressed
# as one stream, with a window wide enough to reach back over the message before it, the next whole
# render is encoded as references to the last one, which costs what changed and little more: the
# delta the design declined to compute by hand, recovered by the codec. That is what the windows
# below are for, and the reason they are the widest a browser takes.

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from compression import zstd
from typing import Final

from without_asgi import EVENT_STREAM_MEDIA_TYPE
from without_asgi.compression import MAX_ZSTD_WINDOW_LOG
from without_asgi.compression import StreamingCompressor
from without_asgi.compression import brotli_compressor
from without_asgi.compression import compress
from without_asgi.compression import gzip_compressor
from without_asgi.compression import is_compressible
from without_asgi.compression import zstd_compressor
from without_asgi.routing import HttpMiddleware

# Brotli's format ceiling, a window of 16 MiB (RFC 7932), which is also the only limit HTTP sets on
# it. zstd's is `MAX_ZSTD_WINDOW_LOG`, 8 MiB, which RFC 9659 sets for HTTP and `without` refuses past.
WIDEST_BROTLI_WINDOW_LOG: Final = 24


def widest_zstd() -> StreamingCompressor:
    """
    Zstd at its default level with the widest window HTTP allows, which is 8 MiB.

    The options are written inline at the call rather than held in a constant, which is the
    stdlib's stubs' doing: they type `options` as `Mapping[int, int]`, and a type checker refuses
    the `dict[CompressionParameter, int]` it infers for a literal held in a variable.
    """
    return zstd_compressor(options={zstd.CompressionParameter.window_log: MAX_ZSTD_WINDOW_LOG})


def widest_brotli() -> StreamingCompressor:
    """Brotli at `without`'s dynamic quality with the widest window its format has, 16 MiB."""
    return brotli_compressor(lgwin=WIDEST_BROTLI_WINDOW_LOG)


COMPRESSORS: Final[Mapping[bytes, Callable[[], StreamingCompressor]]] = {
    b"zstd": widest_zstd,
    b"br": widest_brotli,
    b"gzip": gzip_compressor,
}
"""
Every coding the console answers in, in its order of preference, which settles a tie between codings
a browser offers at the same weight, as every browser does.

Typed as `StreamingCompressor` rather than the `Compressor` that `compress` takes, because a coding
whose compressor cannot end a block is one `compress` leaves the live connection uncompressed in,
silently. Narrower here, a factory of that kind is a type error rather than a stream that quietly
costs its megabytes again.

**zstd before brotli, which is the reverse of `without`'s own order**, because the cost here is per
message on a held connection rather than per response. Measured over a 5.9 MB render, a fresh zstd
message cost 13 ms against brotli's 64 ms for a ratio about a fifth worse, and a held zstd compressor
settled at about 9 MiB against brotli's 23 MiB. The cost, stated: zstd's window stops at 8 MiB, so a
transcript past that on a browser that takes zstd is compressed from scratch every message, where
brotli would keep finding the last render up to 16 MiB. A browser that offers only brotli gets
brotli with its widest window, and one offering neither gets gzip, whose 32 KiB window reaches no
earlier message at all.

**The windows cost memory on both ends for as long as a page is open**: the compressor a stream holds
here and the decoder the tab holds there, each about the window's size. A response that arrives
whole holds its compressor only while it is encoded, so a page or a fragment pays for the window for
an instant and not for the life of a connection.
"""


def compressible(content_type: bytes | None) -> bool:
    """
    Whether a response of this media type is compressed: `is_compressible`, with the event stream let
    back in.

    `without` leaves `text/event-stream` out by default, and for a reason that holds here as written.
    A held stream encodes each event against a window holding every event before it, so somebody who
    can put text into one event and watch the encrypted length of the next can recover a secret in it
    a guess at a time, which is BREACH with as many samples as they care to take.

    It is taken anyway, as a risk accepted rather than one missed. The console puts no secret of its
    own in a body: there is no CSRF token, and the credential in front of it is a cookie the request
    carries rather than anything a page draws. What a transcript can hold is something a model or a
    tool printed, and recovering that needs a prompt injected into the session *and* the encrypted
    traffic between this console and the page watched at the same time. If that stops being unlikely
    enough, the answer is to mask the secrets the console knows before they are drawn, as a CI log
    does, rather than to give the stream back its megabytes. `docs/design/security.md` records it.
    """
    if content_type is not None and content_type.split(b";")[0].strip().lower() == EVENT_STREAM_MEDIA_TYPE:
        return True
    return is_compressible(content_type)


def compressing() -> HttpMiddleware[object]:
    """
    Answer every response in the best coding the browser offers, the live connection included.

    A request that offers no coding is answered uncompressed, which is why nothing in the suite that
    does not ask for one has to decode anything.
    """
    return compress(COMPRESSORS, compressible=compressible)
