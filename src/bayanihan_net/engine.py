"""The discrete-event engine -- the orchestrator that runs the whole MAS.

Each tick advances the seeded world, lets the forecast and sensing agents refresh the COP,
runs triage -> contract-net auction -> coordinator award (with the HITL gate) over the
open incidents, then moves committed assets along their routes and services arrivals. It
validates every planned route against the *true* flood at arrival -- so a stale-COP
misroute strands an asset rather than teleporting it -- and it reclaims the leases of
assets whose owners go dark. The same loop powers the baselines and the stress tests: the
winner-selector, the fairness term, the governance gate, and the stress knobs are all
injected, so every comparison runs through identical machinery.

``run()`` returns a fully provenance-stamped :class:`RunReport` -- the evidence artifact.
"""

from __future__ import annotations

import platform
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from .agents.auditor import Auditor
from .agents.coordinator import CoordinatorAgent
from .agents.forecast import ForecastAgent
from .agents.logistics import LogisticsAgent
from .agents.medical import MedicalAgent
from .agents.routing import RoutingAgent
from .agents.sensing import SensingAgent
from .agents.triage import TriageAgent
from .blackboard import AssetState, AssetStatus, Blackboard
from .config import ASSET_TRAVEL_MODE, Config, default_config
from .coordination.blackboard_bus import BlackboardBus
from .coordination.contract_net import ScoredBid, select_winner
from .coordination.escalation import HumanApprover
from .governance.metrics import TickSignals, gini
from .incidents import Incident, IncidentStatus
from .interop.a2a import A2AClient, default_mutual_aid_network
from .interop.mcp_tools import MCPRegistry, default_registry
from .messages import Envelope, MsgType, TaskAnnouncePayload
from .provenance import env_fingerprint, library_versions
from .scenario import World

# A winner-selection strategy over scored bids (hybrid welfare default; baselines inject others).
Selector = Callable[[list[ScoredBid]], "ScoredBid | None"]


@dataclass
class RunReport:
    """The provenance-stamped result of one simulation run -- the evidence artifact."""

    run_id: str
    provenance: dict[str, Any]
    outcome: dict[str, Any]  # system-level evaluation
    emergence: dict[str, Any]  # emergence + human-level (HITL) evaluation
    quality: dict[str, Any]  # agent-level + interaction-level evaluation
    golden_rows: list[dict[str, float]]
    events: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        """The JSON-serializable evidence payload (provenance + four-level evaluation + signals)."""
        return {
            "run_id": self.run_id,
            "provenance": self.provenance,
            # the four-level evaluation grid, each level emitting real numbers
            "evaluation": {
                "agent_level": self.quality["agent_level"],
                "interaction_level": self.quality["interaction_level"],
                "system_level": self.outcome,
                "human_and_emergence_level": self.emergence,
            },
            "golden_signals": self.golden_rows,
            "event_count": len(self.events),
        }

    def summary_row(self) -> dict[str, Any]:
        """A flat row for results.csv / cross-run comparison."""
        return {
            "run_id": self.run_id,
            "policy": self.provenance["policy"],
            "fairness": self.provenance["use_fairness"],
            "governance": self.provenance["use_governance"],
            "seed": self.provenance["seed"],
            **{
                k: self.outcome[k]
                for k in (
                    "served_fraction",
                    "severity_weighted_served_fraction",
                    "sla_compliance",
                    "coverage_gini",
                    "min_served_fraction",
                    "gini_unmet",
                    "total_served_people",
                    "total_need_people",
                    "served_late",
                    "sybil_served",
                    "sybil_suppressed",
                )
            },
            **{
                k: self.emergence[k]
                for k in (
                    "reassignment_rate",
                    "escalations",
                    "hitl_approvals",
                    "messages_dropped",
                )
            },
        }


