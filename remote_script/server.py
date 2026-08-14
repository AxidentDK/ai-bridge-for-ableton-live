"""Socket server: accept loop + per-client reader/writer threads.

Pure stdlib, no ``Live`` import.

Phase-2 shape: each client owns an **outbox queue** drained by a dedicated
writer thread. Responses AND events go through it, which gives two guarantees:

- a single writer per socket (no interleaved frames, no write lock), and
- ``Client.send`` never blocks — so Live's main thread (which fires observer
  events) can enqueue and return instantly. A slow/stuck consumer fills its
  outbox and gets disconnected rather than stalling Live.
"""
from __future__ import annotations

import itertools
import queue
import socket
import threading

from . import framing

OUTBOX_LIMIT = 1000  # frames buffered per client before we drop the client
_CLOSE = object()    # writer-thread sentinel


class Client:
    """One connected peer: socket + non-blocking outbox + writer thread."""

    def __init__(self, sock: socket.socket, client_id: int, log):
        self._sock = sock
        self.id = client_id
        self._log = log
        self._outbox: queue.Queue = queue.Queue(maxsize=OUTBOX_LIMIT)
        self._writer = threading.Thread(
            target=self._drain, name=f"bridge-writer-{client_id}", daemon=True
        )
        self._writer.start()

    def send(self, msg: dict) -> bool:
        """Enqueue a frame. NEVER blocks. False (and disconnect) when full."""
        try:
            self._outbox.put_nowait(msg)
            return True
        except queue.Full:
            self._log(f"client {self.id}: outbox full ({OUTBOX_LIMIT}) — dropping client")
            self.close()
            return False

    def close(self):
        try:
            self._outbox.put_nowait(_CLOSE)
        except queue.Full:
            pass  # writer will die on the closed socket instead
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def _drain(self):
        while True:
            msg = self._outbox.get()
            if msg is _CLOSE:
                return
            try:
                framing.write_frame(self._sock, msg)
            except (OSError, framing.ProtocolError):
                return  # socket gone; reader loop will notice and clean up


class SocketServer:
    def __init__(self, handle_request, host: str = "127.0.0.1", port: int = 8766,
                 log=None, on_disconnect=None):
        # handle_request: (request_dict, client) -> response_dict  (must not raise)
        # on_disconnect:  (client) -> None — fired once when a client goes away
        self._handle_request = handle_request
        self._on_disconnect = on_disconnect or (lambda client: None)
        self._host = host
        self._port = port
        self._log = log or (lambda *a: None)
        self._sock: socket.socket | None = None
        self._running = False
        self._client_ids = itertools.count(1)

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self._host, self._port))
        sock.listen(8)
        self._sock = sock
        self._running = True
        threading.Thread(target=self._accept_loop, name="bridge-accept", daemon=True).start()
        self._log(f"listening on {self._host}:{self._port}")

    @property
    def port(self) -> int:
        return self._sock.getsockname()[1] if self._sock else self._port

    def stop(self):
        self._running = False
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _accept_loop(self):
        while self._running:
            try:
                raw, _addr = self._sock.accept()
            except OSError:
                break  # socket closed -> shutting down
            threading.Thread(
                target=self._serve_client, args=(raw,), name="bridge-client", daemon=True
            ).start()

    def _serve_client(self, raw: socket.socket):
        try:
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        client = Client(raw, next(self._client_ids), self._log)
        try:
            while self._running:
                request = framing.read_frame(raw)
                if request is None:
                    break  # clean close
                client.send(self._handle_request(request, client))
        except (framing.ProtocolError, OSError) as exc:
            self._log(f"client {client.id} dropped: {exc}")
        finally:
            try:
                self._on_disconnect(client)
            except Exception as exc:  # teardown must never kill the server
                self._log(f"on_disconnect failed for client {client.id}: {exc}")
            client.close()
