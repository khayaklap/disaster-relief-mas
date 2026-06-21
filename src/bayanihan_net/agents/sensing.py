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
from ..scenario import RawReport
from .base import Agent


class SensingAgent(Agent):
    """A field scout. Reports incidents for the barangays it covers."""

    role = "sensing-scout"
    default_scopes = ("cop:write",)

    def __init__(self, agent_id: str, scenario: Scenario, nodes: Iterable[str]) -> None:
        super().__init__(agent_id, scenario)
        self.nodes = set(nodes)

    def observe(
        self, raw_reports: list[RawReport], cop_river_m: float | None, tick: int
    ) -> list[Envelope]:
        """Turn this tick's raw reports (for my nodes) into incident-report envelopes."""
        out: list[Envelope] = []
        for r in raw_reports:
            if r.node not in self.nodes:
                continue
            key = content_key(r.node, r.itype, tick, self.params.dedup_window_ticks)
            payload = IncidentReportPayload(
                incident_id=r.ground_truth_id,
                node=r.node,
                barangay=r.barangay,
                itype=r.itype,
                people=r.people,
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
