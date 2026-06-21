"""Perception edge: the optional, advisory LLM layer that turns free-text citizen reports
into typed facts -- under a hard governance guarantee that it can never move a
safety-relevant value (see :mod:`bayanihan_net.perception.llm_extract`)."""

from __future__ import annotations

from .llm_extract import (
    ExtractedReport,
    ExtractionOutcome,
    evaluate_extraction,
    extract_report,
    provenance_block,
)
from .report_text import render_report_text

__all__ = [
    "ExtractedReport",
    "ExtractionOutcome",
    "evaluate_extraction",
    "extract_report",
    "provenance_block",
    "render_report_text",
]
