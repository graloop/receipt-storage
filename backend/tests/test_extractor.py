# The code around the real API call, with the Anthropic client replaced by a
# stand-in: what gets sent, and what happens with each kind of answer.

import base64
import io
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from PIL import Image
from pydantic import SecretStr

from app import storage
from app.config import get_settings

from .conftest import make_extracted

# conftest swaps read_receipt for a fake; this file tests the real one.
from app.extractor import ExtractionError, read_receipt  # isort: skip


class StubMessages:
    def __init__(self, answer):
        self.answer = answer
        self.sent = None

    def parse(self, **kwargs):
        self.sent = kwargs
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def api(monkeypatch):
    """A key that is set, and a client that goes nowhere."""
    monkeypatch.setattr(get_settings(), "anthropic_api_key", SecretStr("test-key"))
    stub = StubMessages(SimpleNamespace(stop_reason="end_turn", parsed_output=make_extracted()))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **_: SimpleNamespace(messages=stub))
    return stub


def jpeg_sent(sent) -> Image.Image:
    block = sent["messages"][0]["content"][0]
    assert block["type"] == "image"
    assert block["source"]["media_type"] == "image/jpeg"
    return Image.open(io.BytesIO(base64.standard_b64decode(block["source"]["data"])))


def api_error(cls, status):
    response = httpx2.Response(status, request=httpx2.Request("POST", "http://test"))
    return cls("nope", response=response, body=None)


def picture(size, mode="RGB", image_format="PNG", **save_args) -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, "white").save(out, format=image_format, **save_args)
    return out.getvalue()


def test_no_key_says_so_without_calling_anything():
    with pytest.raises(ExtractionError, match="No API key"):
        read_receipt(b"x", "pdf")


def test_uses_the_configured_model(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "receipt_model", "some-other-model")
    read_receipt(picture((10, 10)), "image")
    assert api.sent["model"] == "some-other-model"


def test_default_model_is_haiku():
    assert get_settings().receipt_model == "claude-haiku-5-5"


def test_big_image_is_shrunk_to_1600_on_the_long_side(api):
    read_receipt(picture((3000, 2000)), "image")
    assert jpeg_sent(api.sent).size == (1600, 1067)


def test_small_image_is_not_enlarged(api):
    read_receipt(picture((400, 300)), "image")
    assert jpeg_sent(api.sent).size == (400, 300)


def test_transparent_screenshot_gets_a_white_background(api):
    read_receipt(picture((20, 20), mode="RGBA"), "image")
    assert jpeg_sent(api.sent).mode == "RGB"


def test_sideways_phone_photo_is_turned_upright(api):
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90 degrees to display"
    read_receipt(picture((200, 100), image_format="JPEG", exif=exif), "image")
    assert jpeg_sent(api.sent).size == (100, 200)


def test_pdf_goes_as_a_document_before_the_text(api):
    read_receipt(b"%PDF-1.4 fake", "pdf")
    blocks = api.sent["messages"][0]["content"]
    assert blocks[0]["type"] == "document"
    assert blocks[0]["source"]["media_type"] == "application/pdf"
    assert base64.standard_b64decode(blocks[0]["source"]["data"]) == b"%PDF-1.4 fake"
    assert blocks[1]["type"] == "text"


def test_damaged_image_is_refused(api):
    with pytest.raises(storage.FileRejected):
        read_receipt(picture((50, 50))[:60], "image")


@pytest.mark.parametrize(
    ("stop_reason", "parsed"),
    [("refusal", None), ("max_tokens", None), ("end_turn", None), ("refusal", make_extracted())],
)
def test_answers_without_a_usable_receipt(api, stop_reason, parsed):
    api.answer = SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed)
    with pytest.raises(ExtractionError, match="couldn't read"):
        read_receipt(b"%PDF-", "pdf")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (api_error(anthropic.AuthenticationError, 401), "API key was refused"),
        (api_error(anthropic.PermissionDeniedError, 403), "isn't allowed"),
        (api_error(anthropic.NotFoundError, 404), "RECEIPT_MODEL"),
        (api_error(anthropic.RateLimitError, 429), "Too many receipts"),
        (api_error(anthropic.BadRequestError, 400), "refused this file"),
        (api_error(anthropic.InternalServerError, 500), "had a problem"),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "http://test")), "Couldn't reach"),
        (anthropic.APITimeoutError(request=httpx2.Request("POST", "http://test")), "Couldn't reach"),
        (ValueError("not JSON"), "couldn't read"),
    ],
)
def test_api_problems_become_readable_messages(api, error, expected):
    api.answer = error
    with pytest.raises(ExtractionError, match=expected):
        read_receipt(b"%PDF-", "pdf")


def test_error_messages_never_contain_the_key(api):
    api.answer = api_error(anthropic.AuthenticationError, 401)
    with pytest.raises(ExtractionError) as caught:
        read_receipt(b"%PDF-", "pdf")
    assert "test-key" not in str(caught.value)
    assert "test-key" not in repr(get_settings())
