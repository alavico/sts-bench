"""Shared fake-mod fixture: a scripted CommunicationMod on the far end of a real socket.

TCP buffers both directions, so tests stay single-threaded: queue the mod's
reply with send_json() *before* calling the env method that blocks on it.
"""

from __future__ import annotations

import json
import socket

import pytest

from sts_bench.env import GameConnection, HarnessServer


class FakeMod:
    """The relay/game side of the protocol, driven explicitly by tests."""

    def __init__(self, server: HarnessServer):
        self.sock = socket.create_connection((server.host, server.port))
        self._rfile = self.sock.makefile("rb")

    def send_json(self, obj: dict) -> None:
        self.sock.sendall(json.dumps(obj).encode("utf-8") + b"\n")

    def send_state(self, **overrides) -> dict:
        state = {
            "available_commands": ["play", "end", "state"],
            "ready_for_command": True,
            "in_game": True,
            "game_state": {"floor": 1, "act": 1, "screen_type": "NONE"},
        }
        state.update(overrides)
        self.send_json(state)
        return state

    def expect_line(self, timeout: float = 2.0) -> str:
        self.sock.settimeout(timeout)
        raw = self._rfile.readline()
        assert raw, "harness closed the connection unexpectedly"
        return raw.decode("utf-8").rstrip("\n")

    def close(self) -> None:
        # The makefile() reader holds a reference to the socket; close it too
        # or the peer never sees FIN.
        self._rfile.close()
        self.sock.close()


@pytest.fixture
def link():
    """(harness GameConnection, FakeMod) pair over a real localhost socket."""
    with HarnessServer(port=0) as server:
        mod = FakeMod(server)
        conn = server.accept(timeout=2)
        yield conn, mod
        mod.close()
        conn.close()
