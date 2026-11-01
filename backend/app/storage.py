# Everything about the uploaded files: checking what they really are, keeping
# them under uploads/ with a random name, and shrinking images for the model.

import io
import os
import uuid
from dataclasses import dataclass

import pillow_heif
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import get_settings

pillow_heif.register_heif_opener()  # Lets Pillow open iPhone HEIC photos.

settings = get_settings()
UPLOADS_DIR = os.path.join(settings.data_dir, "uploads")
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_IMAGE_SIDE = 1600

# What Pillow calls each format we accept -> the extension we save it under.
# MPO is how Pillow names the JPEGs some phones make.
_IMAGE_EXTENSIONS = {"JPEG": ".jpg", "MPO": ".jpg", "PNG": ".png", "WEBP": ".webp", "HEIF": ".heic"}
MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".pdf": "application/pdf",
}


class FileRejected(Exception):
    """The file can't be used. The message is shown to the person."""


@dataclass
class CheckedFile:
    kind: str  # "image" or "pdf"
    extension: str


def check_file(data: bytes) -> CheckedFile:
    """Look at the content, not the file's name: a name can say anything."""
    if not data:
        raise FileRejected("This file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise FileRejected("This file is too big (15 MB maximum).")
    if data.startswith(b"%PDF-"):
        return CheckedFile("pdf", ".pdf")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
            image_format = image.format
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise FileRejected("This file isn't an image or a PDF.") from None
    if image_format not in _IMAGE_EXTENSIONS:
        raise FileRejected("This file isn't an image or a PDF.")
    return CheckedFile("image", _IMAGE_EXTENSIONS[image_format])


def _opened_upright(data: bytes) -> Image.Image:
    # Phones save photos sideways and note the rotation in the file: apply it.
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        return background
    return image.convert("RGB")


def _to_jpeg(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=85)
    return out.getvalue()


def shrink_for_model(data: bytes) -> bytes:
    """A JPEG at most 1600 px on its long side: enough to read, and much cheaper to send."""
    try:
        image = _opened_upright(data)
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        return _to_jpeg(image)
    except (OSError, ValueError, Image.DecompressionBombError):
        raise FileRejected("This image is damaged and can't be read.") from None


def save_file(data: bytes, extension: str) -> str:
    """Keeps the original under a random name, and answers that name."""
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    name = uuid.uuid4().hex + extension
    with open(os.path.join(UPLOADS_DIR, name), "wb") as f:
        f.write(data)
    return name


def file_path(name: str) -> str:
    return os.path.join(UPLOADS_DIR, name)


def delete_file(name: str | None) -> None:
    if name:
        try:
            os.remove(file_path(name))
        except FileNotFoundError:
            pass


def heic_as_jpeg(name: str) -> bytes:
    """Most browsers can't show HEIC, so the page gets a JPEG copy; the original stays as it is."""
    with open(file_path(name), "rb") as f:
        return _to_jpeg(_opened_upright(f.read()))
