"""Socket plumbing between the harness and CommunicationMod's relay.

The harness is the long-lived side: it listens, the mod-launched relay
connects, and from then on the CommunicationMod protocol (newline-delimited
JSON in, newline-delimited commands out) travels over the socket. One
connected relay equals one game instance, so the async runner later just
accepts more connections from the same server.

This layer knows about bytes, lines, and timeouts -- nothing about game
state. Protocol semantics live in `communication_mod.py`.
"""

from __future__ import annotations

import socket
import time

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9999


class ConnectionClosed(Exception):
    """The relay (and therefore the game process) went away."""


class GameConnection:
    """Line-oriented, blocking channel to one game instance.

    Buffers lines manually over raw recv() -- socket.makefile() objects break
    permanently after a read timeout, and timeouts are normal here (polling
    for follow-up states).
    """

    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._buf = bytearray()
        self._peer = _peer_name(sock)

    @property
    def peer(self) -> str:
        return self._peer

    def send_line(self, text: str) -> None:
        try:
            self._sock.sendall(text.encode("utf-8") + b"\n")
        except OSError as exc:
            raise ConnectionClosed(f"send to {self._peer} failed: {exc}") from exc

    def recv_line(self, timeout: float | None = None) -> str:
        """Block until a full line arrives. Raises TimeoutError or ConnectionClosed."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while b"\n" not in self._buf:
            if deadline is None:
                self._sock.settimeout(None)
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no line from {self._peer} within {timeout}s")
                self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(65536)
            except TimeoutError:
                raise TimeoutError(f"no line from {self._peer} within {timeout}s") from None
            except OSError as exc:
                raise ConnectionClosed(f"read from {self._peer} failed: {exc}") from exc
            if not chunk:
                raise ConnectionClosed(f"{self._peer} closed the connection")
            self._buf += chunk
        raw, _, rest = bytes(self._buf).partition(b"\n")
        self._buf = bytearray(rest)
        return raw.decode("utf-8").rstrip("\r")

    def poll_line(self, timeout: float) -> str | None:
        """Like recv_line, but returns None on timeout instead of raising."""
        try:
            return self.recv_line(timeout)
        except TimeoutError:
            return None

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class HarnessServer:
    """Listens for relay connections. Context manager closes the listener."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((host, port))
        self._srv.listen()
        self.host, self.port = self._srv.getsockname()

    def accept(self, timeout: float | None = None) -> GameConnection:
        self._srv.settimeout(timeout)
        try:
            sock, _ = self._srv.accept()
        except TimeoutError:
            raise TimeoutError(f"no relay connected within {timeout}s") from None
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return GameConnection(sock)

    def close(self) -> None:
        self._srv.close()

    def __enter__(self) -> "HarnessServer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _peer_name(sock: socket.socket) -> str:
    try:
        host, port = sock.getpeername()[:2]
        return f"{host}:{port}"
    except OSError:
        return "<unknown peer>"
