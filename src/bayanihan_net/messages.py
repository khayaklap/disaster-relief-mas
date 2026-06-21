"""The communication contract, in code.

Every inter-agent message is a versioned :class:`Envelope` carrying the header fields
the course's MAS deck requires for production coordination (``trace_id``, ``agent_id``,
``msg_type``, ``deadline``, ``schema_version``, ``idempotency_key``, ``correlation_id``,
``security_context``, ``priority``) plus a typed ``payload``. The header is what makes
coordination *inspectable*: routing, deduplication, escalation, and the audit trail all
read these fields -- not the free text.

Payloads are validated against a per-``msg_type`` registry (:data:`PAYLOAD_MODELS`), so a
malformed message fails fast at the boundary instead of corrupting the blackboard.
``message_id`` / ``idempotency_key`` are deterministic strings supplied by callers (see
:func:`deterministic_id`) so the whole simulation is reproducible from a seed -- no
``uuid4`` randomness on the wire.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .config import AssetType, IncidentType

SCHEMA_VERSION = "v1"


def deterministic_id(*parts: object) -> str:
    """Build a stable id from its parts (keeps the simulation reproducible)."""
    return ":".join(str(p) for p in parts)


class Priority(StrEnum):
    """Message / task urgency. Drives routing order and SLA targets."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class MsgType(StrEnum):
    """The closed set of message types on the bus (the protocol's verbs)."""

    OBSERVATION_POSTED = "observation.posted"
    INCIDENT_REPORT = "incident.report"
    TASK_ANNOUNCED = "task.announced"  # contract-net call-for-proposals
    BID = "bid.submitted"
    AWARD = "award.granted"
    ASSET_COMMITTED = "asset.committed"
    ROUTE_COMPUTED = "route.computed"
    APPROVAL_REQUESTED = "approval.requested"  # -> human-in-the-loop
    APPROVAL_DECISION = "approval.decision"
    ESCALATION_RAISED = "escalation.raised"
    ROLLBACK_ISSUED = "rollback.issued"
    STATUS_UPDATE = "status.update"


class SecurityContext(BaseModel):
    """Identity + authorization travelling with a message. The basis of trust at A2A /
    MCP boundaries: who is asking, on whose behalf, and what they are scoped to do."""

    model_config = ConfigDict(extra="forbid")

    agency: str = "marikina-cdrrmo"  # owning organization (LGU command center by default)
    role: str  # the agent's role (e.g. "coordinator", "logistics-rescue")
    scopes: tuple[str, ...] = ()  # capability scopes (e.g. "asset:commit", "tool:pagasa.read")
    token: str = "local-trust"  # opaque capability token (mocked)


# ---------------------------------------------------------------------------
# Typed payloads -- one model per message type. ``extra="forbid"`` makes each a
# strict contract: an unknown field is a protocol violation, not silent drift.
# ---------------------------------------------------------------------------


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObservationPayload(_Payload):
    """A sensor reading posted to the COP (a river level, a road depth, or a sighting)."""

    kind: str  # "river" | "road" | "sighting"
    node: str | None = None
    edge: tuple[str, str] | None = None
    river_m: float | None = None
    depth_m: float | None = None
    note: str = ""


class IncidentReportPayload(_Payload):
    """One citizen incident report as it arrives, before COP deduplication."""

    incident_id: str
    node: str
    barangay: str
    itype: IncidentType
    people: int
    severity: float  # 0..1 severity score (see incidents.severity)
    reported_tick: int
    is_duplicate: bool = False
    is_suspected_false: bool = False  # flagged by sensing heuristic (Sybil guard)


class TaskAnnouncePayload(_Payload):
    """A contract-net call-for-proposals for one prioritized incident."""

    task_id: str
    incident_id: str
    node: str
    itype: IncidentType
    people: int
    severity: float
    priority: Priority
    deadline_tick: int


class BidPayload(_Payload):
    """A logistics agent's proposal for one task: an asset, its ETA, capacity, and route risk."""

    task_id: str
    asset_id: str
    asset_type: AssetType
    eta_ticks: float
    capacity: int
    route_risk: float  # 0..1 cumulative flood risk along the route
    feasible: bool
    local_cost: float  # the bidder's own (local) cost estimate
    note: str = ""


class AwardPayload(_Payload):
    """The coordinator's award of a task to the winning asset, with its global welfare score."""

    task_id: str
    incident_id: str
    asset_id: str
    route: tuple[str, ...]
    eta_ticks: float
    welfare_score: float  # the GLOBAL social-welfare score that won the auction
    requires_approval: bool


