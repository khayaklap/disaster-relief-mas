"""The blackboard bus -- the pub/sub transport that carries envelopes to the COP.

Agents publish envelopes; the bus applies the state-changing ones (incident reports,
observations) to the blackboard and records *every* message to an ordered log (the wire
tap the auditor reads). It enforces two transport-layer protections from the MAS failure
catalogue:

* **Transport idempotency** -- an exact message (same ``message_id``) re-delivered is
  dropped, so a retried/echoed send can't double-apply. (Distinct citizen reports that
  share a *content* ``idempotency_key`` are NOT dropped here -- they are corroborating
  observations and are fused, with a headcount, by the blackboard.)
* **Lossy delivery** -- an optional drop probability models comms degradation; a dropped
  message is logged, never silently lost, so the audit trail stays honest.
"""

from __future__ import annotations

import numpy as np

from ..blackboard import Blackboard
from ..governance.policy import is_authorized
from ..messages import Envelope, IncidentReportPayload, MsgType, ObservationPayload

# Message types whose effect mutates the shared COP (the rest are coordination acts that
# the engine consumes directly; the bus still logs them for the audit trail).
_COP_MUTATING = {MsgType.INCIDENT_REPORT, MsgType.OBSERVATION_POSTED}

# Least privilege, enforced at the transport layer: a COP-mutating message is only applied
# if its sender actually holds the write scope. Boundaries are thus not merely documented in
# AGENTS.md -- an under-scoped sender is refused here, in code, and the denial is audited.
_REQUIRED_SCOPE = {
    MsgType.INCIDENT_REPORT: "cop:write",
    MsgType.OBSERVATION_POSTED: "cop:write",
}


class BlackboardBus:
    """Ordered, idempotent, optionally-lossy message transport over the blackboard."""

    def __init__(self, bb: Blackboard, rng: np.random.Generator | None = None) -> None:
        self.bb = bb
        self.rng = rng
        self.log: list[Envelope] = []
        self._delivered_ids: set[str] = set()
        self.dropped = 0

    def publish(self, env: Envelope, tick: int, *, drop_prob: float = 0.0) -> bool:
        """Deliver one envelope. Returns ``True`` if applied, ``False`` if dropped or a
        duplicate. Comms-drop never applies to coordination integrity messages."""
        # exact-duplicate suppression (transport idempotency)
        if env.message_id in self._delivered_ids:
            self.bb.record(tick, "duplicate_message_dropped", message_id=env.message_id)
            return False
        # simulated comms loss (only for non-critical traffic; never drop awards/rollbacks)
        if (
            drop_prob > 0.0
            and self.rng is not None
            and env.priority.value not in ("critical",)
            and env.msg_type not in (MsgType.AWARD, MsgType.ROLLBACK_ISSUED)
            and float(self.rng.random()) < drop_prob
        ):
            self.dropped += 1
            self.bb.record(tick, "message_dropped", message_id=env.message_id, reason="comms")
            return False
        # least-privilege gate: refuse a COP write from a sender that lacks the scope
        required = _REQUIRED_SCOPE.get(env.msg_type)
        if required is not None and not is_authorized(env.security_context, required):
            self.bb.record(
                tick,
                "scope_denied",
                message_id=env.message_id,
                msg_type=env.msg_type.value,
                required_scope=required,
            )
            return False

        self._delivered_ids.add(env.message_id)
        self.log.append(env)
        self._apply(env, tick)
        return True

    def _apply(self, env: Envelope, tick: int) -> None:
        """Apply a COP-mutating message to the blackboard (others are log-only here)."""
        if env.msg_type not in _COP_MUTATING:
            return
        payload = env.typed_payload()
        if env.msg_type is MsgType.INCIDENT_REPORT:
            assert isinstance(payload, IncidentReportPayload)
            self.bb.ingest_report(payload, env.idempotency_key, tick)
        elif env.msg_type is MsgType.OBSERVATION_POSTED:
            assert isinstance(payload, ObservationPayload)
            if payload.kind == "river" and payload.river_m is not None:
                self.bb.post_river(payload.river_m, tick)
            elif (
                payload.kind == "road" and payload.edge is not None and payload.depth_m is not None
            ):
                self.bb.post_road(payload.edge, payload.depth_m, tick)
