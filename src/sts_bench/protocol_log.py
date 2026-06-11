"""Timestamped protocol logfile, shared by smoke runner and future runners.

Every line that crosses the socket lands here tagged with its direction
(`>>` sent to the game, `<<` received), so a hung or misbehaving run can
always be reconstructed with `tail -f`. Runners may interleave further
channels: `--` narrative (decisions in readable form, floor/combat
landmarks) and `>m`/`<m` model traffic (what the LLM was sent / sent back;
arrow direction means sent/received as on the wire, the letter names the peer).
Wire lines stay verbatim, so filtering by tag still yields clean
fixture-harvesting material. Each session gets its own file (never overwrite
old ones); `latest.log` is a symlink to the newest for easy tailing.
"""

from __future__ import annotations

import datetime
import threading
from pathlib import Path


class ProtocolLog:
    def __init__(self, directory: Path, name: str = "session"):
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = directory / f"{name}-{stamp}.log"
        self._fh = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

        latest = directory / "latest.log"
        latest.unlink(missing_ok=True)
        latest.symlink_to(self.path.name)

    def line(self, tag: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        stamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with self._lock:
            self._fh.write(f"{stamp} {tag} {text}\n")
            self._fh.flush()
