"""JSONL trajectory store: one file per run, append-only, readable mid-run.

Each append writes one complete line in a single write call and flushes, so
a crash or a concurrent reader (`tail -f`, a live dashboard) never sees a
torn record -- the file is valid JSONL after every append. Records go down
in the order they happen: decisions as they commit, each floor's packet at
its boundary, the run record last; a missing run record therefore marks a
run that died mid-flight, and its floors and decisions remain usable.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterator

from .schema import TrajectoryRecord, parse_record


class TrajectoryStore:
    """Append-only writer for one run's records."""

    def __init__(self, directory: Path, run_id: str):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{run_id}.jsonl"
        self._fh = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def append(self, record: TrajectoryRecord) -> None:
        line = record.model_dump_json() + "\n"
        with self._lock:
            self._fh.write(line)
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            self._fh.close()

    def __enter__(self) -> "TrajectoryStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def read_records(path: Path) -> Iterator[TrajectoryRecord]:
    """Replay a trajectory file as typed records, in written order.

    Strict on purpose: a line that does not parse raises rather than being
    skipped -- a trajectory with silently missing records would poison every
    downstream consumer (metrics, exports, replay) without a trace.
    """
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield parse_record(line)
