"""The optional advisory LLM perception layer (the "Tier B enrichment" the ``.env.example``
anticipates) -- the one place an LLM touches bayanihan-net, deliberately bounded so it can
**never move a safety-relevant value**.

How it is bounded:

* It runs only at the *perception edge*: it turns a free-text citizen report
  (:func:`bayanihan_net.perception.report_text.render_report_text`) back into the typed facts
  a citizen states -- how many people, and what kind of emergency.
* Its output is re-validated by a **pure output guardrail** against the deterministic ground
  truth (:func:`evaluate_extraction`): the extracted ``people`` and ``itype`` must match the
  structured report exactly. A passing extraction therefore *equals* the truth (the run is
  unchanged) and a failing one is *discarded* in favour of the deterministic facts (the run is
  unchanged). The LLM is **observed and verified, never trusted** -- a hallucinated headcount
  is caught, logged, and dropped. Severity/priority are always computed downstream by code,
  never supplied by the model.
* It is **opt-in** (``EnvParams.llm_advisory``, default ``False``) and **offline by default**
  (no API key -> deterministic passthrough). With the toggle off the default pipeline imports
  no LLM library and is byte-identical with or without this module present.

This mirrors the project's RL stance (``SAFETY_GOVERNANCE`` Section 5: *"RL informs; code and
the human decide"*) and reuses Assignment 1's discipline -- a typed Agents-SDK agent + an
output guardrail that re-validates against fixtures + a deterministic fallback + tracing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..config import IncidentType
from ..scenario import RawReport
from .report_text import render_report_text

_DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
_DEFAULT_OLLAMA_MODEL = "llama3.1"

_EXTRACT_INSTRUCTIONS = (
    "You are a disaster-hotline intake assistant. Read one citizen report (free text) and "
    "extract ONLY the facts the caller states: the number of people affected, and the kind of "
    "emergency (rescue, medical, or relief). Do not invent or estimate; report exactly what the "
    "text says. Do not assess severity -- that is computed elsewhere."
)
_EXTRACT_PROMPT = "Citizen report:\n{text}\n\nExtract the headcount and the incident type."


class ExtractedReport(BaseModel):
    """The facts the advisory LLM recovers from a free-text report -- a strict contract."""

    model_config = ConfigDict(extra="forbid")

    people: int
    itype: IncidentType


@dataclass(frozen=True)
class ExtractionOutcome:
    """One advisory extraction: the facts the caller should use plus an audit record.

    ``people``/``itype`` are the LLM's values when they passed the guardrail (== ground truth),
    otherwise the deterministic fallback (also ground truth) -- so the safety-relevant output is
    identical either way. ``audit`` is appended to the blackboard event log so the run stays
    traceable (``used_llm``, ``ok``, model, any guardrail ``violations``).
    """

    people: int
    itype: IncidentType
    audit: dict[str, Any]


# Pure guardrail core (no SDK; unit-testable with no API key)
def evaluate_extraction(extracted: ExtractedReport, truth: RawReport) -> list[str]:
    """Return the list of guardrail violations (empty == the extraction matches ground truth).

    The only facts a citizen states -- and the only ones the LLM may supply -- are the
    headcount and the incident type; both must match the structured report exactly. This is
    the "critic with independent evidence" pattern: truth comes from the fixture, not the model.
    """
    violations: list[str] = []
    if extracted.people != truth.people:
        violations.append(f"people mismatch: extracted {extracted.people}, truth {truth.people}")
    if extracted.itype is not truth.itype:
        violations.append(
            f"itype mismatch: extracted {extracted.itype.value}, truth {truth.itype.value}"
        )
    return violations


def extract_report(report: RawReport, *, trace_id: str) -> ExtractionOutcome:
    """Advisory perception: render the report to text, extract it back, verify, or fall back.

    Returns the verified facts (always == ground truth) plus an audit record. The caller invokes
    this only when ``EnvParams.llm_advisory`` is set; when enabled but offline (no provider/key)
    it returns a deterministic passthrough, so a graded run still needs no network.
    """
    base_audit = {"node": report.node, "report_id": report.report_id}
    _load_dotenv_once()
    mode, model = _provider()
    if mode == "offline":
        return _fallback(report, reason="offline_no_provider", **base_audit)
    try:
        extracted = _run_extraction(report, mode=mode, model=model)
    except Exception as exc:  # network / SDK / parse failure -> safe fallback, never crash a run
        return _fallback(report, reason=f"error:{type(exc).__name__}", model=model, **base_audit)
    violations = evaluate_extraction(extracted, report)
    if violations:
        # The guardrail tripped: the model contradicted ground truth. Discard it, fall back to
        # the deterministic facts, and record the catch as evidence (the A1/A2 "hallucination /
        # reward-hacking caught" lesson, applied here).
        return ExtractionOutcome(
            people=report.people,
            itype=report.itype,
            audit={
                "used_llm": True,
                "ok": False,
                "reason": "guardrail_tripwire",
                "violations": violations,
                "model": model,
                "trace": trace_id,
                **base_audit,
            },
        )
    return ExtractionOutcome(
        people=extracted.people,
        itype=extracted.itype,
        audit={"used_llm": True, "ok": True, "model": model, "trace": trace_id, **base_audit},
    )


def _fallback(truth: RawReport, *, reason: str, **extra: Any) -> ExtractionOutcome:
    """Use the deterministic facts and record why the LLM output was not used."""
    return ExtractionOutcome(
        people=truth.people,
        itype=truth.itype,
        audit={"used_llm": False, "ok": True, "reason": reason, **extra},
    )


def _provider() -> tuple[str, str | None]:
    """Resolve the runtime from the environment: ``("openai"|"ollama"|"offline", model)``.

    Mirrors Assignment 1's ``model_provider``: a real ``OPENAI_API_KEY`` selects cloud OpenAI;
    otherwise an ``OLLAMA_BASE_URL`` selects a local server; otherwise offline (the default).
    """
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key and not key.lower().startswith("sk-your"):
        return "openai", os.environ.get(
            "OPENAI_MODEL", _DEFAULT_OPENAI_MODEL
        ).strip() or _DEFAULT_OPENAI_MODEL
    if os.environ.get("OLLAMA_BASE_URL", "").strip():
        return "ollama", os.environ.get(
            "OLLAMA_MODEL", _DEFAULT_OLLAMA_MODEL
        ).strip() or _DEFAULT_OLLAMA_MODEL
    return "offline", None


def provenance_block(events: list[dict[str, Any]]) -> dict[str, Any]:
    """A self-describing provenance block for the advisory layer, derived from the audit log.

    Honest in offline-enabled mode too: ``reports_seen`` counts every advisory pass while
    ``llm_invocations`` stays 0 (deterministic passthrough) until a real provider is configured.
    """
    recs = [e for e in events if e.get("event") == "llm_extract"]
    used = [e for e in recs if e.get("used_llm")]
    mode, model = _provider()
    return {
        "enabled": True,
        "mode": mode,
        "model": model,
        "reports_seen": len(recs),
        "llm_invocations": len(used),
        "guardrail_tripwires": sum(1 for e in used if not e.get("ok")),
    }


_dotenv_loaded = False


def _load_dotenv_once() -> None:
    """Load a local ``.env`` once per process, if ``python-dotenv`` is installed (live path only)."""
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # pragma: no cover - dotenv is optional; absence is fine
        pass


def _run_extraction(
    report: RawReport, *, mode: str, model: str | None
) -> (
    ExtractedReport
):  # pragma: no cover - exercised live (needs a provider); offline/guardrail paths are tested
    """Run the OpenAI Agents SDK extraction agent on the rendered free text (lazy SDK import)."""
    from agents import Agent, Runner, trace

    agent = Agent(
        name="Citizen-report extractor",
        instructions=_EXTRACT_INSTRUCTIONS,
        model=_resolve_model(mode, model),
        output_type=ExtractedReport,
    )
    with trace("perception_extract"):
        result = Runner.run_sync(agent, _EXTRACT_PROMPT.format(text=render_report_text(report)))
    output = result.final_output
    if not isinstance(output, ExtractedReport):  # defensive: the SDK should honour output_type
        raise TypeError("extractor did not return an ExtractedReport")
    return output


def _resolve_model(mode: str, model: str | None) -> Any:  # pragma: no cover - live only
    """Return the SDK model: a model-name string (OpenAI) or a local chat-completions model (Ollama)."""
    if mode != "ollama":
        return model
    from agents import OpenAIChatCompletionsModel, set_default_openai_client, set_tracing_disabled
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=os.environ["OLLAMA_BASE_URL"], api_key="ollama")
    set_default_openai_client(client)
    set_tracing_disabled(True)  # local server -> never export traces to the cloud
    return OpenAIChatCompletionsModel(model=model, openai_client=client)
