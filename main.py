"""Step-one entry point: let spirecomm's built-in SimpleAgent play via CommunicationMod.

CommunicationMod (a ModTheSpire mod) launches this script and talks to it over
stdin/stdout with newline-delimited JSON. The only job here is to prove the
integration works end to end: the harness signals `ready`, receives game state,
and a baseline agent makes legal decisions through a full run.

Wire it up by pointing CommunicationMod's SpireConfig `command` at the project's
venv interpreter and this script:

    command=/Users/berto/work/sts-bench/.venv/bin/python /Users/berto/work/sts-bench/main.py

To watch what's happening, every protocol line is mirrored to logs/latest.log.
In a terminal:

    tail -f /Users/berto/work/sts-bench/logs/latest.log

Lines tagged `<< IN ` are game-state JSON from the mod; `>> OUT` are commands we
send back. stdout itself stays clean for the protocol -- never print() to it.

Once a real game drives a combat to completion, step one is done and the
SimpleAgent gets replaced by our own Env + LLM agent layers.
"""

import datetime
import itertools
import sys
import threading
from pathlib import Path

from spirecomm.communication.coordinator import Coordinator
from spirecomm.ai.agent import SimpleAgent
from spirecomm.spire.character import PlayerClass

LOG_PATH = Path(__file__).resolve().parent / "logs" / "latest.log"


class _ProtocolLog:
    """Append protocol lines to the logfile with a direction tag and timestamp."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("w", encoding="utf-8")
        self._lock = threading.Lock()

    def line(self, tag: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        stamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with self._lock:
            self._fh.write(f"{stamp} {tag} {text}\n")
            self._fh.flush()


class _TeeReader:
    """Wrap stdin's read(); mirror each completed input line to the log."""

    def __init__(self, stream, log: _ProtocolLog):
        self._stream = stream
        self._log = log
        self._buf = ""

    def read(self, size=-1):
        data = self._stream.read(size)
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._log.line("<< IN ", line)
        return data

    def __getattr__(self, name):
        return getattr(self._stream, name)


class _TeeWriter:
    """Wrap stdout's write(); mirror each completed output line to the log."""

    def __init__(self, stream, log: _ProtocolLog):
        self._stream = stream
        self._log = log
        self._buf = ""

    def write(self, data):
        n = self._stream.write(data)
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._log.line(">> OUT", line)
        return n

    def __getattr__(self, name):
        return getattr(self._stream, name)


def main() -> None:
    log = _ProtocolLog(LOG_PATH)
    sys.stdin = _TeeReader(sys.stdin, log)
    sys.stdout = _TeeWriter(sys.stdout, log)
    log.line("--", "session start")

    agent = SimpleAgent()
    coordinator = Coordinator()
    coordinator.signal_ready()
    coordinator.register_command_error_callback(agent.handle_error)
    coordinator.register_state_change_callback(agent.get_next_action_in_game)
    coordinator.register_out_of_game_callback(agent.get_next_action_out_of_game)

    # Play games forever, cycling through the playable classes.
    for chosen_class in itertools.cycle(PlayerClass):
        agent.change_class(chosen_class)
        coordinator.play_one_game(chosen_class)


if __name__ == "__main__":
    main()
