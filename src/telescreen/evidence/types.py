"""Evidence kind classification for transient uploads."""

from enum import StrEnum
from pathlib import PurePath

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode


class EvidenceKind(StrEnum):
    """Supported evidence payload categories."""

    TEXT = "text"
    IMAGE = "image"


_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {".txt", ".md", ".log", ".csv", ".json", ".yaml", ".yml"}
)


def classify(filename: str, data: bytes) -> tuple[EvidenceKind, str]:
    """Classify an upload by extension and verify its encoding or image signature."""
    extension: str = PurePath(filename).suffix.lower()
    if extension in _TEXT_EXTENSIONS:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise TelescreenError(
                ErrorCode.UNSUPPORTED_MEDIA_TYPE, "unsupported evidence media type"
            ) from None
        return EvidenceKind.TEXT, "text/plain"
    if extension == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return EvidenceKind.IMAGE, "image/png"
    if extension in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff"):
        return EvidenceKind.IMAGE, "image/jpeg"
    if extension == ".webp" and len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return EvidenceKind.IMAGE, "image/webp"
    raise TelescreenError(ErrorCode.UNSUPPORTED_MEDIA_TYPE, "unsupported evidence media type")
