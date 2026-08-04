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

from .providers.base import Usage


class ProtocolLog:
    def __init__(self, directory: Path, name: str = "session"):
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        # The stamp resolves to a second; runs starting inside the same one
        # (queued jobs over a fast game, parallel instances) must not share a
        # run_id, so creation is exclusive and collisions take a counter.
        serial = 1
        while True:
            suffix = f"-{serial}" if serial > 1 else ""
            self.path = directory / f"{name}-{stamp}{suffix}.log"
            try:
                self._fh = self.path.open("x", encoding="utf-8")
                break
            except FileExistsError:
                serial += 1
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

    def close(self) -> None:
        """Sessions that open one log per run must release each handle."""
        with self._lock:
            self._fh.close()


def model_traffic(transcript: list[dict]) -> list[tuple[str, str]]:
    """(tag, text) pairs for the model channel of one decision's new messages.

    The input is the decision's transcript delta, never a full persistent
    conversation -- each message is logged exactly once, by the decision that
    introduced it.

    Arrow direction matches the wire tags (right = sent, left = received);
    the letter names the peer: `>m` is what we sent the model (state view,
    nudges, tool results), `<m` is what it sent back (text, tool calls). The
    system prompt is skipped here -- it is constant, so the runner logs it
    once per session.
    """
    pairs = []
    for msg in transcript:
        role = msg.get("role")
        if role == "user":
            pairs.append((">m", msg.get("content") or ""))
        elif role == "tool":
            pairs.append((">m", f"[{msg.get('tool_call_id')}] {msg.get('content')}"))
        elif role == "assistant":
            for key in ("reasoning_content", "reasoning"):
                if isinstance(msg.get(key), str) and msg[key]:
                    pairs.append(("<m", f"(reasoning) {msg[key]}"))
                    break
            if msg.get("content"):
                pairs.append(("<m", msg["content"]))
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                pairs.append(("<m", f"call {fn.get('name')} {fn.get('arguments')}"))
    return pairs


def reasoning_note(usage: Usage) -> str:
    """' (of which N reasoning)' when the backend reports hidden thinking."""
    return f" (of which {usage.reasoning_tokens} reasoning)" if usage.reasoning_tokens else ""
