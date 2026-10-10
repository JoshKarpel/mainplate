"""
The raster formats shared by file reads and composer attachments.

Recognition uses bytes, not filenames or MIME claims. Keep the accepted set common to every wire
and the browser, so the person and the model are shown the same thing.
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class Format:
    """One kind of image the console shows a model: its opening bytes and what it is called."""

    signature: bytes
    media_type: str
    name: str


# These four because every wire accepts them and every browser draws them in an `<img>`. WebP
# names its kind after the length of its RIFF container, so `pictured` reads it apart. SVG stays
# text: no provider takes it as a picture, and a file read already anchors its source.
FORMATS: Final = (
    Format(signature=b"\x89PNG\r\n\x1a\n", media_type="image/png", name="PNG"),
    Format(signature=b"\xff\xd8\xff", media_type="image/jpeg", name="JPEG"),
    Format(signature=b"GIF87a", media_type="image/gif", name="GIF"),
    Format(signature=b"GIF89a", media_type="image/gif", name="GIF"),
)
WEBP: Final = Format(signature=b"WEBP", media_type="image/webp", name="WebP")


def pictured(content: bytes) -> Format | None:
    """Which common raster format opens these bytes, or nothing for another kind of file."""
    if content[:4] == b"RIFF" and content[8:12] == WEBP.signature:
        return WEBP
    return next((kind for kind in FORMATS if content.startswith(kind.signature)), None)
