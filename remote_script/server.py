"""Socket server: a background accept loop + one handler thread per client.

Pure stdlib, no ``Live`` import. It reads length-framed requests, hands each to a
``handle_request`` callback, and writes the framed response. The callback is
where the caller marshals onto Live's main thread — the server itself knows
nothing about Live.
"""
from __future__ import annotations

import socket
import threading

from . import framing


class SocketServer:
    def __init__(self, handle_request, host: str = "127.0.0.1", port: int = 8766, log=None):
        # handle_request: (request_dict) -> response_dict  (must not raise)
        self._handle_request = handle_request
        self._host = host
        self._port = port
        self._log = log or (lambda *a: None)
        self._sock: socket.socket | None = None
        self._running = False

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
        # the bound port (useful when port=0 was requested for tests)
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
                client, _addr = self._sock.accept()
            except OSError:
                break  # socket closed -> shutting down
            threading.Thread(
                target=self._handle_client, args=(client,), name="bridge-client", daemon=True
            ).start()

    def _handle_client(self, client: socket.socket):
        with client:
            try:
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            try:
                while self._running:
                    request = framing.read_frame(client)
                    if request is None:
                        break  # clean close
                    framing.write_frame(client, self._handle_request(request))
            except (framing.ProtocolError, OSError) as exc:
                self._log(f"client dropped: {exc}")
