"""Auditor -- the read-only observer that turns the run into evidence.

The auditor never acts on the disaster; it reads the immutable event log, the COP, and the
world's ground truth, and produces the two reports the evaluation grades on:

* :meth:`outcome_report` -- the *system-level* outcome by joining each genuine incident
  back to the COP (via its content key) to measure severity-weighted people served, unmet
  need per barangay, SLA compliance, and how fabricated (Sybil) reports were handled.
* :meth:`emergence_report` -- the emergence detectors (unmet-need Gini, asset
  reassignment rate, per-barangay activity entropy) plus a roll-up of the safety counters.

Keeping scoring in a read-only auditor (rather than in the acting agents) is the
separation the governance story depends on: the thing being graded does not grade itself.
"""

from __future__ import annotations

from typing import Any

from ..blackboard import Blackboard
from ..governance.metrics import gini, reassignment_rate, shannon_entropy
from ..incidents import Incident, IncidentStatus, content_key
from ..messages import BidPayload, Envelope, MsgType
from ..scenario import World
from .base import Agent


def _tally_served_and_sla(
    by_key: dict[str, list[Incident]], bb: Blackboard
) -> tuple[dict[str, int], float, int, int]:
    """Credit served people and tally dispatch-SLA, grouped by the COP incident genuine reports
    fuse to. Each fused dispatch's served headcount is distributed once across its genuine
    incidents -- most-severe first, capped at each one's need -- so a dedup collision never
    double-counts lives served. Returns ``(served_by, sev_served, sla_met, late)``.
    """
    served_by: dict[str, int] = {}
    sev_served = 0.0
    sla_met = 0
    late = 0
    for _key, gts in by_key.items():
        cop = bb.incidents.get(_key)
        if cop is None:
            continue
        # SLA is a *dispatch* target: a responder was committed (assigned) by the deadline, judged
        # independently of completion. Genuine incidents fused onto one COP incident share its verdict.
        dispatched_on_time = (
            cop.assigned_tick is not None and cop.assigned_tick <= cop.deadline_tick
        )
        if dispatched_on_time:
            sla_met += len(gts)
        if cop.status is not IncidentStatus.RESOLVED:
            continue
        remaining = cop.served_people
        for gt in sorted(gts, key=lambda g: -g.severity):
            served = min(gt.people, remaining)
            served_by[gt.barangay] = served_by.get(gt.barangay, 0) + served
            sev_served += gt.severity * served
            remaining -= served
        if not dispatched_on_time:
            late += len(gts)  # served, but the responder was committed after the deadline
    return served_by, sev_served, sla_met, late


