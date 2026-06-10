"""Byte-bridge launched by CommunicationMod.

CommunicationMod spawns this process and talks to it over stdin/stdout. All this
does is forward those bytes, unmodified, to a TCP socket where the interactive
brain (console.py) is listening -- so the real driver can run in your terminal.

    mod ──stdin──> relay ──socket──> brain        (game state)
    mod <─stdout── relay <─socket── brain        (commands)

Start console.py FIRST, then trigger CommunicationMod's external process in-game.
If the brain isn't listening yet, the relay retries briefly, then gives up.
"""

import socket
import sys
import threading
import time

HOST, PORT = "127.0.0.1", 9999


def connect():
    last = None
    for _ in range(25):  # ~5s of retries before CommunicationMod's ready timeout
        try:
            return socket.create_connection((HOST, PORT))
        except OSError as exc:
            last = exc
            time.sleep(0.2)
    raise SystemExit(f"relay: could not reach brain at {HOST}:{PORT}: {last}")


def main():
    sock = connect()
    mod_in = sys.stdin.buffer
    mod_out = sys.stdout.buffer

    def mod_to_brain():
        try:
            while True:
                line = mod_in.readline()
                if not line:
                    break
                sock.sendall(line)
        finally:
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    threading.Thread(target=mod_to_brain, daemon=True).start()

    # brain -> mod: forward each line and flush so the mod sees it immediately.
    for line in sock.makefile("rb"):
        mod_out.write(line)
        mod_out.flush()


if __name__ == "__main__":
    main()
