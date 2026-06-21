"""Escalation, human-in-the-loop approval, and rollback.

This module is the *accountability* leg of the hybrid design. When the policy gate
(:mod:`governance.policy`) flags a high-stakes action, the coordinator routes a
**decision package** to a :class:`HumanApprover` rather than acting autonomously. For a
deterministic, reproducible simulation the "human" is a scripted duty-officer policy with
an explicit, defensible rule set; a real deployment swaps in a UI for a person -- the
*gate and the contract* are what matter and are real in code.

It also holds the equity-guardrail **rollback** hook (:func:`should_auto_rollback`), which
recommends reversing reversible awards when projected unmet-need inequity (Gini) crosses a
threshold. It is a tested pure function but is **not wired into the runtime** -- the live
rollback trigger is lease reclaim, in the coordinator -- and is flagged here rather than
presented as active.
"""

from __future__ import annotations

from ..config import EnvParams
from ..incidents import priority_of
from ..messages import ApprovalDecisionPayload, ApprovalRequestPayload, Priority


class HumanApprover:
    """A deterministic stand-in for the on-call CDRRMC duty officer.

    Policy: approve high-stakes commitments that are clearly justified, but *withhold* the
    last asset of a type from a non-critical incident -- preserving a reserve for a
    possible critical call is exactly the judgement a human is kept in the loop for."""

    def __init__(self, approver_id: str = "cdrrmo-duty-officer") -> None:
        self.approver_id = approver_id

    def decide(self, req: ApprovalRequestPayload, *, severity: float) -> ApprovalDecisionPayload:
        """Approve or deny a gated commitment, applying the duty-officer reserve policy."""
        # Read the structured gate flag, not a parsed reason string (a rename of the reason
        # text can no longer silently disable the reserve-protection rule).
        commits_last_reserve = bool(
            req.decision_package.get("risk", {}).get("commits_last_reserve", False)
        )
        # Withhold the last unit of a scarce type unless the incident is at least HIGH
        # priority -- preserve the reserve for a more severe call still to come.
        if commits_last_reserve and priority_of(severity) not in (Priority.CRITICAL, Priority.HIGH):
            return ApprovalDecisionPayload(
                request_id=req.request_id,
                approved=False,
                approver=self.approver_id,
                reason="hold last-of-type asset in reserve for a possible critical incident",
            )
        return ApprovalDecisionPayload(
            request_id=req.request_id,
            approved=True,
            approver=self.approver_id,
            reason="high-stakes action justified by severity and need",
        )


def should_auto_rollback(projected_gini: float, params: EnvParams) -> bool:
    """Equity guardrail: recommend rolling back recent reversible awards if the projected
    unmet-need Gini would exceed the configured threshold (inequity is a safety failure)."""
    return projected_gini > params.gini_rollback_threshold
