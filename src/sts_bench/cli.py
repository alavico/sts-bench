"""Shared CLI plumbing for the entry-point modules (play, queue, bench, smoke).

Tiny on purpose: the argument definitions and `.env` loading that were
otherwise copy-pasted across the entry points live here once.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Callable

from dotenv import dotenv_values, load_dotenv

DEFAULT_ENV_FILE = ".env"


def add_character_ascension_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--character", default="ironclad")
    parser.add_argument("--ascension", type=int, default=0)


def add_env_file_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--env-file",
        default=DEFAULT_ENV_FILE,
        help="env file with keys/config (real env vars win)",
    )


def apply_env_file(env_file: str, say: Callable[[str], None]) -> bool:
    """Load `env_file` into the environment (shell vars win); report names applied.

    Returns False when a non-default file is explicitly named but missing (the
    caller should abort); a missing default `.env` is fine and returns True.
    """
    path = Path(env_file)
    if path.exists():
        # Report names (never values) of what the file contributes; shell env wins.
        applied = [key for key in dotenv_values(path) if key not in os.environ]
        load_dotenv(path)
        if applied:
            say(f"loaded {', '.join(applied)} from {path}")
        return True
    if env_file != DEFAULT_ENV_FILE:
        say(f"env file {path} not found")
        return False
    return True
