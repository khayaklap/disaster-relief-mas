"""Incidents: the demand the system must serve, plus pure severity/priority scoring.

An :class:`Incident` is the unit of work. ``severity`` (0..1) is a deterministic function
of how many people are affected, the barangay's vulnerability, the incident type, and how
fast the water is rising -- so the *same* facts always yield the *same* priority. Priority
in turn sets the SLA (ticks-to-first-dispatch) the evaluation later grades against.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .config import BarangaySpec, EnvParams, IncidentType
from .messages import Priority

# Relative urgency multiplier by incident type (medical rescues are most time-critical).
_TYPE_WEIGHT: dict[IncidentType, float] = {
    IncidentType.MEDICAL: 1.0,
    IncidentType.RESCUE: 0.9,
    IncidentType.RELIEF: 0.5,
}


class IncidentStatus(StrEnum):
    """Lifecycle of an incident: OPEN -> ASSIGNED -> IN_PROGRESS -> RESOLVED (or FAILED /
    SUPPRESSED). Reversible only up to ASSIGNED; IN_PROGRESS work is never rolled back."""

    OPEN = "open"  # reported, awaiting allocation
    ASSIGNED = "assigned"  # an asset has been awarded (reversible until in-progress)
    IN_PROGRESS = "in_progress"  # asset en route / on scene (irreversible commitment)
    RESOLVED = "resolved"  # served
    FAILED = "failed"  # unreachable / deadline missed
    SUPPRESSED = "suppressed"  # flagged as a false (Sybil) report and not served


@dataclass
class Incident:
    """One unit of disaster demand. Shared by reference between the blackboard and the
    engine, so a resolution recorded once is visible everywhere."""

    incident_id: str
    ground_truth_id: str  # stable id of the underlying real event (for scoring duplicates)
    node: str
    barangay: str
    itype: IncidentType
    people: int
    severity: float
    reported_tick: int
    deadline_tick: int
    is_false: bool = False  # ground-truth label (a fabricated report); only set world-side
    suspected_false: bool = False  # COP-side heuristic flag raised by sensing/triage (Sybil guard)
    status: IncidentStatus = IncidentStatus.OPEN
    assigned_asset: str | None = None
    assigned_tick: int | None = None
    on_scene_tick: int | None = None  # when the asset arrived on scene (work became irreversible)
    resolved_tick: int | None = None
    served_people: int = 0  # headcount actually served (min of asset capacity and people)
    last_report_tick: int = 0  # freshness of the latest citizen report
    report_count: int = 1  # how many citizen reports collapsed into this incident
    escalated: bool = False  # an unreachable-incident escalation has already been raised

    @property
    def is_open(self) -> bool:
        """True while the incident is still awaiting allocation."""
        return self.status is IncidentStatus.OPEN

    @property
    def priority(self) -> Priority:
        """The incident's priority, derived from its severity."""
        return priority_of(self.severity)


def severity(
    *, people: int, barangay: BarangaySpec, itype: IncidentType, river_rising_rate: float
) -> float:
    """Deterministic 0..1 severity score.

    Combines exposure (people relative to a 200-person reference, capped), the barangay's
    structural vulnerability, the incident-type urgency weight, and a surge term for a
    fast-rising river. Pure and monotone in each input -- easy to defend and to test.
    """
    exposure = min(1.0, people / 200.0)
    surge = min(1.0, max(0.0, river_rising_rate) / 0.4)  # 0.4 m/tick ~ extreme rise
    base = 0.55 * exposure + 0.30 * barangay.vulnerability + 0.15 * surge
    return round(min(1.0, base * _TYPE_WEIGHT[itype] + 0.05), 4)


def content_key(node: str, itype: IncidentType, tick: int, window_ticks: int) -> str:
    """The COP deduplication key for a citizen report.

    Reports of the same incident type, in the same node, falling in the same fixed time
    *bucket* (``tick // window`` -- a tumbling window, not a sliding one) collapse onto one
    Common-Operating-Picture incident. This is what the triage layer uses to fuse duplicates
    -- and, deliberately, the seam where two *distinct* genuine events in one barangay-bucket
    can be merged (a documented dedup hazard). The world recomputes this same key to join its
    ground truth back to the COP for scoring.
    """
    bucket = tick // max(1, window_ticks)
    return f"{node}:{itype.value}:{bucket}"


def priority_of(sev: float) -> Priority:
    """Map a 0..1 severity score to a priority band (the CRITICAL/HIGH/MEDIUM/LOW cutoffs)."""
    if sev >= 0.75:
        return Priority.CRITICAL
    if sev >= 0.55:
        return Priority.HIGH
    if sev >= 0.30:
        return Priority.MEDIUM
    return Priority.LOW


def sla_deadline(reported_tick: int, prio: Priority, params: EnvParams) -> int:
    """Tick by which first dispatch must happen to meet the service-level target."""
    budget = {
        Priority.CRITICAL: params.sla_critical_ticks,
        Priority.HIGH: params.sla_high_ticks,
        Priority.MEDIUM: params.sla_medium_ticks,
        Priority.LOW: params.sla_medium_ticks * 2,
    }[prio]
    return reported_tick + budget
