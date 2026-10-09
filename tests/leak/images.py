"""Runtime image fixtures containing samples only in removable metadata."""

import struct
import zlib


def _chunk(kind: bytes, payload: bytes) -> bytes:
    """Encode one PNG chunk and its CRC."""
    body: bytes = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def png_with_metadata(sample: str) -> bytes:
    """Build a one-pixel PNG with a sample in its removable tEXt chunk."""
    header: bytes = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    image_data: bytes = zlib.compress(b"\x00\xff\xff\xff")
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"tEXt", b"Comment\x00" + sample.encode())
        + _chunk(b"IDAT", image_data)
        + _chunk(b"IEND", b"")
    )


def jpeg_with_metadata(sample: str) -> bytes:
    """Build a minimal JPEG carrying a sample in EXIF and comment segments."""
    exif: bytes = b"Exif\x00\x00" + sample.encode()
    exif_segment: bytes = b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif
    comment: bytes = sample.encode()
    comment_segment: bytes = b"\xff\xfe" + struct.pack(">H", len(comment) + 2) + comment
    frame: bytes = b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    scan: bytes = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x00\xff\xd9"
    return b"\xff\xd8" + exif_segment + comment_segment + frame + scan
