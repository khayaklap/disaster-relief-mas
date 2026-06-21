"""The road network and its dynamic flooding.

A :class:`RoadNetwork` wraps a ``networkx`` graph of the Marikina scenario. As the river
rises, each edge's flood depth grows in proportion to its ``exposure``; trucks (ROAD
mode) become blocked above ``road_impassable_depth_m`` while boats (BOAT mode) keep
going up to ``boat_max_depth_m``. Routing is deterministic Dijkstra over the *currently
passable* sub-network, with an optional **risk-aware** cost so a route can prefer a
slower-but-safer higher-ground bypass over a fast-but-deep riverside corridor.

This is the sub-problem the offline RL study later learns a policy for; here it is the
hand-coded heuristic baseline that the live MAS always uses as its safe default.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from .config import EdgeSpec, EnvParams, TravelMode


def local_flood_intensity(river_m: float, flood_sensitivity: float, params: EnvParams) -> float:
    """How hard a location is flooding (0..~1.5), given the river level and how sensitive
    its streets are. Shared by the world (demand generation) and the sensing agent (its
    Sybil-plausibility heuristic), so both reason from the same flood model."""
    over = max(0.0, river_m - params.flood_onset_m)
    return min(1.5, over * flood_sensitivity)


@dataclass(frozen=True)
class RouteResult:
    """The outcome of a routing query."""

    feasible: bool
    path: tuple[str, ...]
    eta_ticks: float  # estimated travel time in simulation ticks
    risk: float  # 0..1 time-weighted flood risk along the path


class RoadNetwork:
    """A flood-aware road graph. Pure/deterministic: no RNG, no hidden state beyond the
    current per-edge depth, which is a function of the latest river level."""

    def __init__(self, edges: tuple[EdgeSpec, ...], params: EnvParams) -> None:
        self.params = params
        self.G: nx.Graph = nx.Graph()
        for e in edges:
            self.G.add_edge(e.u, e.v, base_minutes=e.base_minutes, exposure=e.exposure, depth=0.0)
        self._river_m = params.river_start_m
        self.update_flood(params.river_start_m)

    # -- flood dynamics --------------------------------------------------------------
    def update_flood(self, river_m: float) -> None:
        """Recompute every edge's flood depth from the current river level."""
        self._river_m = river_m
        onset = self.params.flood_onset_m
        over = max(0.0, river_m - onset)
        for _, _, data in self.G.edges(data=True):
            data["depth"] = over * float(data["exposure"])

    def edge_depth(self, u: str, v: str) -> float:
        """Current flood depth (metres) on the road segment between two nodes."""
        return float(self.G.edges[u, v]["depth"])

    def passable(self, u: str, v: str, mode: TravelMode) -> bool:
        """Whether a segment can be traversed by the given mode at its current flood depth
        (roads flood out shallower than boats can pass)."""
        depth = self.edge_depth(u, v)
        if mode is TravelMode.ROAD:
            return depth <= self.params.road_impassable_depth_m
        return depth <= self.params.boat_max_depth_m  # BOAT

    def path_passable(self, path: tuple[str, ...], mode: TravelMode) -> bool:
        """Whether every edge of a planned path is currently passable for a mode.

        The engine checks a planned route against the *true* flood at arrival; a path that
        was passable on the routing agent's (possibly stale) belief but fails here is a
        stale-COP misroute -- the asset is stranded and must re-plan or be reclaimed."""
        if len(path) < 2:
            return True
        return all(self.passable(u, v, mode) for u, v in zip(path[:-1], path[1:], strict=True))

    # -- cost model ------------------------------------------------------------------
    def _edge_risk(self, depth: float) -> float:
        """0..1 flood risk for an edge at a given depth (deeper water = riskier)."""
        return min(1.0, depth / self.params.boat_max_depth_m)

    def _edge_minutes(self, u: str, v: str, risk_aware: bool) -> float:
        data = self.G.edges[u, v]
        depth = float(data["depth"])
        base = float(data["base_minutes"])
        # Water slows movement: +120% travel time at the deepest passable level.
        slowdown = 1.0 + 1.2 * self._edge_risk(depth)
        minutes = base * slowdown
        if risk_aware:
            # Risk-aversion penalty steers routes toward safer (shallower) corridors.
            minutes *= 1.0 + 2.0 * self._edge_risk(depth)
        return minutes

    def _passable_subgraph(self, mode: TravelMode) -> nx.Graph:
        keep = [(u, v) for u, v in self.G.edges() if self.passable(u, v, mode)]
        return self.G.edge_subgraph(keep)

    # -- routing ---------------------------------------------------------------------
    def route(
        self, src: str, dst: str, mode: TravelMode, *, risk_aware: bool = True
    ) -> RouteResult:
        """Shortest feasible route under the current flood, by travel mode.

        Returns ``feasible=False`` (an *unreachable* result) when no passable path
        exists -- a first-class outcome the system must escalate, not crash on.
        """
        if src == dst:
            return RouteResult(True, (src,), 0.0, 0.0)
        sub = self._passable_subgraph(mode)
        if src not in sub or dst not in sub:
            return RouteResult(False, (), float("inf"), 1.0)
        try:
            path = nx.shortest_path(
                sub, src, dst, weight=lambda u, v, _d: self._edge_minutes(u, v, risk_aware)
            )
        except nx.NetworkXNoPath:
            return RouteResult(False, (), float("inf"), 1.0)

        minutes = 0.0
        risk_time = 0.0
        for u, v in zip(path[:-1], path[1:], strict=True):
            m = self._edge_minutes(u, v, risk_aware=False)  # true minutes, not the penalty
            minutes += m
            risk_time += self._edge_risk(self.edge_depth(u, v)) * m
        eta_ticks = minutes / self.params.tick_minutes
        risk = (risk_time / minutes) if minutes > 0 else 0.0
        return RouteResult(True, tuple(path), eta_ticks, min(1.0, risk))
