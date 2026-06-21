"""A2A -- agent-to-agent work exchange across organizational / trust boundaries.

Where MCP gives an agent a tool, A2A lets one *agency's* system hand a unit of **work** to
another's: Marikina CDRRMC asking Quezon City or the Red Cross for medical mutual aid when
its own teams are overwhelmed. The boundary here carries the things that make cross-org
delegation safe (course interoperability material): a typed task with a capability request,
a **local access policy** owned by the receiving agency (it decides whom it trusts and what
it offers), **idempotency** (a retried request is not double-served), partial-progress /
terminal states, and trace propagation.

The partner systems are mocked, but the contract -- and the trust decision -- are real.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class A2AState(StrEnum):
    """Terminal/transient states of a cross-agency task: COMPLETED/REJECTED are final,
    FAILED is transient (retryable)."""

    SUBMITTED = "submitted"
    COMPLETED = "completed"
    REJECTED = "rejected"  # refused by the receiving agency's access policy
    FAILED = "failed"  # transient failure (route down) -- retryable


@dataclass
class A2ATask:
    """A unit of work offered from one agency to another."""

    task_id: str
    from_agency: str
    to_agency: str
    capability: str  # the capability being requested, e.g. "medical_mutual_aid"
    idempotency_key: str
    context: dict[str, Any] = field(default_factory=dict)  # carries trace_id, incident, etc.
    state: A2AState = A2AState.SUBMITTED
    progress: float = 0.0
    result: dict[str, Any] | None = None
    error: str | None = None


@dataclass(frozen=True)
class AgencyProfile:
    """A receiving agency's local policy: what it offers and whom it trusts."""

    agency: str
    capabilities: tuple[str, ...]
    trusted_partners: tuple[str, ...]


class A2AGateway:
    """A remote agency's inbound endpoint. It -- not the requester -- owns the trust decision."""

    def __init__(self, profile: AgencyProfile, *, flaky_until: int = 0) -> None:
        self.profile = profile
        self._seen: dict[str, A2ATask] = {}  # idempotency cache
        self.received: list[A2ATask] = []
        self._flaky_until = flaky_until  # fail this many initial attempts (transient-failure demo)
        self._attempts = 0

    def submit(self, task: A2ATask) -> A2ATask:
        """Apply idempotency, then the local access policy, then (mock-)serve the work."""
        if task.idempotency_key in self._seen:
            return self._seen[task.idempotency_key]  # already handled -> no double-service
        self._attempts += 1
        if self._attempts <= self._flaky_until:
            task.state = A2AState.FAILED
            task.error = "partner temporarily unreachable"
            return task  # NOT cached -> a retry can still succeed
        if task.from_agency not in self.profile.trusted_partners:
            task.state, task.error = A2AState.REJECTED, "requester not trusted"
        elif task.capability not in self.profile.capabilities:
            task.state, task.error = A2AState.REJECTED, "capability not offered"
        else:
            task.state = A2AState.COMPLETED
            task.progress = 1.0
            task.result = {"accepted_by": self.profile.agency, "capability": task.capability}
        self._seen[task.idempotency_key] = task
        self.received.append(task)
        return task


class A2AClient:
    """A local agency's outbound side: route a request to a partner, with bounded retry."""

    def __init__(self, agency: str, partners: dict[str, A2AGateway]) -> None:
        self.agency = agency
        self._partners = partners
        self.sent: list[A2ATask] = []

    def request(
        self,
        *,
        to_agency: str,
        capability: str,
        idempotency_key: str,
        trace_id: str,
        context: dict[str, Any] | None = None,
        retries: int = 2,
    ) -> A2ATask:
        """Submit a task to a partner agency, retrying only transient (FAILED) outcomes."""
        gateway = self._partners.get(to_agency)
        ctx = {**(context or {}), "trace_id": trace_id}
        if gateway is None:
            task = A2ATask(
                f"a2a:{idempotency_key}",
                self.agency,
                to_agency,
                capability,
                idempotency_key,
                ctx,
                state=A2AState.FAILED,
                error="no route to agency",
            )
            self.sent.append(task)
            return task
        last: A2ATask | None = None
        for _ in range(retries + 1):
            task = A2ATask(
                f"a2a:{idempotency_key}",
                self.agency,
                to_agency,
                capability,
                idempotency_key,
                dict(ctx),
            )
            last = gateway.submit(task)
            if last.state is not A2AState.FAILED:  # only transient failures are worth retrying
                break
        assert last is not None
        self.sent.append(last)
        return last


def default_mutual_aid_network() -> A2AClient:
    """Marikina CDRRMC's outbound client, wired to two mocked partner agencies."""
    partners = {
        "qc-cdrrmo": A2AGateway(
            AgencyProfile(
                "qc-cdrrmo",
                capabilities=("medical_mutual_aid", "boat_mutual_aid"),
                trusted_partners=("marikina-cdrrmo",),
            )
        ),
        "phil-red-cross": A2AGateway(
            AgencyProfile(
                "phil-red-cross",
                capabilities=("medical_mutual_aid", "relief_goods"),
                trusted_partners=("marikina-cdrrmo", "qc-cdrrmo"),
            )
        ),
    }
    return A2AClient("marikina-cdrrmo", partners)
