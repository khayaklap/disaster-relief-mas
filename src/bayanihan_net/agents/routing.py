"""Routing agent -- plans feasible routes over its *belief* of the flooded network.

The routing agent does not see the true flood directly. It maintains a private
:class:`RoadNetwork` belief that it updates from the COP river observation (in the fiction,
cross-checked against an MMDA road-closure MCP feed). When the COP is fresh this belief
tracks reality; when it is stale the belief lags -- which is precisely how a stale-COP
mis-route can occur, and why the engine validates actual movement against the *true*
network. This module is also the seam the offline RL routing study plugs into: a learned
policy can replace :meth:`route_for` while the heuristic remains the fallback.
"""

from __future__ import annotations

from ..blackboard import AssetState
from ..config import ASSET_TRAVEL_MODE, Scenario
from ..network import RoadNetwork, RouteResult
from .base import Agent


class RoutingAgent(Agent):
    """Computes risk-aware routes from an agent-local belief of the network state."""

    role = "routing"
    default_scopes = ("tool:mmda.read",)

    def __init__(self, agent_id: str, scenario: Scenario) -> None:
        super().__init__(agent_id, scenario)
        self.belief = RoadNetwork(scenario.edges, scenario.params)

    def update_belief(self, cop_river_m: float | None) -> None:
        """Refresh the routing belief from the latest COP river level (if any)."""
        if cop_river_m is not None:
            self.belief.update_flood(cop_river_m)

    def route_for(self, asset: AssetState, dst: str, *, risk_aware: bool = True) -> RouteResult:
        """Best route for an asset to a destination, using its travel mode."""
        mode = ASSET_TRAVEL_MODE[asset.asset_type]
        return self.belief.route(asset.node, dst, mode, risk_aware=risk_aware)
