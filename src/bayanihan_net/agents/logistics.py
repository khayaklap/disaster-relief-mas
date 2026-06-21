"""Logistics agents -- own scarce assets and bid them into contract-net auctions.

Each logistics agent holds a fleet subset (rescue boats, relief trucks, or medical teams,
keyed by ``owner``) and, on a task announcement, submits a feasible bid for every fit idle
asset. The bid carries the bidder's *local* view (its own ETA, capacity, route risk, and a
selfish ``local_cost``). Crucially, the bidder does NOT decide who wins -- the coordinator
re-scores bids on a global welfare objective -- which is the structural reason a logistics
agent cannot hoard or cherry-pick easy work to flatter its own utilization.
"""

from __future__ import annotations

from ..blackboard import Blackboard
from ..config import CAPABILITY, Scenario
from ..messages import BidPayload, Envelope, MsgType, TaskAnnouncePayload
from .base import Agent
from .routing import RoutingAgent


class LogisticsAgent(Agent):
    """Bids the idle assets it owns on announced tasks."""

    role = "logistics"
    default_scopes: tuple[str, ...] = ("asset:bid", "asset:commit")

    def __init__(self, agent_id: str, scenario: Scenario, owner: str) -> None:
        super().__init__(agent_id, scenario)
        self.owner = owner

    def bid(
        self, task: TaskAnnouncePayload, bb: Blackboard, routing: RoutingAgent, tick: int
    ) -> list[Envelope]:
        """Submit a feasible bid for each fit, idle, owned asset."""
        capable = CAPABILITY[task.itype]
        out: list[Envelope] = []
        for a in bb.idle_assets():
            if a.owner != self.owner or a.asset_type not in capable:
                continue
            route = routing.route_for(a, task.node)
            if not route.feasible:
                bb.record(tick, "bid_infeasible", asset_id=a.asset_id, task_id=task.task_id)
                continue
            payload = BidPayload(
                task_id=task.task_id,
                asset_id=a.asset_id,
                asset_type=a.asset_type,
                eta_ticks=route.eta_ticks,
                capacity=a.capacity,
                route_risk=route.risk,
                feasible=True,
                local_cost=route.eta_ticks + route.risk,  # the bidder's selfish estimate
            )
            out.append(
                self._emit(
                    msg_type=MsgType.BID,
                    payload=payload,
                    trace_id=f"trace:{task.incident_id}",
                    tick=tick,
                    correlation_id=task.task_id,
                    priority=task.priority,
                )
            )
        return out
