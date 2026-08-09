"""Length-prefixed JSON framing for the bridge wire protocol.

See ``docs/PROTOCOL.md``. Each frame on the wire is::

    [4-byte big-endian unsigned length N][N bytes of UTF-8 JSON]

Pure stdlib, Python 3.11+. This exact framing is used by both the remote script
(inside Live) and the host (outside). No Live API dependency — unit-testable on
its own.
"""
from __future__ import annotations

import json
import struct

_HEADER = struct.Struct(">I")          # 4-byte big-endian unsigned length
HEADER_SIZE = _HEADER.size
MAX_FRAME = 64 * 1024 * 1024           # 64 MiB ceiling — refuse absurd frames


class ProtocolError(Exception):
    """A malformed or oversized frame on the wire."""


def encode(msg: dict) -> bytes:
    """Serialize a message dict to a length-prefixed UTF-8 JSON frame."""
    payload = json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_FRAME:
        raise ProtocolError(f"frame too large to send: {len(payload)} bytes")
    return _HEADER.pack(len(payload)) + payload


def decode(payload: bytes) -> dict:
    """Decode a raw JSON payload (without the length header) to a dict."""
    return json.loads(payload.decode("utf-8"))


def recv_exactly(sock, n: int) -> bytes | None:
    """Read exactly ``n`` bytes from a blocking socket.

    Returns the bytes, or ``None`` on a clean EOF before any of the ``n`` bytes
    arrive-through (peer closed). Raises ``ProtocolError`` on a mid-frame EOF.
    """
    chunks: list[bytes] = []
    got = 0
    while got < n:
        block = sock.recv(n - got)
        if not block:
            if got == 0:
                return None            # clean close on a frame boundary
            raise ProtocolError(f"connection closed mid-frame ({got}/{n} bytes)")
        chunks.append(block)
        got += len(block)
    return b"".join(chunks)


def read_frame(sock) -> dict | None:
    """Read one whole frame from a blocking socket. ``None`` on clean EOF."""
    header = recv_exactly(sock, HEADER_SIZE)
    if header is None:
        return None
    (length,) = _HEADER.unpack(header)
    if length > MAX_FRAME:
        raise ProtocolError(f"declared frame too large: {length} bytes")
    payload = recv_exactly(sock, length) if length else b""
    if length and payload is None:
        raise ProtocolError("connection closed before frame payload")
    return decode(payload)


def write_frame(sock, msg: dict) -> None:
    """Send one whole frame on a blocking socket."""
    sock.sendall(encode(msg))