class Auditor(Agent):
    """Computes outcome and emergence reports from the run's evidence."""

    role = "auditor"
    default_scopes = ("audit:read",)

    def outcome_report(self, world: World, bb: Blackboard) -> dict[str, Any]:
        """System-level outcome: served vs. unmet need by barangay, SLA, Sybil handling."""
        window = world.params.dedup_window_ticks
        need_by: dict[str, int] = {}
        sla_total = 0
        sev_need = 0.0  # severity-weighted, for the headline harm-reduction metric

        # Genuine incidents are grouped by the COP content key they fuse to. Two distinct
        # genuine events in one node/type/window share a single COP incident, so the people
        # that one dispatch served are credited *once* across them -- not once per incident,
        # which would let a fused dispatch over-count lives served (the dedup hazard's true cost).
        by_key: dict[str, list[Incident]] = {}
        for gt in world.ground_truth.values():  # genuine incidents only
            need_by[gt.barangay] = need_by.get(gt.barangay, 0) + gt.people
            sev_need += gt.severity * gt.people
            sla_total += 1
            key = content_key(gt.node, gt.itype, gt.reported_tick, window)
            by_key.setdefault(key, []).append(gt)

        served_by, sev_served, sla_met, late = _tally_served_and_sla(by_key, bb)

        unmet_by = {b: max(0, need_by[b] - served_by.get(b, 0)) for b in need_by}
        total_need = sum(need_by.values())
        total_served = sum(served_by.values())

        # Equity is about *coverage*, not absolute volume: a big barangay will always have
        # the most unmet people. The fairness lever targets the per-barangay served fraction
        # -- so the headline equity metrics are the worst-served community's coverage and the
        # Gini of unmet *fractions* (how unequally coverage is spread), not unmet headcount.
        served_frac_by = {
            b: (served_by.get(b, 0) / need_by[b] if need_by[b] else 1.0) for b in need_by
        }
        min_served_fraction = min(served_frac_by.values()) if served_frac_by else 0.0
        coverage_gini = gini([1.0 - sf for sf in served_frac_by.values()])

        # Sybil handling: of the fabricated reports that reached the COP, how many were
        # correctly suppressed vs. wrongly served (a wasted, harm-displacing dispatch)?
        false_served = 0
        false_suppressed = 0
        for key in world.false_keys:
            cop = bb.incidents.get(key)
            if cop is None:
                continue
            if cop.status is IncidentStatus.RESOLVED:
                false_served += 1
            elif cop.status is IncidentStatus.SUPPRESSED:
                false_suppressed += 1

        return {
            "total_need_people": total_need,
            "total_served_people": total_served,
            "served_fraction": round(total_served / total_need, 4) if total_need else 0.0,
            "severity_weighted_served_fraction": (
                round(sev_served / sev_need, 4) if sev_need else 0.0
            ),
            "genuine_incidents": sla_total,
            "sla_met": sla_met,
            "served_late": late,
            "sla_compliance": round(sla_met / sla_total, 4) if sla_total else 0.0,
            "unmet_by_barangay": dict(sorted(unmet_by.items())),
            "need_by_barangay": dict(sorted(need_by.items())),
            "served_fraction_by_barangay": {
                b: round(f, 4) for b, f in sorted(served_frac_by.items())
            },
            "min_served_fraction": round(min_served_fraction, 4),
            "coverage_gini": round(coverage_gini, 4),
            "gini_unmet": round(gini(list(unmet_by.values())), 4),
            "sybil_reports_seen": len(world.false_keys),
            "sybil_suppressed": false_suppressed,
            "sybil_served": false_served,
        }

    def emergence_report(
        self,
        world: World,
        bb: Blackboard,
        *,
        commitments: int,
        rollbacks: int,
        escalations: int,
        approvals: int,
        approvals_denied: int,
        drops: int,
    ) -> dict[str, Any]:
        """Emergence detectors + safety counters (the 'is it misbehaving?' panel)."""
        # per-barangay served activity -> load-spread entropy
        served_counts: dict[str, int] = {}
        for inc in bb.incidents.values():
            if inc.status is IncidentStatus.RESOLVED:
                served_counts[inc.barangay] = served_counts.get(inc.barangay, 0) + 1

        unmet = self.outcome_report(world, bb)["unmet_by_barangay"]
        return {
            "gini_unmet": round(gini(list(unmet.values())), 4),
            "reassignment_rate": round(reassignment_rate(rollbacks, commitments), 4),
            "load_spread_entropy": round(
                shannon_entropy([float(v) for v in served_counts.values()]), 4
            ),
            "commitments": commitments,
            "rollbacks": rollbacks,
            "escalations": escalations,
            "hitl_approvals": approvals,
            "hitl_denied": approvals_denied,
            "messages_dropped": drops,
        }

    def quality_report(
        self, events: list[dict[str, Any]], bus_log: list[Envelope]
    ) -> dict[str, Any]:
        """The *agent-* and *interaction-*level evaluation panels (the two levels the
        outcome/emergence reports don't cover), computed straight from the audit log and the
        message wire-tap so every level of the four-level grid emits real numbers."""
        # agent level: did each agent do its job?
        n_feasible_bids = sum(1 for e in bus_log if e.msg_type is MsgType.BID)
        n_infeasible = sum(1 for e in events if e["event"] == "bid_infeasible")
        attempts = n_feasible_bids + n_infeasible
        opened = sum(1 for e in events if e["event"] == "incident_opened")
        fused = sum(1 for e in events if e["event"] == "incident_fused")
        suppressed = sum(1 for e in events if e["event"] == "incident_suppressed")
        agent_level = {
            "bid_feasibility_rate": round(n_feasible_bids / attempts, 4) if attempts else 0.0,
            "reports_fused_as_duplicates": fused,
            "dedup_fusion_rate": round(fused / (opened + fused), 4) if (opened + fused) else 0.0,
            "suspected_false_suppressed": suppressed,
        }

        # interaction level: did the protocols hold?
        committed: set[str | None] = set()
        double_commit = False
        for e in events:
            if e["event"] == "asset_committed":
                aid = e.get("asset_id")
                if aid in committed:
                    double_commit = True
                committed.add(aid)
            elif e["event"] in ("asset_freed", "asset_released"):
                committed.discard(e.get("asset_id"))
        bid_keys = set()
        for env in bus_log:
            if env.msg_type is MsgType.BID:
                p = env.typed_payload()
                assert isinstance(p, BidPayload)
                bid_keys.add((p.task_id, p.asset_id))
        awards = [e for e in events if e["event"] == "awarded"]
        traced = sum(
            1
            for a in awards
            if any(
                a.get("asset_id") == asset and tid.startswith(f"task:{a.get('incident_id')}:")
                for tid, asset in bid_keys
            )
        )
        interaction_level = {
            "no_double_commit": not double_commit,
            "awards_trace_to_valid_bid_rate": round(traced / len(awards), 4) if awards else 1.0,
            "escalations_fired": sum(1 for e in bus_log if e.msg_type is MsgType.ESCALATION_RAISED),
        }
        return {"agent_level": agent_level, "interaction_level": interaction_level}
