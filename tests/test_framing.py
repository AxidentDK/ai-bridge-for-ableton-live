"""Unit tests for the wire framing — pure, no Live required.

Run with pytest, or directly:  python tests/test_framing.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import pytest  # noqa: E402
except ImportError:  # standalone runner without pytest installed
    import contextlib

    class pytest:  # noqa: N801 — minimal shim: only raises() is used here
        @staticmethod
        @contextlib.contextmanager
        def raises(exc_type):
            try:
                yield
            except exc_type:
                return
            raise AssertionError(f"expected {exc_type.__name__} to be raised")

from remote_script import framing  # noqa: E402


class FakeSock:
    """A blocking-socket stand-in: recv drains a byte buffer, sendall captures."""

    def __init__(self, data: bytes = b""):
        self._in = data
        self.sent = bytearray()

    def recv(self, n: int) -> bytes:
        block, self._in = self._in[:n], self._in[n:]
        return block

    def sendall(self, data: bytes) -> None:
        self.sent += data


def test_encode_decode_roundtrip():
    msg = {"id": 42, "method": "get", "params": {"path": "live_set", "prop": "tempo"}}
    frame = framing.encode(msg)
    assert len(frame) == framing.HEADER_SIZE + (len(frame) - framing.HEADER_SIZE)
    # header declares the payload length
    (declared,) = framing._HEADER.unpack(frame[: framing.HEADER_SIZE])
    assert declared == len(frame) - framing.HEADER_SIZE
    assert framing.decode(frame[framing.HEADER_SIZE :]) == msg


def test_write_then_read_one_frame():
    sock = FakeSock()
    msg = {"id": 1, "ok": True, "result": 120.0}
    framing.write_frame(sock, msg)
    assert framing.read_frame(FakeSock(bytes(sock.sent))) == msg


def test_multiple_frames_in_order():
    a = {"id": 1, "method": "ping"}
    b = {"id": 2, "method": "get", "params": {"path": "live_set", "prop": "is_playing"}}
    sock = FakeSock(framing.encode(a) + framing.encode(b))
    assert framing.read_frame(sock) == a
    assert framing.read_frame(sock) == b
    assert framing.read_frame(sock) is None  # clean EOF at boundary


def test_unicode_survives():
    msg = {"name": "Vöcal åäö — Deep ♭"}
    assert framing.read_frame(FakeSock(framing.encode(msg))) == msg


def test_empty_payload_is_impossible_but_zero_len_handled():
    # a zero-length frame decodes the empty string -> JSON error is fine to raise
    sock = FakeSock(framing._HEADER.pack(0))
    with pytest.raises(Exception):
        framing.read_frame(sock)


def test_clean_eof_returns_none():
    assert framing.read_frame(FakeSock(b"")) is None


def test_mid_frame_close_raises():
    partial = framing.encode({"id": 9, "method": "ping"})[:-3]  # chop payload tail
    with pytest.raises(framing.ProtocolError):
        framing.read_frame(FakeSock(partial))


def test_oversized_declared_frame_rejected():
    sock = FakeSock(framing._HEADER.pack(framing.MAX_FRAME + 1))
    with pytest.raises(framing.ProtocolError):
        framing.read_frame(sock)


if __name__ == "__main__":
    # standalone runner (no pytest needed)
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception:
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
