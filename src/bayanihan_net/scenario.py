"""The seeded scenario generator -- the ground truth the agents only partially observe.

:class:`World` owns the river hydrograph, the (true) flood state of the road network, and
the stream of citizen incident reports. Each tick it emits :class:`RawReport` objects:
genuine incidents, *duplicate* re-reports of the same event (which the triage layer must
deduplicate), and -- when the corresponding stress knob is on -- fabricated **Sybil**
reports (which the system must avoid serving). Everything is driven by one seeded
``numpy`` generator, so a given seed reproduces the entire disaster exactly.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import IncidentType, Scenario
from .incidents import (
    Incident,
    IncidentStatus,
    content_key,
    priority_of,
    severity,
    sla_deadline,
)
from .network import RoadNetwork, local_flood_intensity


@dataclass
class RawReport:
    """A single citizen report as it arrives off the wire (pre-deduplication)."""

    report_id: str
    ground_truth_id: str
    node: str
    barangay: str
    itype: IncidentType
    people: int
    severity: float
    reported_tick: int
    is_duplicate: bool
    is_false: bool


# Incident-type mix during an urban flood (rescue dominates, then relief, then medical).
_TYPE_MIX: tuple[tuple[IncidentType, float], ...] = (
    (IncidentType.RESCUE, 0.55),
    (IncidentType.RELIEF, 0.30),
    (IncidentType.MEDICAL, 0.15),
)


class World:
    """Ground-truth disaster process. Deterministic given a seed."""

    def __init__(self, scenario: Scenario, seed: int) -> None:
        self.scenario = scenario
        self.params = scenario.params
        self.rng = np.random.default_rng(seed)
        self.network = RoadNetwork(scenario.edges, scenario.params)
        self.ground_truth: dict[str, Incident] = {}  # all genuine incidents (for scoring)
        self.false_keys: set[str] = set()  # COP keys of fabricated reports (Sybil scoring)
        self._counter = 0
        self._prev_river = scenario.params.river_start_m

    # -- hydrology -------------------------------------------------------------------
    def true_river_m(self, tick: int) -> float:
        """Piecewise-linear hydrograph: rise to the peak, then recede toward 1st alarm."""
        p = self.params
        if tick <= 0:  # clamp pre-start ticks so river_rising_rate(0) is a true 0, not an artifact
            return p.river_start_m
        if tick <= p.river_peak_tick:
            frac = tick / max(1, p.river_peak_tick)
            return p.river_start_m + frac * (p.river_peak_m - p.river_start_m)
        # recession over the remaining window down to just under 1st alarm
        span = max(1, p.horizon_ticks - p.river_peak_tick)
        frac = (tick - p.river_peak_tick) / span
        end_m = p.alarm1_m - 0.5
        return p.river_peak_m + frac * (end_m - p.river_peak_m)

    def river_rising_rate(self, tick: int) -> float:
        """Change in true river level over the previous tick (metres/tick; negative once falling)."""
        return self.true_river_m(tick) - self.true_river_m(tick - 1)

    # -- demand generation -----------------------------------------------------------
    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter:04d}"

    def _local_intensity(self, river_m: float, b_node: str) -> float:
        """How active a barangay's incident process is, given the flood depth there."""
        bgy = self.scenario.barangay_by_node(b_node)
        if bgy is None:
            return 0.0
        return local_flood_intensity(river_m, bgy.flood_sensitivity, self.params)

    def _draw_type(self) -> IncidentType:
        r = self.rng.random()
        acc = 0.0
        for t, w in _TYPE_MIX:
            acc += w
            if r <= acc:
                return t
        return IncidentType.RESCUE

    def _make_genuine(self, tick: int, b_node: str, river_m: float) -> Incident:
        bgy = self.scenario.barangay_by_node(b_node)
        assert bgy is not None
        itype = self._draw_type()
        # people affected scales with the barangay's at-risk population and the flood.
        intensity = self._local_intensity(river_m, b_node)
        mean_people = 4 + 0.010 * bgy.population_at_risk * bgy.vulnerability * intensity
        people = int(max(1, self.rng.poisson(mean_people)))
        sev = severity(
            people=people,
            barangay=bgy,
            itype=itype,
            river_rising_rate=self.river_rising_rate(tick),
        )
        gt_id = self._next_id("GT")
        inc = Incident(
            incident_id=gt_id,
            ground_truth_id=gt_id,
            node=b_node,
            barangay=bgy.name,
            itype=itype,
            people=people,
            severity=sev,
            reported_tick=tick,
            deadline_tick=sla_deadline(tick, priority_of(sev), self.params),
            last_report_tick=tick,
        )
        self.ground_truth[gt_id] = inc
        return inc

    def step(self, tick: int) -> list[RawReport]:
        """Advance the world one tick: update flood, emit this tick's citizen reports."""
        river_m = self.true_river_m(tick)
        self.network.update_flood(river_m)
        reports: list[RawReport] = []
        p = self.params

        for bgy in self.scenario.barangays:
            # genuine incidents are driven only by the flood -- NOT by the report-storm knob,
            # which inflates re-reports of the *same* events (message volume), not real demand.
            mean = p.base_report_rate * self._local_intensity(river_m, bgy.node)
            n_new = int(self.rng.poisson(mean)) if mean > 0 else 0
            for _ in range(n_new):
                inc = self._make_genuine(tick, bgy.node, river_m)
                reports.append(
                    RawReport(
                        report_id=self._next_id("R"),
                        ground_truth_id=inc.ground_truth_id,
                        node=inc.node,
                        barangay=inc.barangay,
                        itype=inc.itype,
                        people=inc.people,
                        severity=inc.severity,
                        reported_tick=tick,
                        is_duplicate=False,
                        is_false=False,
                    )
                )
                # other residents re-report the SAME incident; a report storm multiplies how
                # many duplicates arrive (the triage dedup must absorb them without dispatching
                # twice). Mean ~= duplicate_prob at baseline; scaled by the storm multiplier.
                n_dupes = int(self.rng.poisson(p.duplicate_prob * p.report_storm_multiplier))
                for _ in range(n_dupes):
                    jitter = max(1, inc.people + int(self.rng.integers(-3, 4)))
                    reports.append(
                        RawReport(
                            report_id=self._next_id("R"),
                            ground_truth_id=inc.ground_truth_id,
                            node=inc.node,
                            barangay=inc.barangay,
                            itype=inc.itype,
                            people=jitter,
                            severity=inc.severity,
                            reported_tick=tick,
                            is_duplicate=True,
                            is_false=False,
                        )
                    )

            # fabricated (Sybil) reports -- only when the stress knob is on
            if p.sybil_false_rate > 0 and self.rng.random() < p.sybil_false_rate:
                fake_type = self._draw_type()
                fake_people = int(self.rng.integers(20, 90))
                gt_id = self._next_id("FALSE")
                self.false_keys.add(content_key(bgy.node, fake_type, tick, p.dedup_window_ticks))
                reports.append(
                    RawReport(
                        report_id=self._next_id("R"),
                        ground_truth_id=gt_id,
                        node=bgy.node,
                        barangay=bgy.name,
                        itype=fake_type,
                        people=fake_people,
                        severity=min(1.0, 0.6 + fake_people / 200.0),
                        reported_tick=tick,
                        is_duplicate=False,
                        is_false=True,
                    )
                )

        self._prev_river = river_m
        return reports

    # -- scoring helpers -------------------------------------------------------------
    def total_genuine_need(self) -> dict[str, int]:
        """People-in-need per barangay across all genuine incidents (the denominator)."""
        need: dict[str, int] = {}
        for inc in self.ground_truth.values():
            if not inc.is_false:
                need[inc.barangay] = need.get(inc.barangay, 0) + inc.people
        return need

    def unresolved_genuine(self) -> list[Incident]:
        """Genuine (non-Sybil) incidents not yet resolved -- the true outstanding demand the
        run is scored against (false reports are excluded)."""
        return [
            inc
            for inc in self.ground_truth.values()
            if not inc.is_false and inc.status is not IncidentStatus.RESOLVED
        ]
