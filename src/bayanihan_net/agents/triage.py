"""Triage agent -- turns the fused COP into a prioritized task queue.

Triage is where the shared situational picture becomes *action*. It suppresses
uncorroborated Sybil-suspect reports (a single unconfirmed implausible report is not
dispatched; a corroborated one is), then announces the worst open incidents as
contract-net tasks (calls-for-proposals), worst-first by severity and deadline. It does
not allocate assets -- that is the auction's job -- it decides *what is worth bidding on*.
"""

from __future__ import annotations

from ..blackboard import Blackboard
from ..incidents import IncidentStatus
from ..messages import Envelope, MsgType, TaskAnnouncePayload
from .base import Agent


class TriageAgent(Agent):
    """Prioritizes incidents and announces them as tasks."""

    role = "triage"
    default_scopes = ("taskboard:write",)

    def plan(self, bb: Blackboard, tick: int, max_tasks: int) -> list[Envelope]:
        """Suppress uncorroborated Sybil reports, then announce the worst open incidents as tasks."""
        self._suppress_suspected_false(bb, tick)
        out: list[Envelope] = []
        for inc in bb.open_incidents()[:max_tasks]:
            task_id = f"task:{inc.incident_id}:{tick}"
            payload = TaskAnnouncePayload(
                task_id=task_id,
                incident_id=inc.incident_id,
                node=inc.node,
                itype=inc.itype,
                people=inc.people,
                severity=inc.severity,
                priority=inc.priority,
                deadline_tick=inc.deadline_tick,
            )
            out.append(
                self._emit(
                    msg_type=MsgType.TASK_ANNOUNCED,
                    payload=payload,
                    trace_id=f"trace:{inc.incident_id}",
                    tick=tick,
                    priority=inc.priority,
                    deadline_tick=inc.deadline_tick,
                )
            )
        return out

    def _suppress_suspected_false(self, bb: Blackboard, tick: int) -> None:
        """Drop uncorroborated Sybil-suspect incidents from the actionable queue."""
        for inc in bb.incidents.values():
            if inc.status is IncidentStatus.OPEN and inc.suspected_false and inc.report_count < 2:
                inc.status = IncidentStatus.SUPPRESSED
                bb.record(
                    tick,
                    "incident_suppressed",
                    incident_id=inc.incident_id,
                    reason="suspected_false_uncorroborated",
                )
