"""Governance policy -- pure, prompt-independent decision rules.

Two kinds of policy live here, and both are deliberately *outside* any agent's reasoning
(the MAS-deck "policy as code, not in the prompt" stance):

1. **Authorization** -- does this security context hold the scope an action requires?
   The basis for the A2A / MCP trust boundary checks in ``interop/``.
2. **The HITL gate** -- is an action irreversible / high-stakes enough to require a human?
   :func:`evaluate_commit_gate` answers from explicit thresholds in :class:`EnvParams`
   (committing the last asset of a type, a large single commitment, a forced evacuation),
   never from a learned or free-text judgement -- so the gate is auditable and testable.

All functions are pure: same inputs -> same decision, with no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import AssetType, EnvParams
from ..incidents import Incident
from ..messages import SecurityContext


def is_authorized(ctx: SecurityContext, required_scope: str) -> bool:
    """True iff the context carries the capability scope an action requires."""
    return required_scope in ctx.scopes


@dataclass(frozen=True)
class GateDecision:
    """The HITL gate's verdict for one proposed action."""

    requires_approval: bool
    reasons: tuple[str, ...]
    risk_level: str  # "low" | "high"
    commits_last_reserve: bool  # structured flag so the approver need not parse reason strings


def evaluate_commit_gate(
    *,
    action: str,
    people: int,
    asset_type: AssetType,
    idle_of_type_after: int,
    fleet_of_type: int,
    river_m: float,
    params: EnvParams,
) -> GateDecision:
    """Decide whether committing an asset needs human approval.

    A commitment is gated when ANY high-stakes condition holds: it would commit the *last
    ready unit* of a genuinely scarce type (``fleet_of_type`` at or below the reserve-protect
    size -- e.g. the two medical teams -- so we don't page a human for every busy-boat
    commit), or it serves a large number of people in one irreversible dispatch.

    A third reason -- enacting a *forced evacuation* at 3rd-alarm river level -- is defined and
    unit-tested but is **not raised by the current run loop**, which only issues ``commit_asset``
    actions; it is kept for the evacuation-order action a real deployment would add (compare the
    similarly unwired :func:`coordination.escalation.should_auto_rollback` hook).
    """
    reasons: list[str] = []
    commits_last_reserve = (
        params.last_asset_of_type_gate
        and idle_of_type_after <= 0
        and fleet_of_type <= params.reserve_protect_fleet
    )
    if commits_last_reserve:
        reasons.append(f"commit_last_{asset_type.value}")
    if people >= params.large_commit_people:
        reasons.append("large_commit")
    # Defined gate reason for a forced-evacuation order; the live loop only commits assets, so this
    # branch is reached by tests, not the current run (an evacuation-order action is future work).
    if action == "forced_evacuation" and river_m >= params.forced_evacuation_alarm_m:
        reasons.append("forced_evacuation")
    requires = len(reasons) > 0
    return GateDecision(
        requires_approval=requires,
        reasons=tuple(reasons),
        risk_level="high" if requires else "low",
        commits_last_reserve=commits_last_reserve,
    )


def build_decision_package(
    *,
    incident: Incident,
    recommendation: dict[str, Any],
    alternatives: list[dict[str, Any]],
    gate: GateDecision,
) -> dict[str, Any]:
    """Assemble the package the human approver sees: *context, options, recommendation,
    risk* -- a structured decision aid, not raw agent chatter (MAS-deck HITL guidance)."""
    return {
        "context": {
            "incident_id": incident.incident_id,
            "barangay": incident.barangay,
            "type": incident.itype.value,
            "people": incident.people,
            "severity": incident.severity,
            "priority": incident.priority.value,
            "deadline_tick": incident.deadline_tick,
        },
        "recommendation": recommendation,
        "options": alternatives,
        "risk": {
            "level": gate.risk_level,
            "gate_reasons": list(gate.reasons),
            "commits_last_reserve": gate.commits_last_reserve,
        },
    }
