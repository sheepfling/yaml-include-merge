"""Read resolver-owned binary streams with optional byte bounds."""

from typing import BinaryIO, cast

_DEFAULT_MAX_CHUNK_SIZE = 65_536

def _read_binary_chunk(stream: BinaryIO, size: int) -> bytes | None:
    """Keep a resolver's possibly non-blocking read result at the boundary."""
    value: object = stream.read(size)
    return cast(bytes | None, value)

def read_bounded(stream: BinaryIO, limit: int | None, max_chunk_size: int | None = None) -> bytes:
    """Read a binary stream in chunks, stopping at ``limit`` or EOF.

    ``None`` reads to EOF. Bounded callers include one probe byte in ``limit``
    so they can distinguish a complete file from one that exceeds the budget.
    """
    content = bytearray()
    remaining = limit
    if max_chunk_size is None or max_chunk_size < 1:
        chunk_size = _DEFAULT_MAX_CHUNK_SIZE
    else:
        chunk_size = max_chunk_size
    while remaining is None or remaining > 0:
        requested = min(remaining, chunk_size) if remaining is not None else chunk_size
        chunk = _read_binary_chunk(stream, requested)
        if not isinstance(chunk, bytes):
            if chunk is None:
                raise OSError("include resolver stream returned no blocking read result")
            raise TypeError("include resolver streams must return bytes")
        if len(chunk) > requested:
            raise ValueError("include resolver stream returned more bytes than requested")
        if not chunk:
            break
        content.extend(chunk)
        if remaining is not None:
            remaining -= len(chunk)
    return bytes(content)
