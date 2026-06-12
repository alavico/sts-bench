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


def render_html(data: dict[str, Any]) -> str:
    template = (_ASSETS / "template.html").read_text()
    title = data.get("run", {}).get("run_id") or "run"
    # The data payload is substituted last so nothing inside it is ever
    # subject to the other replacements.
    return (
        template.replace("/*__CSS__*/", (_ASSETS / "report.css").read_text())
        .replace("//__JS__", (_ASSETS / "report.js").read_text())
        .replace("__TITLE__", html.escape(title))
        .replace("__DATA__", _embed(data))
    )
