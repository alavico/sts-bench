"""Blocking Env over the CommunicationMod protocol.

Protocol recap: after this side says `ready`, the mod sends
a state JSON whenever the game is stable. We send one command; the reply is
either the next stable state or `{"error": ..., "ready_for_command": true}`
with the game unchanged. States can also arrive unprompted (animations
finishing, monster turns resolving), so a decision point is the *latest* ready
state, not the first line we happen to read.

spirecomm's Coordinator solves the same problem with reader threads and
callbacks; this is the same protocol knowledge restructured as a blocking
call, which is what an agent loop (and later an RL loop) wants. spirecomm
remains the reference for message-field semantics only.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

from .base import EnvError, RawState, StepResult
from .connection import GameConnection

# Long ceiling: between END and the next player turn the game can spend many
# seconds on monster turns, transitions, or the player closing a popup by hand.
DEFAULT_STEP_TIMEOUT = 60.0
# After a ready state arrives, briefly drain follow-up messages so we act on
# the latest stable state rather than an intermediate one.
SETTLE_SECONDS = 0.1
# A combat's first snapshot can land before monster intents finish rolling
# (they arrive as the game's DEBUG placeholder, observed on turn 1 of every
# live combat). One bounded re-probe usually fetches the revealed intent.
INTENT_PROBE_WINDOW = 1.0


class CommunicationModEnv:
    """One connected game instance, exposed as reset/step/legal_actions."""

    def __init__(
        self,
        conn: GameConnection,
        step_timeout: float = DEFAULT_STEP_TIMEOUT,
        on_protocol_line: Callable[[str, str], None] | None = None,
    ):
        self._conn = conn
        self._step_timeout = step_timeout
        self._log = on_protocol_line or (lambda direction, line: None)
        self._state: RawState | None = None

    # -- lifecycle -----------------------------------------------------------

    def handshake(self) -> RawState:
        """Send `ready` and block until the mod reports the first stable state."""
        self._send("ready")
        self._state = self._await_ready_state(self._step_timeout)
        return self._state

    def reset(self, character: str = "IRONCLAD", ascension: int = 0, seed: str | None = None) -> RawState:
        """Start a new run. Only valid out of game (main menu / after a run ends).

        CommunicationMod has no abandon command, so a run in progress must be
        played out or the game restarted; we surface that instead of guessing.
        """
        state = self._require_state()
        if state.get("in_game"):
            raise EnvError("reset() called mid-run: CommunicationMod cannot abandon a run; finish it or restart the game")
        command = f"start {character} {ascension}"
        if seed is not None:
            command += f" {seed}"
        result = self.step(command)
        if not result.ok:
            raise EnvError(f"start rejected: {result.error}")
        return result.state

    # -- core loop -----------------------------------------------------------

    def step(self, command: str) -> StepResult:
        """Send one command; block until the next decision point or a rejection.

        A step timeout is a result, not a crash: the game can absorb a click
        without ever becoming ready again (observed live: taking a potion
        reward onto a full belt), and the caller is the one who can try a
        different command or end the run gracefully. If the wait saw any
        state at all -- the mid-timeout `state` nudge answers even when the
        mod never flips ready_for_command (observed live: `cancel` on an
        event grid deselects the card and triggers no new dump) -- that
        freshest state becomes current, so the caller's recovery acts on
        what the game looks like now, never a snapshot from before the click.
        """
        self._require_state()
        self._send(command)
        try:
            self._state = self._await_ready_state(self._step_timeout)
        except ProtocolErrorMessage as err:
            # Game unchanged; keep the previous state as current.
            return StepResult(state=self._require_state(), error=err.message)
        except StepTimeout as err:
            if err.last_state is not None:
                self._state = err.last_state
            return StepResult(state=self._require_state(), error=str(err))
        return StepResult(state=self._state)

    def legal_actions(self) -> list[str]:
        return list(self._require_state().get("available_commands", []))

    @property
    def state(self) -> RawState:
        return self._require_state()

    # -- internals -----------------------------------------------------------

    def _send(self, command: str) -> None:
        self._log(">>", command)
        self._conn.send_line(command)

    def _await_ready_state(self, timeout: float, probe_rolling_intent: bool = True) -> RawState:
        """Wait for a state with ready_for_command, then drain stragglers.

        Halfway through the timeout we nudge with `state` once -- it is safe at
        any time and recovers from a missed or dropped message. A ready state
        whose monsters still show the rolling-intent placeholder gets one
        bounded re-probe; if the game still hasn't rolled, the placeholder
        state is returned and the serializer renders it honestly.
        """
        deadline = time.monotonic() + timeout
        nudged = False
        last_seen: RawState | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StepTimeout(timeout, last_seen)
            line = self._conn.poll_line(remaining if nudged else remaining / 2)
            if line is None:
                if nudged:
                    raise StepTimeout(timeout, last_seen)
                self._send("state")
                nudged = True
                continue
            message = self._parse(line)
            if "error" in message:
                raise ProtocolErrorMessage(str(message["error"]))
            if "available_commands" in message:
                # Not ready, but real: the nudge's answer reflects the game
                # as it stands, which beats a stale snapshot if we time out.
                last_seen = message
            if message.get("ready_for_command"):
                latest = self._drain(message)
                if probe_rolling_intent and _intent_rolling(latest):
                    self._send("state")
                    try:
                        return self._await_ready_state(
                            min(INTENT_PROBE_WINDOW, max(deadline - time.monotonic(), 0.1)),
                            probe_rolling_intent=False,
                        )
                    except TimeoutError:
                        return latest
                return latest

    def _drain(self, latest: RawState) -> RawState:
        while True:
            line = self._conn.poll_line(SETTLE_SECONDS)
            if line is None:
                return latest
            message = self._parse(line)
            if "error" in message:
                raise ProtocolErrorMessage(str(message["error"]))
            if message.get("ready_for_command"):
                latest = message

    def _parse(self, line: str) -> RawState:
        self._log("<<", line)
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EnvError(f"unparseable message from mod: {line[:200]!r}") from exc
        if not isinstance(message, dict):
            raise EnvError(f"expected a JSON object from mod, got: {line[:200]!r}")
        return message

    def _require_state(self) -> RawState:
        if self._state is None:
            raise EnvError("no state yet: call handshake() first")
        return self._state


def _intent_rolling(state: RawState) -> bool:
    """True when any live monster still shows the DEBUG intent placeholder."""
    combat = (state.get("game_state") or {}).get("combat_state") or {}
    return any(
        m.get("intent") == "DEBUG" and not m.get("is_gone")
        for m in combat.get("monsters", [])
    )


class ProtocolErrorMessage(Exception):
    """Internal: the mod rejected a command. Converted to StepResult.error."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class StepTimeout(TimeoutError):
    """No ready state arrived in time. Carries the freshest state the wait
    saw (a nudge's not-ready answer), so recovery can act on the present."""

    def __init__(self, timeout: float, last_state: RawState | None):
        super().__init__(
            f"no ready state within {timeout}s (last command may still be animating)"
        )
        self.last_state = last_state
