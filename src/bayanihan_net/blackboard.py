"""The blackboard -- the shared Common Operating Picture (COP) and asset registry.

This is the coordination substrate for the *blackboard* leg of the hybrid design: one
shared situational truth that no single agent owns. It provides exactly the safety
primitives the MAS deck calls for:

* **Idempotency** -- citizen reports of the same event fuse onto one COP incident
  (keyed by :func:`incidents.content_key`), so duplicates never cause duplicate dispatch.
* **Leases + freshness** -- river/road observations carry a posted tick and go *stale*
  after ``lease_ttl_ticks``; committed assets hold a renewable lease so a resource whose
  owning agent dies is reclaimed instead of being silently stranded.
* **Atomic commitment** -- :meth:`Blackboard.try_commit` is the single chokepoint that
  guarantees *no double-commit*: an asset moves out of ``IDLE`` exactly once.
* **Append-only audit** -- every state change is recorded in :attr:`Blackboard.events`,
  the immutable spine the auditor and the evaluation read.

The blackboard stores state and enforces these invariants; the *engine* drives the asset
lifecycle transitions through the mutators here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .config import AssetSpec, AssetType, EnvParams
from .incidents import Incident, IncidentStatus, priority_of, sla_deadline
from .messages import IncidentReportPayload


class AssetStatus(StrEnum):
    """Lifecycle of a response asset on the COP."""

    IDLE = "idle"  # available to bid/commit (after completion an asset re-tasks from the field)
    EN_ROUTE = "en_route"  # committed and travelling (still reversible -> can roll back)
    ON_SCENE = "on_scene"  # working the incident (irreversible commitment)
    DISABLED = "disabled"  # knocked out by a stress injection (agent-kill / comms loss)


@dataclass
class AssetState:
    """Mutable runtime state of one asset. The single source of truth for whether an
    asset is free -- which is what makes :meth:`Blackboard.try_commit` safe."""

    asset_id: str
    asset_type: AssetType
    capacity: int
    node: str  # current location on the network
    owner: str
    status: AssetStatus = AssetStatus.IDLE
    incident_id: str | None = None  # COP key of the incident it is committed to
    task_id: str | None = None
    commit_tick: int | None = None
    lease_until: int | None = None  # commitment lease; reclaimed if not renewed
    dest_node: str | None = None
    arrive_tick: int | None = None  # tick it reaches its destination
    service_done_tick: int | None = None  # tick on-scene work finishes
    route: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        """True iff the asset is idle and free to commit."""
        return self.status is AssetStatus.IDLE


class Blackboard:
    """Shared COP: incident view, asset registry, observation freshness, audit log."""

    def __init__(self, params: EnvParams) -> None:
        self.params = params
        self.incidents: dict[str, Incident] = {}  # COP key -> fused incident
        self.assets: dict[str, AssetState] = {}
        self.events: list[dict[str, Any]] = []  # append-only audit spine
        self._river: tuple[float, int] | None = None  # (level_m, posted_tick)
        self._roads: dict[tuple[str, str], tuple[float, int]] = {}  # edge -> (depth, posted_tick)

    # -- audit ----------------------------------------------------------------------
    def record(self, tick: int, event: str, **fields: Any) -> None:
        """Append one immutable event to the audit log."""
        self.events.append({"tick": tick, "event": event, **fields})

    # -- asset registry -------------------------------------------------------------
    def register_fleet(self, fleet: tuple[AssetSpec, ...]) -> None:
        """Seed the COP with an idle AssetState for every asset in the fleet."""
        for a in fleet:
            self.assets[a.asset_id] = AssetState(
                asset_id=a.asset_id,
                asset_type=a.asset_type,
                capacity=a.capacity,
                node=a.base_node,
                owner=a.owner,
            )

    def idle_assets(self) -> list[AssetState]:
        """Every asset currently idle and available to commit."""
        return [a for a in self.assets.values() if a.status is AssetStatus.IDLE]

    def idle_of_type(self, asset_type: AssetType) -> list[AssetState]:
        """Idle assets of a given type (e.g. boats, medical teams)."""
        return [a for a in self.idle_assets() if a.asset_type is asset_type]

    def count_idle_of_type(self, asset_type: AssetType) -> int:
        """How many assets of a type are idle -- the scarcity signal the reserve gate reads."""
        return len(self.idle_of_type(asset_type))

    # -- atomic commitment (no double-commit) ---------------------------------------
    def try_commit(self, asset_id: str, incident_id: str, task_id: str, tick: int) -> bool:
        """Atomically commit an IDLE asset to a task. Returns ``False`` if the asset was
        already taken -- this is the single guarantee that no asset is double-committed."""
        a = self.assets[asset_id]
        if a.status is not AssetStatus.IDLE:
            return False
        a.status = AssetStatus.EN_ROUTE
        a.incident_id = incident_id
        a.task_id = task_id
        a.commit_tick = tick
        a.lease_until = tick + self.params.lease_ttl_ticks
        self.record(
            tick, "asset_committed", asset_id=asset_id, incident_id=incident_id, task_id=task_id
        )
        return True

    def set_route(
        self, asset_id: str, route: tuple[str, ...], dest_node: str, arrive_tick: int
    ) -> None:
        """Record the planned route, destination, and projected arrival tick for a committed asset."""
        a = self.assets[asset_id]
        a.route = route
        a.dest_node = dest_node
        a.arrive_tick = arrive_tick

    def mark_on_scene(self, asset_id: str, tick: int, service_ticks: int) -> None:
        """Asset has arrived and begun the (now irreversible) on-scene work."""
        a = self.assets[asset_id]
        a.status = AssetStatus.ON_SCENE
        if a.dest_node is not None:
            a.node = a.dest_node
        a.service_done_tick = tick + service_ticks
        self.record(tick, "asset_on_scene", asset_id=asset_id, incident_id=a.incident_id)

    def complete_service(self, asset_id: str, tick: int) -> None:
        """On-scene work is done: the asset returns to IDLE in the field, ready to re-task."""
        a = self.assets[asset_id]
        a.status = AssetStatus.IDLE
        a.incident_id = None
        a.task_id = None
        a.commit_tick = None
        a.lease_until = None
        a.dest_node = None
        a.arrive_tick = None
        a.service_done_tick = None
        a.route = ()
        self.record(tick, "asset_freed", asset_id=asset_id, node=a.node)

    def renew_lease(self, asset_id: str, tick: int) -> None:
        """Owning agent heartbeat: extend the commitment lease one TTL window."""
        self.assets[asset_id].lease_until = tick + self.params.lease_ttl_ticks

    def expired_leases(self, tick: int) -> list[str]:
        """EN_ROUTE assets whose lease lapsed (owner stopped renewing -> presumed dead).

        Only *reversible* (EN_ROUTE) commitments are reclaimable; an ON_SCENE asset is
        mid-irreversible-work and is never yanked."""
        return [
            aid
            for aid, a in self.assets.items()
            if a.status is AssetStatus.EN_ROUTE
            and a.lease_until is not None
            and tick > a.lease_until
        ]

    def release_asset(self, asset_id: str, tick: int, reason: str) -> None:
        """Return an asset to IDLE (rollback of a reversible award, or lease reclaim).

        If its incident was only *assigned* (not yet in progress), the incident reverts
        to OPEN so it can be re-auctioned -- the rollback path the coordinator relies on."""
        a = self.assets[asset_id]
        inc_id = a.incident_id
        a.status = AssetStatus.IDLE
        a.incident_id = None
        a.task_id = None
        a.commit_tick = None
        a.lease_until = None
        a.dest_node = None
        a.arrive_tick = None
        a.route = ()
        inc = self.incidents.get(inc_id) if inc_id else None
        if inc is not None and inc.status is IncidentStatus.ASSIGNED:
            inc.status = IncidentStatus.OPEN
            inc.assigned_asset = None
            inc.assigned_tick = None
        self.record(tick, "asset_released", asset_id=asset_id, reason=reason, incident_id=inc_id)

    def disable_asset(self, asset_id: str, tick: int, reason: str) -> None:
        """Stress injection: knock an asset out of service (its in-flight commitment, if
        reversible, is released first)."""
        a = self.assets[asset_id]
        if a.status is AssetStatus.EN_ROUTE:
            self.release_asset(asset_id, tick, reason=f"disabled:{reason}")
        a.status = AssetStatus.DISABLED
        self.record(tick, "asset_disabled", asset_id=asset_id, reason=reason)

    # -- incident view (idempotent ingest) ------------------------------------------
    def ingest_report(
        self, payload: IncidentReportPayload, key: str, tick: int
    ) -> tuple[Incident, bool]:
        """Fuse a citizen report into the COP. Returns ``(incident, created)`` where
        ``created`` is ``False`` for a deduplicated re-report."""
        inc = self.incidents.get(key)
        if inc is None:
            deadline = sla_deadline(tick, priority_of(payload.severity), self.params)
            inc = Incident(
                incident_id=key,
                ground_truth_id=payload.incident_id,
                node=payload.node,
                barangay=payload.barangay,
                itype=payload.itype,
                people=payload.people,
                severity=payload.severity,
                reported_tick=tick,
                deadline_tick=deadline,
                suspected_false=payload.is_suspected_false,
                last_report_tick=tick,
                report_count=1,
            )
            self.incidents[key] = inc
            self.record(
                tick,
                "incident_opened",
                incident_id=key,
                node=payload.node,
                itype=payload.itype.value,
                severity=payload.severity,
                priority=inc.priority.value,
            )
            return inc, True
        # duplicate -> fuse (keep the worst-case people/severity; refresh timestamp)
        inc.report_count += 1
        inc.last_report_tick = tick
        inc.people = max(inc.people, payload.people)
        inc.severity = max(inc.severity, payload.severity)
        # corroboration reduces suspicion: a second independent report is evidence of truth
        if not payload.is_suspected_false:
            inc.suspected_false = False
        self.record(tick, "incident_fused", incident_id=key, report_count=inc.report_count)
        return inc, False

    def open_incidents(self) -> list[Incident]:
        """Incidents awaiting allocation, worst-first (severity, then earliest deadline)."""
        opens = [i for i in self.incidents.values() if i.status is IncidentStatus.OPEN]
        return sorted(opens, key=lambda i: (-i.severity, i.deadline_tick))

    # -- observation freshness (leases) ---------------------------------------------
    def post_river(self, river_m: float, tick: int) -> None:
        """Post a river-level observation with its freshness tick (subject to the COP lease)."""
        self._river = (river_m, tick)

    def latest_river(self, now: int) -> tuple[float | None, bool]:
        """Latest river level and whether it is *stale* (older than the freshness lease)."""
        if self._river is None:
            return None, True
        val, posted = self._river
        return val, (now - posted) > self.params.lease_ttl_ticks

    def post_road(self, edge: tuple[str, str], depth_m: float, tick: int) -> None:
        """Post a flood-depth observation for one road edge with its freshness tick."""
        self._roads[edge] = (depth_m, tick)

    def latest_road(self, edge: tuple[str, str], now: int) -> tuple[float | None, bool]:
        """Latest flood depth for an edge and whether it is *stale* (older than the lease)."""
        obs = self._roads.get(edge)
        if obs is None:
            return None, True
        val, posted = obs
        return val, (now - posted) > self.params.lease_ttl_ticks
