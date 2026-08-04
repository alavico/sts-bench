"""Live-preview server for the campaign report: edit the assets, watch it reload.

Build the data payload once from trajectory files, then serve the report over
localhost. Every page load re-renders from the asset files on disk, so editing
`campaign.js`/`campaign.css`/`campaign.html` and refreshing shows the change with
no rebuild and no trajectory re-read. A small injected poller watches the assets'
modification times and reloads the page for you the moment they change.

Run links work here too: each run's page points at /runs/<run_id>.html, which
renders that trajectory's single-run report on demand from the records already
in memory.

    uv run python -m sts_bench.report.dev logs/2026-06-12/trajectories/*.jsonl

This is a development entry point: it never writes a file and shares nothing with
the production `bench` path beyond the render functions. A broken edit surfaces in
the browser console (the server only inlines the assets, never parses them), so
the loop survives your mistakes -- fix the file and it reloads. Changes to the
Python payload (`campaign.py`, `metrics.py`) need a server restart, not a refresh.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from urllib.parse import unquote

from ..runner.reports import Pricing
from ..trajectory import read_records
from .campaign import CampaignRun, build_campaign_data, render_campaign_html
from .model import build_report_data
from .page import render_html

_ASSETS = Path(__file__).parent / "assets"

# A page load depends on these; the poller reloads when any of them changes.
_WATCH = ("campaign.html", "campaign.js", "campaign.css", "report.css", "template.html")

# Injected before </body>: poll /rev, reload when the asset fingerprint moves.
_RELOAD = """
<script>
(function () {
  let rev = null;
  setInterval(async () => {
    try {
      const v = await (await fetch("/rev")).text();
      if (rev === null) rev = v;
      else if (v !== rev) location.reload();
    } catch (e) {}
  }, 400);
})();
</script>
"""


def _fingerprint() -> str:
    """A string that moves whenever a watched asset is saved."""
    return ";".join(
        f"{name}:{(_ASSETS / name).stat().st_mtime_ns}"
        for name in _WATCH
        if (_ASSETS / name).exists()
    )


def _load_pricing(arg: str | None) -> Pricing | None:
    if not arg:
        return None
    return {
        model: (rates[0], rates[1])
        for model, rates in json.loads(Path(arg).read_text()).items()
    }


def serve(
    data: dict, records_by_run: dict[str, list], pricing: Pricing | None, host: str, port: int
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server's casing)
            if self.path == "/rev":
                return self._send(_fingerprint(), "text/plain")
            if self.path in ("/", "/index.html"):
                page = render_campaign_html(data).replace("</body>", _RELOAD + "</body>")
                return self._send(page, "text/html")
            if self.path.startswith("/runs/") and self.path.endswith(".html"):
                run_id = unquote(self.path[len("/runs/") : -len(".html")])
                records = records_by_run.get(run_id)
                if records is not None:
                    page = render_html(build_report_data(records, pricing, campaign="../"))
                    return self._send(page, "text/html")
            self.send_error(404)

        def _send(self, body: str, mime: str) -> None:
            payload = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", f"{mime}; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):  # the /rev poll would drown out anything useful
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"[dev] campaign report on http://{host}:{port}  (edit assets, the page auto-reloads)")
    print(f"[dev] watching {', '.join(_WATCH)} · Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dev] stopped")
        server.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "trajectories", nargs="+", metavar="JSONL",
        help="trajectory files to load (globs are shell-expanded)",
    )
    parser.add_argument("--pricing", default=None, help="JSON file: model -> [input, output] $ per Mtok")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    paths = [Path(p) for p in args.trajectories]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        print(f"[dev] no such trajectory: {', '.join(missing)}", file=sys.stderr)
        raise SystemExit(1)

    pricing = _load_pricing(args.pricing)
    runs, records_by_run = [], {}
    for path in paths:
        records = list(read_records(path))
        run = CampaignRun.from_records(records)
        records_by_run[run.metrics.run_id] = records
        run.page = f"runs/{run.metrics.run_id}.html"
        runs.append(run)
    data = build_campaign_data(runs, pricing=pricing)
    serve(data, records_by_run, pricing, args.host, args.port)


if __name__ == "__main__":
    main()
