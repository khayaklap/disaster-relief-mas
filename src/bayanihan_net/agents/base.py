"""The agent base class.

Every agent is a small, mostly-pure decision unit: it reads the shared COP and emits
typed :class:`~bayanihan_net.messages.Envelope` speech-acts. It never mutates the
blackboard directly through arbitrary writes -- state changes flow through the bus and the
blackboard's guarded mutators, which is what keeps the invariants (idempotency,
no-double-commit, audit) enforceable in one place.

Each agent carries a :class:`~bayanihan_net.messages.SecurityContext` (its identity +
authorization scopes) and a monotonic ``seq`` counter, so every ``message_id`` it stamps
is deterministic -- the simulation stays reproducible from the seed with no ``uuid4``.
"""

from __future__ import annotations

from ..config import Scenario
from ..messages import Envelope, MsgType, Priority, SecurityContext, _Payload, make_envelope


class Agent:
    """Base class for all agents. Subclasses set ``role`` / ``default_scopes`` and add
    decision methods that return lists of envelopes."""

    role: str = "agent"
    default_scopes: tuple[str, ...] = ()

    def __init__(self, agent_id: str, scenario: Scenario) -> None:
        self.agent_id = agent_id
        self.scenario = scenario
        self.params = scenario.params
        self._seq = 0
        self.alive = True  # flipped off by the agent-kill stress injection

    @property
    def security_context(self) -> SecurityContext:
        """This agent's capability token: its role and the scopes it is permitted to use."""
        return SecurityContext(role=self.role, scopes=self.default_scopes)

    def _emit(
        self,
        *,
        msg_type: MsgType,
        payload: _Payload,
        trace_id: str,
        tick: int,
        priority: Priority = Priority.MEDIUM,
        recipient: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
        deadline_tick: int | None = None,
    ) -> Envelope:
        """Stamp a validated envelope from this agent and advance its sequence counter."""
        env = make_envelope(
            agent_id=self.agent_id,
            msg_type=msg_type,
            payload=payload,
            trace_id=trace_id,
            tick=tick,
            security_context=self.security_context,
            seq=self._seq,
            priority=priority,
            recipient=recipient,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            deadline_tick=deadline_tick,
        )
        self._seq += 1
        return env
