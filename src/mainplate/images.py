"""
Image values crossing the composer boundary and the checkpoint.

The inbox owns the bytes, not an upload directory: a fork carries its question with it and an
archive takes no attachment away. Only the four raster formats every wire accepts are admitted;
a filename and a browser's MIME declaration are not evidence of what a file contains.
"""

from __future__ import annotations

from base64 import b64decode
from base64 import b64encode
from collections.abc import Sequence
from hashlib import sha256
from typing import Final

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic_ai.messages import BinaryImage
from pydantic_ai.messages import UserContent

from mainplate.pictures import pictured

IMAGE_FIELD: Final = "images"
MAX_IMAGES: Final = 8
MAX_IMAGE_BYTES: Final = 10 * 1024 * 1024
MAX_UPLOAD_BYTES: Final = MAX_IMAGE_BYTES + 200_000


class Image(BaseModel):
    """
    An immutable attachment, JSON-native so every inbox codec carries the same bytes.

    `encoded` is base64 rather than a filesystem reference: attachments must remain available after
    a checkout is archived and when a fork re-asks the message. This costs the checkpoint the bytes
    of every image and base64's expansion; pages carry only their inbox address.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    media_type: str
    encoded: str

    @classmethod
    def parse(cls, name: str, content: bytes) -> Image:
        """Refuse anything outside the common raster formats before it enters a conversation."""
        kind = pictured(content)
        if kind is None:
            raise ValueError("an attachment must be a PNG, JPEG, GIF or WebP image")
        if len(content) > MAX_IMAGE_BYTES:
            raise ValueError(f"images may total at most {MAX_IMAGE_BYTES} bytes")
        return cls(name=name, media_type=kind.media_type, encoded=b64encode(content).decode("ascii"))

    @property
    def content(self) -> bytes:
        """The recorded bytes, not a read of a place that can change beneath a replay."""
        return b64decode(self.encoded, validate=True)

    @property
    def identifier(self) -> str:
        """A stable address for the bytes, shared by inbox and provider-history readings."""
        return sha256(self.content).hexdigest()

    def shown(self) -> BinaryImage:
        """The provider's image value, recovered from the inbox at each replay."""
        return BinaryImage(
            data=self.content, media_type=self.media_type, vendor_metadata={"attachment_name": self.name}
        )


def user_content(said: str, images: Sequence[Image]) -> str | Sequence[UserContent]:
    """Keep text-only messages in their existing shape and show attachments beside their text."""
    if not images:
        return said
    return [*([said] if said else []), *(image.shown() for image in images)]
