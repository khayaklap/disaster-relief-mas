"""Tests for the optional advisory LLM perception layer -- all run with NO API key.

The live SDK call (`_run_extraction`) is the only thing that needs a provider; everything that
matters for *safety* -- the guardrail, the fallback, the offline passthrough, and the
byte-identical default -- is pure and is tested here by stubbing the provider/extractor.
"""

from __future__ import annotations

import pytest

from bayanihan_net.agents.sensing import SensingAgent
from bayanihan_net.config import SEED, IncidentType, config_with, default_config
from bayanihan_net.engine import Engine
from bayanihan_net.perception import llm_extract
from bayanihan_net.perception.llm_extract import (
    ExtractedReport,
    evaluate_extraction,
    extract_report,
    provenance_block,
)
from bayanihan_net.perception.report_text import render_report_text
from bayanihan_net.scenario import RawReport


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop any test from loading a real local .env (keeps the suite hermetic)."""
    monkeypatch.setattr(llm_extract, "_dotenv_loaded", True)


def _raw(
    *, people: int, itype: IncidentType, node: str = "T", barangay: str = "Tumana"
) -> RawReport:
    return RawReport(
        report_id="R-0001",
        ground_truth_id="GT-0001",
        node=node,
        barangay=barangay,
        itype=itype,
        people=people,
        severity=0.5,
        reported_tick=1,
        is_duplicate=False,
        is_false=False,
    )


# free-text rendering
def test_render_is_deterministic_and_states_the_headcount() -> None:
    r = _raw(people=23, itype=IncidentType.RESCUE)
    assert render_report_text(r) == render_report_text(r)  # process-independent, stable
    text = render_report_text(r)
    assert "23" in text and "Tumana" in text


# pure guardrail core
def test_guardrail_passes_when_extraction_matches_truth() -> None:
    r = _raw(people=10, itype=IncidentType.RESCUE)
    assert evaluate_extraction(ExtractedReport(people=10, itype=IncidentType.RESCUE), r) == []


def test_guardrail_trips_on_people_or_type_mismatch() -> None:
    r = _raw(people=10, itype=IncidentType.RESCUE)
    v_people = evaluate_extraction(ExtractedReport(people=11, itype=IncidentType.RESCUE), r)
    assert v_people and "people mismatch" in v_people[0]
    v_type = evaluate_extraction(ExtractedReport(people=10, itype=IncidentType.MEDICAL), r)
    assert v_type and "itype mismatch" in v_type[0]


# extract_report: offline / verified / hallucination / error
def test_extract_offline_is_deterministic_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_extract, "_provider", lambda: ("offline", None))
    out = extract_report(_raw(people=12, itype=IncidentType.RESCUE), trace_id="t")
    assert out.people == 12 and out.itype is IncidentType.RESCUE
    assert out.audit["used_llm"] is False and out.audit["ok"] is True


def test_extract_uses_a_verified_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_extract, "_provider", lambda: ("openai", "m"))
    monkeypatch.setattr(
        llm_extract,
        "_run_extraction",
        lambda report, *, mode, model: ExtractedReport(people=report.people, itype=report.itype),
    )
    out = extract_report(_raw(people=7, itype=IncidentType.MEDICAL), trace_id="t")
    assert out.people == 7 and out.itype is IncidentType.MEDICAL
    assert out.audit["used_llm"] is True and out.audit["ok"] is True


def test_extract_falls_back_on_hallucination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_extract, "_provider", lambda: ("openai", "m"))
    monkeypatch.setattr(
        llm_extract,
        "_run_extraction",
        lambda report, *, mode, model: ExtractedReport(
            people=report.people + 50, itype=report.itype
        ),
    )
    out = extract_report(_raw(people=9, itype=IncidentType.RESCUE), trace_id="t")
    assert out.people == 9  # the guardrail discarded the LLM value and used ground truth
    assert out.audit["used_llm"] is True and out.audit["ok"] is False
    assert any("people mismatch" in v for v in out.audit["violations"])


def test_extract_falls_back_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_extract, "_provider", lambda: ("openai", "m"))

    def _boom(report: RawReport, *, mode: str, model: str | None) -> ExtractedReport:
        raise RuntimeError("api down")

    monkeypatch.setattr(llm_extract, "_run_extraction", _boom)
    out = extract_report(_raw(people=5, itype=IncidentType.RELIEF), trace_id="t")
    assert out.people == 5 and out.audit["used_llm"] is False
    assert out.audit["reason"].startswith("error:")


def test_provenance_block_counts_from_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_extract, "_provider", lambda: ("offline", None))
    events = [
        {"event": "llm_extract", "used_llm": False, "ok": True},
        {"event": "llm_extract", "used_llm": True, "ok": True},
        {"event": "llm_extract", "used_llm": True, "ok": False},
        {"event": "asset_committed"},
    ]
    block = provenance_block(events)
    assert block["enabled"] is True and block["reports_seen"] == 3
    assert block["llm_invocations"] == 2 and block["guardrail_tripwires"] == 1


# sensing seam
def test_sensing_default_path_does_not_touch_the_seam() -> None:
    scenario = default_config().scenario  # llm_advisory is False
    scout = SensingAgent("scout", scenario, [b.node for b in scenario.barangays])
    envs = scout.observe([_raw(people=14, itype=IncidentType.RESCUE)], cop_river_m=16.0, tick=1)
    assert envs[0].typed_payload().people == 14  # type: ignore[attr-defined]
    assert scout.drain_advisory() == []  # nothing recorded when disabled


def test_sensing_advisory_offline_records_audit_but_keeps_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_extract, "_provider", lambda: ("offline", None))
    scenario = config_with(SEED, llm_advisory=True).scenario
    scout = SensingAgent("scout", scenario, [b.node for b in scenario.barangays])
    envs = scout.observe([_raw(people=14, itype=IncidentType.RESCUE)], cop_river_m=16.0, tick=1)
    assert envs[0].typed_payload().people == 14  # type: ignore[attr-defined]
    audits = scout.drain_advisory()
    assert len(audits) == 1 and audits[0]["used_llm"] is False
    assert scout.drain_advisory() == []  # drained


# end-to-end determinism invariant
def test_default_run_has_no_llm_events_or_provenance() -> None:
    report = Engine(default_config()).run()
    assert all(e["event"] != "llm_extract" for e in report.events)
    assert "llm" not in report.provenance


def test_advisory_offline_run_stamps_provenance_with_zero_invocations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_extract, "_provider", lambda: ("offline", None))
    report = Engine(config_with(SEED, llm_advisory=True)).run()
    llm = report.provenance["llm"]
    assert llm["enabled"] is True and llm["llm_invocations"] == 0  # offline passthrough
    assert llm["reports_seen"] >= 1


# provider selection + dotenv glue
def test_provider_selects_runtime_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert llm_extract._provider() == ("offline", None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-key-1234567890")
    monkeypatch.setenv("OPENAI_MODEL", "my-model")
    assert llm_extract._provider() == ("openai", "my-model")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-your-key-here")  # placeholder -> ignored
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    assert llm_extract._provider() == ("ollama", "llama3.1")


def test_load_dotenv_once_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_extract, "_dotenv_loaded", False)
    llm_extract._load_dotenv_once()  # runs once (python-dotenv may be absent -> handled)
    assert llm_extract._dotenv_loaded is True
    llm_extract._load_dotenv_once()  # second call is a no-op
