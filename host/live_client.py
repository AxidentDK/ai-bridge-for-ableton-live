"""Minimal client for the ableton-live-mcp bridge (TCP JSON-lines on 127.0.0.1:8765)."""
import json
import socket
import sys


def rpc(method, params=None, timeout=15.0):
    with socket.create_connection(("127.0.0.1", 8765), timeout) as s:
        s.settimeout(timeout)
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    msg = json.loads(buf.decode("utf-8"))
    if "error" in msg:
        raise RuntimeError(msg["error"])
    return msg.get("result")


if __name__ == "__main__":
    method = sys.argv[1] if len(sys.argv) > 1 else "ping"
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    print(json.dumps(rpc(method, params), indent=2)[:12000])
