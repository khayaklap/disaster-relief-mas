"""Configuration: domain enums, the seeded Marikina scenario, and all tunable
parameters as frozen dataclasses.

All numbers here are **illustrative, not authoritative**. The Marikina River alarm
thresholds (1st/2nd/3rd alarm), barangay populations, the road network, and the asset
fleet are plausible stand-ins chosen so realistic edge cases fall out -- they are *not*
official PAGASA / MMDA / Marikina LGU figures. A real deployment would source live
feeds (see ``interop/`` for the boundary). This mirrors the prior assignments'
"illustrative not authoritative" fixture stance.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

# Single global seed for the whole reproducible pipeline (the date this was built).
SEED: int = 20260620


class AssetType(StrEnum):
    """Kinds of scarce response assets the system allocates."""

    BOAT = "boat"  # rescue boat: traverses flooded roads, slow, small capacity
    TRUCK = "truck"  # transport truck: fast, large capacity, blocked by deep water
    MEDICAL = "medical_team"  # medical team: treats + transports casualties


class TravelMode(StrEnum):
    """How an asset traverses the road network (decides which edges are passable)."""

    BOAT = "boat"  # can cross flooded edges up to ``boat_max_depth_m``
    ROAD = "road"  # blocked once edge flood depth exceeds ``road_impassable_depth_m``


# Which travel mode each asset type uses on the network.
ASSET_TRAVEL_MODE: dict[AssetType, TravelMode] = {
    AssetType.BOAT: TravelMode.BOAT,
    AssetType.TRUCK: TravelMode.ROAD,
    AssetType.MEDICAL: TravelMode.ROAD,  # medical teams ride trucks/ambulances
}


class IncidentType(StrEnum):
    """Categories of demand the system must serve."""

    RESCUE = "rescue"  # stranded residents needing evacuation (boat-capable)
    MEDICAL = "medical"  # injured/sick needing a medical team
    RELIEF = "relief"  # food/water/relief-goods delivery


# Which asset types can serve which incident type (capability matching for bids).
CAPABILITY: dict[IncidentType, tuple[AssetType, ...]] = {
    IncidentType.RESCUE: (AssetType.BOAT, AssetType.TRUCK),
    IncidentType.MEDICAL: (AssetType.MEDICAL,),
    IncidentType.RELIEF: (AssetType.TRUCK, AssetType.BOAT),
}


@dataclass(frozen=True)
class BarangaySpec:
    """A flood-exposed community (a demand node on the network)."""

    name: str
    node: str  # network node id
    population_at_risk: int
    vulnerability: float  # 0..1: fraction of pop likely to need help at peak flood
    flood_sensitivity: float  # 0..1: how fast local streets flood vs river level
    riverside: bool  # riverside barangays flood earliest and deepest


@dataclass(frozen=True)
class AssetSpec:
    """A single response asset in the fleet."""

    asset_id: str
    asset_type: AssetType
    capacity: int  # people (BOAT/TRUCK/MEDICAL) or relief units carried
    base_node: str  # staging location it starts and returns to
    owner: str  # logistics agent id that holds and bids it


@dataclass(frozen=True)
class EdgeSpec:
    """A road segment. ``base_minutes`` is dry travel time; ``exposure`` (0..1) scales
    how deep this segment floods relative to the river level."""

    u: str
    v: str
    base_minutes: float
    exposure: float


@dataclass(frozen=True)
class EnvParams:
    """Physics, economics, timing, and governance thresholds for the simulation."""

    # --- timing ---
    horizon_ticks: int = 48  # 48 ticks * 15 min = 12 h response window
    tick_minutes: int = 15
    service_ticks: int = 1  # on-scene work duration before an asset is freed

    # --- river hydrograph (metres at the Sto. Nino gauge; illustrative) ---
    river_start_m: float = 13.0
    river_peak_m: float = 19.2
    river_peak_tick: int = 22  # rises to peak, then recedes
    alarm1_m: float = 15.0  # 1st alarm  (illustrative)
    alarm2_m: float = 16.0  # 2nd alarm
    alarm3_m: float = 18.0  # 3rd alarm -> forced-evacuation regime

    # --- street flooding (depth in metres on an edge/barangay) ---
    # local_depth = max(0, (river_m - flood_onset_m)) * exposure
    flood_onset_m: float = 14.0
    road_impassable_depth_m: float = 0.6  # trucks blocked above this
    boat_max_depth_m: float = 5.5  # boats fail above this (debris/current); riverside stays
    #                                boat-reachable at peak, so boats are the flood asset and
    #                                trucks/medical (ROAD) get cut off -- the core asset tension

    # --- demand generation ---
    base_report_rate: float = (
        0.22  # expected new genuine incidents per active barangay/tick at peak
    )
    duplicate_prob: float = 0.35  # a genuine incident is re-reported (tests triage dedup)
    dedup_window_ticks: int = (
        2  # reports of the same node+type within this window fuse into one COP incident
    )
    # stress knobs (off by default; turned on by named stress scenarios)
    report_storm_multiplier: float = 1.0
    sybil_false_rate: float = 0.0  # fraction of reports that are fabricated
    comms_drop_prob: float = 0.0  # message loss probability (comms degradation)
    agent_kill_fraction: float = 0.0  # fraction of the fleet disabled at peak (asset loss)
    forecast_outage_at_peak: bool = False  # knock out the river gauge -> stale COP / misroute

    # --- service-level targets (ticks to first dispatch, by priority) ---
    sla_critical_ticks: int = 2
    sla_high_ticks: int = 4
    sla_medium_ticks: int = 8

    # --- governance / safety thresholds ---
    # An award is HITL-gated (irreversible/high-stakes) when ANY of these hold:
    last_asset_of_type_gate: bool = True  # committing the last ready unit of a scarce type
    reserve_protect_fleet: int = 2  # only gate "last unit" for types this small (e.g. medical)
    large_commit_people: int = 15  # committing to an incident with >= this many people at risk
    forced_evacuation_alarm_m: float = 18.0  # ordering evacuation at 3rd alarm
    # Fairness: weight on the unmet-need deficit in the triage queue ordering (0 = none).
    # A fully-neglected barangay (deficit 1) gets its severity multiplied by (1+weight),
    # so it can jump ahead of an equally-severe but better-covered barangay.
    fairness_weight: float = 1.5
    # Blackboard freshness: an observation older than this many ticks is "stale". This TTL is
    # also the anti-oscillation lever: a committed asset is only reclaimable after its lease
    # lapses, so there is no rapid release/re-grab loop (idle-only bidding does the rest).
    lease_ttl_ticks: int = 3
    # Emergence guardrail threshold used by escalation.should_auto_rollback -- a tested pure
    # hook that is NOT wired into the live loop (no Gini-triggered rollback fires at runtime).
    gini_rollback_threshold: float = 0.55


# --- The Marikina scenario fixture (illustrative geography) ---------------------------
# Nodes: 6 flood-exposed barangays + 2 staging/care nodes + 2 junctions.
#   T Tumana, M Malanday, N Nangka, P Provident, I Industrial Valley, C Concepcion Uno
#   SPORTS  = Marikina Sports Center (evacuation hub + asset base)
#   HOSP    = Amang Rodriguez Memorial Medical Center (medical destination)
#   J1, J2  = road junctions

_BARANGAYS: tuple[BarangaySpec, ...] = (
    BarangaySpec("Tumana", "T", 1200, 0.82, 0.95, riverside=True),
    BarangaySpec("Malanday", "M", 1000, 0.78, 0.90, riverside=True),
    BarangaySpec("Nangka", "N", 900, 0.74, 0.85, riverside=True),
    BarangaySpec("Provident", "P", 800, 0.70, 0.80, riverside=True),
    BarangaySpec("IndustrialValley", "I", 700, 0.60, 0.65, riverside=False),
    BarangaySpec("Concepcion", "C", 500, 0.45, 0.45, riverside=False),
)

_STAGING_NODES: tuple[str, ...] = ("SPORTS", "HOSP")

_EDGES: tuple[EdgeSpec, ...] = (
    # riverside corridor (high exposure -> floods first)
    EdgeSpec("SPORTS", "J1", 6.0, 0.20),
    EdgeSpec("J1", "T", 7.0, 0.95),
    EdgeSpec("J1", "M", 8.0, 0.90),
    EdgeSpec("T", "M", 5.0, 0.92),
    EdgeSpec("M", "N", 6.0, 0.88),
    EdgeSpec("N", "P", 7.0, 0.85),
    EdgeSpec("J1", "J2", 5.0, 0.40),
    EdgeSpec("J2", "I", 6.0, 0.60),
    EdgeSpec("J2", "C", 9.0, 0.40),
    EdgeSpec("P", "J2", 8.0, 0.70),
    EdgeSpec("SPORTS", "HOSP", 10.0, 0.15),
    EdgeSpec("J2", "HOSP", 7.0, 0.25),
    # a higher-ground bypass (low exposure -> a flood-robust alternative route)
    EdgeSpec("SPORTS", "C", 14.0, 0.20),
    EdgeSpec("C", "I", 8.0, 0.35),
)

_FLEET: tuple[AssetSpec, ...] = (
    # 4 boats, 3 trucks, 2 medical teams -- deliberately scarce vs >5000 people at risk.
    AssetSpec("BOAT-1", AssetType.BOAT, 10, "SPORTS", owner="logistics-rescue"),
    AssetSpec("BOAT-2", AssetType.BOAT, 10, "SPORTS", owner="logistics-rescue"),
    AssetSpec("BOAT-3", AssetType.BOAT, 8, "SPORTS", owner="logistics-rescue"),
    AssetSpec("BOAT-4", AssetType.BOAT, 8, "SPORTS", owner="logistics-rescue"),
    AssetSpec("TRUCK-1", AssetType.TRUCK, 22, "SPORTS", owner="logistics-relief"),
    AssetSpec("TRUCK-2", AssetType.TRUCK, 20, "SPORTS", owner="logistics-relief"),
    AssetSpec("TRUCK-3", AssetType.TRUCK, 18, "SPORTS", owner="logistics-relief"),
    AssetSpec("MED-1", AssetType.MEDICAL, 6, "HOSP", owner="logistics-medical"),
    AssetSpec("MED-2", AssetType.MEDICAL, 6, "HOSP", owner="logistics-medical"),
)


@dataclass(frozen=True)
class Scenario:
    """A complete, named scenario: geography + fleet + parameters."""

    name: str
    params: EnvParams
    barangays: tuple[BarangaySpec, ...] = _BARANGAYS
    staging_nodes: tuple[str, ...] = _STAGING_NODES
    edges: tuple[EdgeSpec, ...] = _EDGES
    fleet: tuple[AssetSpec, ...] = _FLEET

    @property
    def nodes(self) -> tuple[str, ...]:
        """Every graph node id: one per barangay, the staging areas, and the routing junctions."""
        bgy = tuple(b.node for b in self.barangays)
        junctions = ("J1", "J2")
        return bgy + self.staging_nodes + junctions

    def barangay_by_node(self, node: str) -> BarangaySpec | None:
        """The barangay sited at a node, or None for a staging/junction node."""
        return next((b for b in self.barangays if b.node == node), None)


@dataclass(frozen=True)
class Config:
    """Top-level run configuration."""

    seed: int = SEED
    scenario: Scenario = field(default_factory=lambda: Scenario("marikina_typhoon", EnvParams()))


def default_config() -> Config:
    """The canonical Marikina typhoon-flood configuration."""
    return Config()


def config_with(seed: int = SEED, **param_overrides: Any) -> Config:
    """A Marikina config at a given seed, with optional stress-knob overrides applied to
    :class:`EnvParams` (e.g. ``sybil_false_rate=0.2``). Shared by the CLI, the baselines,
    and the stress battery so every run is built the same, reproducible way.

    Unknown override keys raise a clear error rather than a confusing ``dataclasses`` traceback.
    """
    unknown = set(param_overrides) - set(EnvParams.__dataclass_fields__)
    if unknown:
        raise ValueError(f"unknown EnvParams override(s): {sorted(unknown)}")
    params = replace(EnvParams(), **param_overrides) if param_overrides else EnvParams()
    return Config(seed=seed, scenario=Scenario("marikina_typhoon", params))
