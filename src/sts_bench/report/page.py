"""Emit the report as one self-contained HTML file.

Everything is inlined -- styles, script, and the run data as embedded JSON --
so the file opens offline, travels as an attachment, and archives next to the
trajectory it was built from. The page script is a pure renderer; all data
shaping happens before embedding.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

_ASSETS = Path(__file__).parent / "assets"


def _embed(data: dict[str, Any]) -> str:
    """JSON safe to sit inside a <script> tag: nothing in the payload may
    close the tag or open an HTML comment, whatever the conversations hold."""
    payload = json.dumps(data, separators=(",", ":"))
    return (
        payload.replace("</", "<\\/")
        .replace("<!--", "<\\u0021--")
        .replace("<script", "<\\u0073cript")
    )


def render_html(
    data: dict[str, Any],
    *,
    template: str = "template.html",
    css: tuple[str, ...] = ("report.css",),
    js: tuple[str, ...] = ("report.js",),
    title: str | None = None,
) -> str:
    page = (_ASSETS / template).read_text()
    # A run's tab is named by what a reader knows it as -- model and seed --
    # never the internal run id (which only survives as a last resort).
    run = data.get("run") or {}
    named = " · ".join(s for s in (run.get("model"), run.get("seed")) if s)
    title = str(title or named or run.get("run_id") or "run")
    # The data payload is substituted last so nothing inside it is ever
    # subject to the other replacements.
    return (
        page.replace("/*__CSS__*/", "\n".join((_ASSETS / name).read_text() for name in css))
        .replace("//__JS__", "\n".join((_ASSETS / name).read_text() for name in js))
        .replace("__TITLE__", html.escape(title))
        .replace("__DATA__", _embed(data))
    )