class AssetCommittedPayload(_Payload):
    """The atomic-commitment record: an asset is now bound to a task (no double-commit)."""

    asset_id: str
    task_id: str
    incident_id: str
    commit_tick: int


class RouteComputedPayload(_Payload):
    """The route a committed asset will travel, with ETA, flood risk, and travel mode."""

    asset_id: str
    path: tuple[str, ...]
    eta_ticks: float
    risk: float
    mode: str


class ApprovalRequestPayload(_Payload):
    """A decision package for the human approver -- context, not raw chatter."""

    request_id: str
    action: str  # e.g. "commit_last_boat", "forced_evacuation"
    incident_id: str | None = None
    asset_id: str | None = None
    decision_package: dict[str, Any]  # {context, options, recommendation, risk}


class ApprovalDecisionPayload(_Payload):
    """The human approver's verdict on an approval request (approve/deny + reason)."""

    request_id: str
    approved: bool
    approver: str
    reason: str = ""


class EscalationRaisedPayload(_Payload):
    """An escalation to the command center (mass-casualty overload or an unreachable incident)."""

    incident_id: str
    reason: str
    severity: float


class RollbackIssuedPayload(_Payload):
    """A record that a still-reversible award/commit was rolled back, with the reason."""

    target: str  # "award" | "commit"
    task_id: str | None = None
    asset_id: str | None = None
    reason: str = ""


class StatusUpdatePayload(_Payload):
    """A general status change for a subject (asset, incident, or agent)."""

    subject: str  # asset_id / incident_id / agent_id
    status: str
    note: str = ""


PAYLOAD_MODELS: dict[MsgType, type[_Payload]] = {
    MsgType.OBSERVATION_POSTED: ObservationPayload,
    MsgType.INCIDENT_REPORT: IncidentReportPayload,
    MsgType.TASK_ANNOUNCED: TaskAnnouncePayload,
    MsgType.BID: BidPayload,
    MsgType.AWARD: AwardPayload,
    MsgType.ASSET_COMMITTED: AssetCommittedPayload,
    MsgType.ROUTE_COMPUTED: RouteComputedPayload,
    MsgType.APPROVAL_REQUESTED: ApprovalRequestPayload,
    MsgType.APPROVAL_DECISION: ApprovalDecisionPayload,
    MsgType.ESCALATION_RAISED: EscalationRaisedPayload,
    MsgType.ROLLBACK_ISSUED: RollbackIssuedPayload,
    MsgType.STATUS_UPDATE: StatusUpdatePayload,
}


class Envelope(BaseModel):
    """The on-the-wire message. Header fields are routable/auditable; ``payload`` is
    the typed body, validated against :data:`PAYLOAD_MODELS` for its ``msg_type``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    trace_id: str  # one id per incident lifecycle -> end-to-end tracing
    message_id: str
    correlation_id: str | None = None  # links a reply to its request (bid->cfp, decision->request)
    idempotency_key: str  # duplicate suppression key
    agent_id: str  # sender
    recipient: str | None = None  # None = broadcast on the blackboard bus
    msg_type: MsgType
    priority: Priority = Priority.MEDIUM
    tick: int  # simulation tick the message was created
    deadline_tick: int | None = None  # when the work becomes moot
    security_context: SecurityContext
    payload: dict[str, Any] = Field(default_factory=dict)

    def typed_payload(self) -> _Payload:
        """Parse and validate ``payload`` into its registered model (raises on mismatch)."""
        model = PAYLOAD_MODELS[self.msg_type]
        return model.model_validate(self.payload)


def make_envelope(
    *,
    agent_id: str,
    msg_type: MsgType,
    payload: _Payload,
    trace_id: str,
    tick: int,
    security_context: SecurityContext,
    seq: int,
    priority: Priority = Priority.MEDIUM,
    recipient: str | None = None,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
    deadline_tick: int | None = None,
) -> Envelope:
    """Stamp a fully-formed, validated envelope around a typed payload.

    ``seq`` is a per-sender monotonic counter, so ``message_id`` is deterministic. If no
    ``idempotency_key`` is supplied it defaults to the ``message_id`` (unique per send);
    callers that want duplicate-suppression (e.g. incident reports) pass a *content*
    key instead.
    """
    message_id = deterministic_id(agent_id, tick, seq, msg_type.value)
    return Envelope(
        trace_id=trace_id,
        message_id=message_id,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key or message_id,
        agent_id=agent_id,
        recipient=recipient,
        msg_type=msg_type,
        priority=priority,
        tick=tick,
        deadline_tick=deadline_tick,
        security_context=security_context,
        payload=payload.model_dump(),
    )