class Engine:
    """Runs one scenario end-to-end under a chosen coordination policy."""

    def __init__(
        self,
        config: Config | None = None,
        *,
        policy_name: str = "hybrid",
        use_fairness: bool = False,
        use_governance: bool = True,
        select: Selector = select_winner,
        order_mode: str = "severity",
        enable_interop: bool = True,
        run_id: str | None = None,
    ) -> None:
        self.config = config or default_config()
        self.scenario = self.config.scenario
        self.params = self.scenario.params
        self.seed = self.config.seed
        self.policy_name = policy_name
        self.use_fairness = use_fairness
        self.use_governance = use_governance
        self.select = select
        # how the scarce-asset queue is ordered each tick (the triage/equity lever):
        # "fairness" (severity x unmet-deficit) | "severity" | "fifo" (no triage) | "random"
        self.order_mode = order_mode

        self.world = World(self.scenario, self.seed)
        self.bb = Blackboard(self.params)
        self.bb.register_fleet(self.scenario.fleet)
        # engine-side RNG (comms-drop / kill choices) is seed-derived but distinct from the
        # world's, so injecting stress never perturbs the underlying disaster realization.
        self.rng = np.random.default_rng(self.seed + 7)
        self.bus = BlackboardBus(self.bb, rng=self.rng)

        self.forecast = ForecastAgent("hydromet", self.scenario)
        self.routing = RoutingAgent("routing", self.scenario)
        self.triage = TriageAgent("triage", self.scenario)
        self.coordinator = CoordinatorAgent("coordinator", self.scenario)
        self.auditor = Auditor("auditor", self.scenario)
        self.approver = HumanApprover()
        self.sensing = self._make_scouts()
        self.logistics: list[LogisticsAgent] = [
            LogisticsAgent("logistics-rescue", self.scenario, "logistics-rescue"),
            LogisticsAgent("logistics-relief", self.scenario, "logistics-relief"),
        ]
        self.medical = MedicalAgent("logistics-medical", self.scenario, "logistics-medical")
        self._bidders: list[LogisticsAgent] = [*self.logistics, self.medical]

        # interoperability boundaries (mocked upstreams; real scope/trust gates + tracing)
        self.enable_interop = enable_interop
        self.mcp: MCPRegistry = default_registry()
        self.a2a: A2AClient = default_mutual_aid_network()
        self._a2a_requested = False

        self.run_id = run_id or f"{self.scenario.name}-{policy_name}-seed{self.seed}"
        self._samples: list[TickSignals] = []
        # cumulative counters, derived from the message log as it is published
        self._n_commitments = 0
        self._n_escalations = 0
        self._n_rollbacks = 0
        self._n_approvals = 0
        self._n_denied = 0
        self._asset_ticks = 0  # cumulative asset-ticks (cost golden signal)

    def _make_scouts(self) -> list[SensingAgent]:
        """Two scouts split the barangays -- partial, non-overlapping coverage of the COP."""
        bgys = self.scenario.barangays
        half = (len(bgys) + 1) // 2
        return [
            SensingAgent("scout-river", self.scenario, [b.node for b in bgys[:half]]),
            SensingAgent("scout-upland", self.scenario, [b.node for b in bgys[half:]]),
        ]

    # the main loop
    def run(self) -> RunReport:
        """Run the full scenario tick by tick and return the provenance-stamped report."""
        peak = self.params.river_peak_tick
        for tick in range(self.params.horizon_ticks):
            true_river = self.world.true_river_m(tick)
            raw_reports = self.world.step(tick)

            # stress injections fire once, at the flood peak
            if tick == peak:
                self._apply_peak_stress(tick)

            # forecast refreshes the COP river (unless the gauge is out), sourced through
            # the policy-gated PAGASA MCP boundary so the hop is scoped and traced
            if self.forecast.alive:
                river_obs = self._mcp_river(true_river, tick)
                self._emit(self.forecast.observe(river_obs, tick), tick)
            cop_river, _stale = self.bb.latest_river(tick)
            self.routing.update_belief(cop_river)

            # sensing: scouts post incident reports for their barangays
            for scout in self.sensing:
                if scout.alive:
                    self._emit(
                        scout.observe(raw_reports, cop_river, tick),
                        tick,
                        drop_prob=self.params.comms_drop_prob,
                    )

            # medical mass-casualty watch -> on first overload, request A2A mutual aid
            if self.medical.alive:
                med_escalations = self.medical.mass_casualty_check(self.bb, tick)
                self._emit(med_escalations, tick)
                if med_escalations and self.enable_interop and not self._a2a_requested:
                    self._request_mutual_aid(tick, med_escalations[0])

            # reclaim leases of assets whose owners went dark, then allocate
            self._reclaim_expired_leases(tick)
            idle_now = len(self.bb.idle_assets())
            tasks = self.triage.plan(self.bb, tick, max_tasks=max(1, idle_now))
            self._emit(tasks, tick, drop_prob=self.params.comms_drop_prob)
            self._allocate(tasks, tick, cop_river)

            # move assets, service arrivals, keep live leases fresh
            self._advance_assets(tick)
            self._renew_leases(tick)
            self._sample(tick, true_river)

        return self._build_report()

    # allocation
    def _allocate(self, tasks: list[Envelope], tick: int, cop_river: float | None) -> None:
        cop = self.bb.incidents
        ordered = self._order_tasks(tasks, cop)
        for task_env in ordered:
            task = task_env.typed_payload()
            assert isinstance(task, TaskAnnouncePayload)
            incident = cop.get(task.incident_id)
            if incident is None or incident.status is not IncidentStatus.OPEN:
                continue
            bids: list[Envelope] = []
            for agent in self._bidders:
                if agent.alive:
                    bids.extend(agent.bid(task, self.bb, self.routing, tick))
            # No capable asset is free right now: defer to a later tick (normal backpressure,
            # not an exception). Escalation is reserved for genuinely unreachable demand --
            # i.e. bids exist but every route is infeasible -- which decide_award detects.
            if not bids:
                continue
            self._emit(bids, tick)  # bids are logged on the bus for the audit trail
            outcome = self.coordinator.decide_award(
                task=task,
                incident=incident,
                bid_envs=bids,
                bb=self.bb,
                routing=self.routing,
                cop_river_m=cop_river,
                tick=tick,
                approver=self.approver,
                select=self.select,
                gate_enabled=self.use_governance,
            )
            self._emit(outcome.envelopes, tick)

    def _order_tasks(self, tasks: list[Envelope], cop: dict[str, Incident]) -> list[Envelope]:
        """Order the scarce-asset queue by the active policy's ordering strategy. This is
        the seat of the triage/equity tradeoff that distinguishes the policies under load."""
        incs = [(t, cop.get(_incident_id_of(t))) for t in tasks]
        if self.order_mode == "fifo":  # no triage: answer calls in the order they arrived
            incs.sort(
                key=lambda ti: (
                    ti[1].reported_tick if ti[1] else 1 << 30,
                    ti[1].incident_id if ti[1] else "",
                )
            )
            return [t for t, _ in incs]
        if self.order_mode == "random":  # the comparison floor
            stable = sorted(tasks, key=_incident_id_of)
            idx = list(range(len(stable)))
            self.rng.shuffle(idx)
            return [stable[i] for i in idx]
        # severity- or fairness-weighted (worst first)
        incs.sort(key=lambda ti: -self._task_priority(ti[1]))
        return [t for t, _ in incs]

    def _task_priority(self, incident: Incident | None) -> float:
        """Severity, lifted by the barangay's unmet-need deficit when fairness is on."""
        if incident is None:
            return -1.0
        if not self.use_fairness or self.order_mode != "fairness":
            return incident.severity
        deficit = self._equity_deficit(incident.barangay)
        return incident.severity * (1.0 + self.params.fairness_weight * deficit)

    def _equity_deficit(self, barangay: str) -> float:
        """How under-served a barangay is so far (0 = fully served, 1 = nothing delivered)."""
        need = served = 0
        for inc in self.bb.incidents.values():
            if inc.barangay != barangay or inc.suspected_false:
                continue
            need += inc.people
            if inc.status is IncidentStatus.RESOLVED:
                served += inc.people
        if need <= 0:
            return 0.0
        return max(0.0, min(1.0, 1.0 - served / need))

    # asset movement & lifecycle
    def _advance_assets(self, tick: int) -> None:
        for a in list(self.bb.assets.values()):
            if (
                a.status is AssetStatus.EN_ROUTE
                and a.arrive_tick is not None
                and tick >= a.arrive_tick
            ):
                mode = ASSET_TRAVEL_MODE[a.asset_type]
                if self.world.network.path_passable(a.route, mode):
                    self.bb.mark_on_scene(a.asset_id, tick, self.params.service_ticks)
                    inc = self.bb.incidents.get(a.incident_id) if a.incident_id else None
                    if inc is not None:
                        inc.status = IncidentStatus.IN_PROGRESS
                        inc.on_scene_tick = tick
                else:
                    self._handle_misroute(a, tick)
            elif (
                a.status is AssetStatus.ON_SCENE
                and a.service_done_tick is not None
                and tick >= a.service_done_tick
            ):
                inc = self.bb.incidents.get(a.incident_id) if a.incident_id else None
                if inc is not None:
                    inc.status = IncidentStatus.RESOLVED
                    inc.resolved_tick = tick
                    # one dispatch serves up to the asset's capacity; any remainder is
                    # unmet (not re-queued -- a documented simplification, see REFLECTION)
                    inc.served_people = min(a.capacity, inc.people)
                self.bb.complete_service(a.asset_id, tick)

    def _handle_misroute(self, asset: AssetState, tick: int) -> None:
        """A planned route failed against the true flood: re-plan on truth, else strand."""
        mode = ASSET_TRAVEL_MODE[asset.asset_type]
        dest = asset.dest_node
        true_route = self.world.network.route(asset.node, dest, mode) if dest else None
        if true_route is not None and true_route.feasible:
            asset.route = true_route.path
            asset.arrive_tick = tick + max(1, round(true_route.eta_ticks))
            self.bb.record(
                tick,
                "reroute_after_misroute",
                asset_id=asset.asset_id,
                incident_id=asset.incident_id,
            )
        else:
            inc = self.bb.incidents.get(asset.incident_id) if asset.incident_id else None
            self.bb.release_asset(asset.asset_id, tick, reason="stranded_no_true_path")
            if inc is not None and tick > inc.deadline_tick and inc.status is IncidentStatus.OPEN:
                inc.status = IncidentStatus.FAILED
            self.bb.record(tick, "stranded_release", asset_id=asset.asset_id)

    def _renew_leases(self, tick: int) -> None:
        for a in self.bb.assets.values():
            if a.status is AssetStatus.EN_ROUTE and self._owner_alive(a.owner):
                self.bb.renew_lease(a.asset_id, tick)

    def _reclaim_expired_leases(self, tick: int) -> None:
        for aid in self.bb.expired_leases(tick):
            self._emit(self.coordinator.rollback_award(aid, self.bb, tick, "lease_expired"), tick)

    def _owner_alive(self, owner: str) -> bool:
        for agent in self._bidders:
            if agent.owner == owner:
                return agent.alive
        return True

    # interoperability
    def _mcp_river(self, true_river: float, tick: int) -> float:
        """Source the river reading via the scoped PAGASA MCP tool (echo mock). Falls back
        to the direct reading if interop is disabled; logs the traced hop either way."""
        if not self.enable_interop:
            return true_river
        result = self.mcp.get("pagasa.river").call(
            self.forecast.security_context, trace_id=f"river:{tick}", river_m=true_river
        )
        self.bb.record(tick, "mcp_call", tool="pagasa.river", ok=result.ok, trace=result.trace_id)
        if result.ok and result.data is not None:
            return float(result.data["river_m"])
        return true_river

    def _request_mutual_aid(self, tick: int, escalation: Envelope) -> None:
        """Cross-agency A2A request for medical mutual aid when teams are overwhelmed. Logged
        and idempotent -- a single outstanding request per run, served by a partner agency."""
        self._a2a_requested = True
        payload = escalation.typed_payload()
        task = self.a2a.request(
            to_agency="qc-cdrrmo",
            capability="medical_mutual_aid",
            idempotency_key="medical-mutual-aid",
            trace_id=escalation.trace_id,
            context={"incident_id": getattr(payload, "incident_id", None)},
        )
        self.bb.record(
            tick,
            "a2a_mutual_aid",
            to_agency=task.to_agency,
            capability=task.capability,
            state=task.state.value,
            accepted=task.result is not None,
        )

    # stress
    def _apply_peak_stress(self, tick: int) -> None:
        if self.params.forecast_outage_at_peak:
            self.forecast.alive = False
            self.bb.record(tick, "stress_forecast_outage")
        frac = self.params.agent_kill_fraction
        if frac > 0.0:
            ids = sorted(self.bb.assets)
            k = int(frac * len(ids))
            for aid in ids[:k]:
                self.bb.disable_asset(aid, tick, reason="stress_agent_kill")

    # plumbing
    def _emit(self, envs: list[Envelope], tick: int, *, drop_prob: float = 0.0) -> None:
        for env in envs:
            if not self.bus.publish(env, tick, drop_prob=drop_prob):
                continue
            mt = env.msg_type
            if mt is MsgType.ASSET_COMMITTED:
                self._n_commitments += 1
            elif mt is MsgType.ESCALATION_RAISED:
                self._n_escalations += 1
            elif mt is MsgType.ROLLBACK_ISSUED:
                self._n_rollbacks += 1
            elif mt is MsgType.APPROVAL_DECISION:
                payload = env.typed_payload()
                approved = bool(getattr(payload, "approved", False))
                self._n_approvals += int(approved)
                self._n_denied += int(not approved)

    def _sample(self, tick: int, river: float) -> None:
        incs = self.bb.incidents.values()
        in_progress = sum(1 for i in incs if i.status is IncidentStatus.IN_PROGRESS)
        resolved = sum(1 for i in incs if i.status is IncidentStatus.RESOLVED)
        committed = sum(
            1
            for a in self.bb.assets.values()
            if a.status in (AssetStatus.EN_ROUTE, AssetStatus.ON_SCENE)
        )
        self._asset_ticks += committed  # cost: cumulative asset-ticks consumed
        # latency: mean report->on-scene (response) delay among incidents reached so far
        delays = [i.on_scene_tick - i.reported_tick for i in incs if i.on_scene_tick is not None]
        mean_response = sum(delays) / len(delays) if delays else 0.0
        unmet_by: dict[str, int] = {}
        for i in incs:
            if i.status in (IncidentStatus.RESOLVED, IncidentStatus.SUPPRESSED):
                continue
            unmet_by[i.barangay] = unmet_by.get(i.barangay, 0) + i.people
        self._samples.append(
            TickSignals(
                tick=tick,
                river_m=river,
                open_incidents=len(self.bb.open_incidents()),
                in_progress=in_progress,
                resolved_cumulative=resolved,
                mean_response_ticks=mean_response,
                committed_assets=committed,
                utilization=committed / max(1, len(self.bb.assets)),
                committed_asset_ticks=self._asset_ticks,
                unmet_people=sum(unmet_by.values()),
                gini_unmet=gini([float(v) for v in unmet_by.values()]),
                escalations_cumulative=self._n_escalations,
                approvals_cumulative=self._n_approvals,
                rollbacks_cumulative=self._n_rollbacks,
                drops_cumulative=self.bus.dropped,
            )
        )

    # reporting
    def _build_report(self) -> RunReport:
        outcome = self.auditor.outcome_report(self.world, self.bb)
        emergence = self.auditor.emergence_report(
            self.world,
            self.bb,
            commitments=self._n_commitments,
            rollbacks=self._n_rollbacks,
            escalations=self._n_escalations,
            approvals=self._n_approvals,
            approvals_denied=self._n_denied,
            drops=self.bus.dropped,
        )
        quality = self.auditor.quality_report(self.bb.events, self.bus.log)
        return RunReport(
            run_id=self.run_id,
            provenance=self._provenance(),
            outcome=outcome,
            emergence=emergence,
            quality=quality,
            golden_rows=[s.as_row() for s in self._samples],
            events=self.bb.events,
        )

    def _provenance(self) -> dict[str, Any]:
        libs = library_versions()
        return {
            "policy": self.policy_name,
            "use_fairness": self.use_fairness,
            "use_governance": self.use_governance,
            "seed": self.seed,
            "scenario": self.scenario.name,
            "horizon_ticks": self.params.horizon_ticks,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "libraries": libs,
            "env_fingerprint": env_fingerprint(
                self.scenario.name, self.seed, self.policy_name, libs
            ),
            "stress": {
                "report_storm_multiplier": self.params.report_storm_multiplier,
                "sybil_false_rate": self.params.sybil_false_rate,
                "comms_drop_prob": self.params.comms_drop_prob,
                "agent_kill_fraction": self.params.agent_kill_fraction,
                "forecast_outage_at_peak": self.params.forecast_outage_at_peak,
            },
        }


def _incident_id_of(task_env: Envelope) -> str:
    """Extract the incident id from a TASK_ANNOUNCED envelope (typed, mypy-safe)."""
    payload = task_env.typed_payload()
    assert isinstance(payload, TaskAnnouncePayload)
    return payload.incident_id
