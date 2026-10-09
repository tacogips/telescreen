"""Container-level image metadata stripping without decoding pixels."""

import struct

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode

_PNG_SIGNATURE: bytes = b"\x89PNG\r\n\x1a\n"
_PNG_METADATA: frozenset[bytes] = frozenset({b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"tIME"})


def strip_metadata(kind_content_type: str, data: bytes) -> bytes:
    """Remove supported image metadata, rejecting malformed image containers."""
    try:
        if kind_content_type == "image/jpeg":
            return _strip_jpeg(data)
        if kind_content_type == "image/png":
            return _strip_png(data)
        if kind_content_type == "image/webp":
            return _strip_webp(data)
    except (IndexError, struct.error, ValueError):
        raise _malformed() from None
    raise _malformed()


def _malformed() -> TelescreenError:
    """Create the stable malformed-image rejection."""
    return TelescreenError(ErrorCode.EVIDENCE_REJECTED, "malformed_image")


def _strip_jpeg(data: bytes) -> bytes:
    """Remove JPEG APP1-APP15 and comment segments before scan data."""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise _malformed()
    output: bytearray = bytearray(data[:2])
    offset: int = 2
    while offset < len(data):
        marker_start: int = offset
        if data[offset] != 0xFF:
            raise _malformed()
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            raise _malformed()
        marker: int = data[offset]
        offset += 1
        if marker == 0xD9:
            if offset != len(data):
                raise _malformed()
            output.extend(data[marker_start:offset])
            return bytes(output)
        if marker == 0xDA:
            if offset + 2 > len(data):
                raise _malformed()
            segment_length: int = int.from_bytes(data[offset : offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(data):
                raise _malformed()
            output.extend(data[marker_start:])
            if not data.endswith(b"\xff\xd9"):
                raise _malformed()
            return bytes(output)
        if marker in {0x01, *range(0xD0, 0xD8)}:
            output.extend(data[marker_start:offset])
            continue
        if offset + 2 > len(data):
            raise _malformed()
        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        end: int = offset + segment_length
        if segment_length < 2 or end > len(data):
            raise _malformed()
        if not (0xE1 <= marker <= 0xEF or marker == 0xFE):
            output.extend(data[marker_start:end])
        offset = end
    raise _malformed()


def _strip_png(data: bytes) -> bytes:
    """Drop metadata chunks while retaining all remaining bytes and CRCs."""
    if not data.startswith(_PNG_SIGNATURE):
        raise _malformed()
    output: bytearray = bytearray(_PNG_SIGNATURE)
    offset: int = len(_PNG_SIGNATURE)
    saw_header: bool = False
    while offset + 12 <= len(data):
        length: int = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type: bytes = data[offset + 4 : offset + 8]
        end: int = offset + 12 + length
        if end > len(data) or not chunk_type.isalpha():
            raise _malformed()
        if not saw_header and chunk_type != b"IHDR":
            raise _malformed()
        if chunk_type == b"IHDR":
            if saw_header or length != 13:
                raise _malformed()
            saw_header = True
        if chunk_type == b"IEND":
            if length != 0 or end != len(data):
                raise _malformed()
            output.extend(data[offset:end])
            return bytes(output)
        if chunk_type not in _PNG_METADATA:
            output.extend(data[offset:end])
        offset = end
    raise _malformed()


def _strip_webp(data: bytes) -> bytes:
    """Drop WebP EXIF/XMP chunks, clear VP8X flags and recalculate RIFF size."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise _malformed()
    declared_size: int = int.from_bytes(data[4:8], "little") + 8
    if declared_size != len(data):
        raise _malformed()
    offset: int = 12
    chunks: list[bytes] = []
    while offset < len(data):
        if offset + 8 > len(data):
            raise _malformed()
        chunk_type: bytes = data[offset : offset + 4]
        size: int = int.from_bytes(data[offset + 4 : offset + 8], "little")
        end: int = offset + 8 + size
        padded_end: int = end + (size % 2)
        if padded_end > len(data):
            raise _malformed()
        body: bytes = data[offset + 8 : end]
        padding: bytes = data[end:padded_end]
        if chunk_type == b"VP8X":
            if size < 10:
                raise _malformed()
            flags: int = body[0] & ~0x0C
            body = bytes((flags,)) + body[1:]
        if chunk_type not in {b"EXIF", b"XMP "}:
            chunks.append(chunk_type + size.to_bytes(4, "little") + body + padding)
        offset = padded_end
    payload: bytes = b"WEBP" + b"".join(chunks)
    return b"RIFF" + len(payload).to_bytes(4, "little") + payload
