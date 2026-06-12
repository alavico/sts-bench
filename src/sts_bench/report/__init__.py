"""One run's trajectory file rendered as a self-contained HTML report."""

from .model import build_report_data
from .page import render_html

__all__ = ["build_report_data", "render_html"]
