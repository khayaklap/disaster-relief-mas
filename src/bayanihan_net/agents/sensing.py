"""Sensing / scout agents -- the eyes on the COP.

A scout covers a subset of barangays and converts the raw citizen-report stream into typed
:class:`IncidentReportPayload` messages on the bus. It is the system's first, partial view
of the disaster: each scout sees only its own nodes (partial observability by design), and
it raises a *Sybil-plausibility* flag on reports that cannot physically be true given the
observed flood -- a heuristic, fallible guard, never ground truth.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..config import Scenario
from ..incidents import content_key, priority_of, sla_deadline
from ..messages import Envelope, IncidentReportPayload, MsgType
from ..network import local_flood_intensity
from ..perception import extract_report
from ..scenario import RawReport
from .base import Agent


class SensingAgent(Agent):
    """A field scout. Reports incidents for the barangays it covers."""

    role = "sensing-scout"
    default_scopes = ("cop:write",)

    def __init__(self, agent_id: str, scenario: Scenario, nodes: Iterable[str]) -> None:
        super().__init__(agent_id, scenario)
        self.nodes = set(nodes)
        # Audit records from the OPTIONAL advisory LLM extraction (empty unless llm_advisory is
        # on); the engine drains them into the blackboard event log after observe().
        self._advisory: list[dict[str, object]] = []

    def observe(
        self, raw_reports: list[RawReport], cop_river_m: float | None, tick: int
    ) -> list[Envelope]:
        """Turn this tick's raw reports (for my nodes) into incident-report envelopes."""
        out: list[Envelope] = []
        for r in raw_reports:
            if r.node not in self.nodes:
                continue
            key = content_key(r.node, r.itype, tick, self.params.dedup_window_ticks)
            # Default path: copy the structured facts. Advisory path (opt-in): an LLM extracts
            # them from rendered free text, re-validated against this ground truth -- so the
            # values used are identical on success and fall back to the truth on any mismatch.
            people, itype = r.people, r.itype
            if self.params.llm_advisory:
                outcome = extract_report(r, trace_id=f"trace:{key}")
                people, itype = outcome.people, outcome.itype
                self._advisory.append(outcome.audit)
            payload = IncidentReportPayload(
                incident_id=r.ground_truth_id,
                node=r.node,
                barangay=r.barangay,
                itype=itype,
                people=people,
                severity=r.severity,
                reported_tick=tick,
                is_duplicate=r.is_duplicate,
                is_suspected_false=self._suspect_false(r, cop_river_m),
            )
            prio = priority_of(r.severity)
            out.append(
                self._emit(
                    msg_type=MsgType.INCIDENT_REPORT,
                    payload=payload,
                    trace_id=f"trace:{key}",
                    tick=tick,
                    priority=prio,
                    idempotency_key=key,  # content key -> fuses duplicates at the blackboard
                    deadline_tick=sla_deadline(tick, prio, self.params),
                )
            )
        return out

    def drain_advisory(self) -> list[dict[str, object]]:
        """Return and clear the advisory-extraction audit records gathered this tick.

        Empty unless ``llm_advisory`` is on. The scout cannot write the shared event log
        itself (it only emits messages); the engine drains these into the blackboard audit
        log, keeping the no-double-commit/idempotency invariants enforced in one place.
        """
        records = self._advisory
        self._advisory = []
        return records

    def _suspect_false(self, r: RawReport, cop_river_m: float | None) -> bool:
        """Flag a severe report from a barangay that cannot plausibly be flooding yet.

        Deliberately conservative: it only fires where the *observed* flood makes a severe
        rescue physically implausible, so it catches some Sybil injections without
        suppressing genuine demand. Corroboration by a second report clears the flag
        (handled at the blackboard)."""
        if cop_river_m is None:
            return False
        bgy = self.scenario.barangay_by_node(r.node)
        if bgy is None:
            return False
        intensity = local_flood_intensity(cop_river_m, bgy.flood_sensitivity, self.params)
        return intensity <= 0.0 and r.severity >= 0.6
