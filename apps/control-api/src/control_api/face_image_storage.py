"""Bounded image decoding and metadata-free enrollment image artifacts."""

import re
import warnings
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from control_api.object_storage import ObjectStorage

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_UPLOAD_FILES = 10


class ImageValidationError(ValueError):
    pass


class FaceImageStorage:
    def __init__(self, root: Path | None, object_storage: ObjectStorage | None = None) -> None:
        self.root = root.resolve() if root is not None else None
        self.object_storage = object_storage

    def save(self, payload: bytes) -> tuple[str, int, int, str]:
        if len(payload) > MAX_FILE_BYTES:
            raise ImageValidationError("IMAGE_TOO_LARGE")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(payload)) as source:
                    if source.format not in {"JPEG", "PNG"}:
                        raise ImageValidationError("UNSUPPORTED_IMAGE_FORMAT")
                    if source.width * source.height > MAX_IMAGE_PIXELS:
                        raise ImageValidationError("IMAGE_DIMENSIONS_EXCEEDED")
                    if getattr(source, "n_frames", 1) != 1:
                        raise ImageValidationError("MULTIFRAME_IMAGE_NOT_SUPPORTED")
                    source.load()
                    oriented = ImageOps.exif_transpose(source)
                    # Construct a fresh image to drop EXIF, comments and ICC metadata.
                    rgb = oriented.convert("RGB")
                    clean = Image.new("RGB", rgb.size)
                    clean.paste(rgb)
                    output = BytesIO()
                    clean.save(output, format="JPEG", quality=90)
                    width, height = clean.size
        except ImageValidationError:
            raise
        except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
            raise ImageValidationError("IMAGE_DIMENSIONS_EXCEEDED") from error
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as error:
            raise ImageValidationError("INVALID_IMAGE") from error

        key = f"{uuid4().hex}.jpg"
        jpeg = output.getvalue()
        if self.object_storage is not None:
            object_key = f"{key[:2]}/{key}"
            self.object_storage.put(object_key, jpeg, "image/jpeg")
            key = object_key
        else:
            if self.root is None:
                raise RuntimeError("face image storage root is not configured")
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.path(key)
            temporary = self.root / f"{key}.tmp"
            try:
                with temporary.open("xb") as target:
                    target.write(jpeg)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return key, width, height, sha256(jpeg).hexdigest()

    def path(self, key: str) -> Path:
        if self.root is None or not re.fullmatch(r"[0-9a-f]{32}\.jpg", key):
            raise ValueError("INVALID_IMAGE_REFERENCE")
        return self.root / key

    def read(self, key: str) -> bytes:
        if self.object_storage is not None:
            return self.object_storage.get(key)
        return self.path(key).read_bytes()

    def remove(self, key: str) -> None:
        if self.object_storage is not None:
            self.object_storage.delete(key)
            return
        self.path(key).unlink(missing_ok=True)
