"""Composer images remain the same question at the HTTP, inbox and provider boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest
from calling import calling
from conftest import DEFAULT_CHOICE
from conftest import started
from pydantic_ai.messages import BinaryContent
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import UserPromptPart
from without_asgi import ASGIApp
from without_http import request

from mainplate import records
from mainplate.console import LINKS
from mainplate.console import NotAMessage
from mainplate.console import parse_form_send
from mainplate.conversation import opened_key
from mainplate.conversation import parse_messages
from mainplate.conversation import posted_in
from mainplate.conversation import recorded_messages
from mainplate.conversation import recorded_prompt
from mainplate.images import MAX_IMAGES
from mainplate.images import Image
from mainplate.images import user_content
from mainplate.reference import Facts
from mainplate.reference import Reference
from mainplate.service import Service

PNG = Path("src/mainplate/assets/icon-192.png").read_bytes()


def multipart(*files: tuple[str, bytes], text: str = "Inspect these", disposition: str = "here") -> bytes:
    """A native browser form with deliberate filenames and an untrusted MIME claim."""
    fields = [("prompt", text.encode()), ("disposition", disposition.encode())]
    parts = [
        b'--test-boundary\r\nContent-Disposition: form-data; name="' + name.encode() + b'"\r\n\r\n' + content + b"\r\n"
        for name, content in fields
    ]
    parts.extend(
        b'--test-boundary\r\nContent-Disposition: form-data; name="images"; filename="'
        + name.encode()
        + b'"\r\nContent-Type: application/octet-stream\r\n\r\n'
        + content
        + b"\r\n"
        for name, content in files
    )
    return b"".join(parts) + b"--test-boundary--\r\n"


CONTENT_TYPE = "multipart/form-data; boundary=test-boundary"


@pytest.mark.parametrize("text", ["Look at the icon", ""])
def test_a_native_upload_carries_bytes_even_when_the_message_is_only_an_image(text: str) -> None:
    """A posted MIME claim cannot change the image the model receives."""
    sent = parse_form_send(multipart(("first.png", PNG), text=text), CONTENT_TYPE)
    assert sent.said == text
    assert sent.images[0].content == PNG
    assert sent.images[0].media_type == "image/png"
    assert sent.images[0].name == "first.png"


@pytest.mark.parametrize("content", [b"<svg></svg>", b"not an image", b"", b"%PDF-1.7"])
def test_other_formats_are_refused_before_any_message_is_delivered(content: bytes) -> None:
    """Names and declared content types cannot turn text into a raster image."""
    with pytest.raises(NotAMessage, match="PNG, JPEG, GIF or WebP"):
        parse_form_send(multipart(("looks-like.png", content)), CONTENT_TYPE)


def test_too_many_images_are_refused_as_one_question() -> None:
    """The bound applies to the whole batch rather than dropping attachments after the limit."""
    with pytest.raises(NotAMessage, match="at most"):
        parse_form_send(multipart(*((f"{index}.png", PNG) for index in range(MAX_IMAGES + 1))), CONTENT_TYPE)


def test_a_truncated_upload_is_not_a_truncated_message() -> None:
    """A missing closing boundary cannot silently deliver the parts that happened to arrive."""
    with pytest.raises(NotAMessage, match="complete multipart"):
        parse_form_send(multipart(("icon.png", PNG)).removesuffix(b"--test-boundary--\r\n"), CONTENT_TYPE)


def test_recorded_and_replayed_user_content_contains_the_original_image() -> None:
    """The provider-history codec must recover a binary image rather than a base64 mapping."""
    image = Image.parse("checkpoint.png", PNG)
    prompt = records.Prompt.model_validate(recorded_prompt("Inspect it", images=(image,)))
    messages = [ModelRequest(parts=[UserPromptPart(content=user_content(prompt.said, prompt.images))])]
    loaded = parse_messages(recorded_messages(messages))
    part = loaded[0].parts[0]
    assert isinstance(part, UserPromptPart)
    assert not isinstance(part.content, str)
    assert isinstance(part.content[1], BinaryContent)
    assert part.content[1].data == PNG


async def test_an_upload_is_recorded_drawn_and_served_from_the_inbox(app: ASGIApp, service: Service) -> None:
    """No upload directory or checkout file stands between a posted question and its image."""
    session = (await started(service, said="Existing question")).id
    service.references.current = Reference(
        qualified={DEFAULT_CHOICE.model: Facts(traits=(("vision", True),))}, upstream={}
    )
    async with calling(app) as caller:
        async with request(
            caller.client,
            "POST",
            f"http://console/sessions/{session}/messages",
            headers=((b"content-type", CONTENT_TYPE.encode()),),
            body=multipart(("posted.png", PNG), text=""),
        ) as response:
            assert response.head.status == 200
            rendered = await response.body.read()
        entries = posted_in(await service.checkpointer.load(session))
        last = entries[-1].what
        assert isinstance(last, records.Steer)
        image = last.images[0]
        assert image.content == PNG
        address = LINKS.to_attachment(session, image.identifier)
        assert address.encode() in rendered
        served = await caller.get(address)
        assert served.status == 200
        assert served.body == PNG
        assert served.headers["content-type"] == "image/png"
        assert served.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("disposition", ["here", "run", "plugin:handoff"])
async def test_a_refused_image_does_not_enter_the_inbox(app: ASGIApp, service: Service, disposition: str) -> None:
    """Unknown vision and text-only actions must not poison the durable history."""
    session = (await started(service, said="Existing question")).id
    previous = await service.checkpointer.load(session)
    async with calling(app) as caller:
        async with request(
            caller.client,
            "POST",
            f"http://console/sessions/{session}/messages",
            headers=((b"content-type", CONTENT_TYPE.encode()),),
            body=multipart(("posted.png", PNG), disposition=disposition),
        ) as response:
            assert response.head.status == 422
            await response.body.read()
    assert await service.checkpointer.load(session) == previous


async def test_a_fork_reasks_the_image_with_its_text(service: Service) -> None:
    """The branch point re-delivers its attachment even though the inherited prefix stops before it."""
    image = Image.parse("fork.png", PNG)
    session = await started(service, said="Inspect it")
    await service.say(session.id, "Attached question", images=(image,))
    entries = posted_in(await service.checkpointer.load(session.id))
    await service.checkpointer.supply(session.id, opened_key(0), entries[-1].key)
    forked = await service.fork(session.id, at=0, chosen=DEFAULT_CHOICE, said="Rephrased question")
    assert forked is not None
    questions = posted_in(await service.checkpointer.load(forked.id))
    question = questions[-1].what
    assert isinstance(question, records.Prompt)
    assert question.said == "Rephrased question"
    assert question.images == (image,)
