"""Interop tests: the MCP and A2A boundaries enforce scope/trust, idempotency, and tracing."""

from __future__ import annotations

from bayanihan_net.interop.a2a import (
    A2AClient,
    A2AGateway,
    A2AState,
    AgencyProfile,
    default_mutual_aid_network,
)
from bayanihan_net.interop.mcp_tools import default_registry
from bayanihan_net.messages import SecurityContext


# -- MCP: scoped discovery + policy-gated, trace-propagating calls --------------------
def test_mcp_discovery_lists_tools_and_scopes() -> None:
    tools = {t.name: t for t in default_registry().discover()}
    assert "pagasa.river" in tools
    assert tools["pagasa.river"].required_scope == "tool:pagasa.read"


def test_mcp_call_requires_the_declared_scope() -> None:
    reg = default_registry()
    authorized = SecurityContext(role="hydromet", scopes=("tool:pagasa.read",))
    unauthorized = SecurityContext(role="curious", scopes=("cop:write",))
    ok = reg.get("pagasa.river").call(authorized, "trace-1", river_m=17.5)
    denied = reg.get("pagasa.river").call(unauthorized, "trace-2", river_m=17.5)
    assert ok.ok and ok.data is not None and ok.data["river_m"] == 17.5
    assert ok.trace_id == "trace-1"  # trace propagated through the hop
    assert denied.ok is False and "scope" in (denied.error or "")


# -- A2A: local access policy, capability match, idempotency, retry ------------------
def test_a2a_completes_for_a_trusted_capable_partner() -> None:
    client = default_mutual_aid_network()
    task = client.request(
        to_agency="qc-cdrrmo",
        capability="medical_mutual_aid",
        idempotency_key="k1",
        trace_id="t1",
    )
    assert task.state is A2AState.COMPLETED
    assert task.result is not None and task.result["accepted_by"] == "qc-cdrrmo"
    assert task.context["trace_id"] == "t1"


def test_a2a_rejects_untrusted_requester_and_unknown_capability() -> None:
    gw = A2AGateway(
        AgencyProfile("ngo", capabilities=("relief_goods",), trusted_partners=("marikina-cdrrmo",))
    )
    client = A2AClient("marikina-cdrrmo", {"ngo": gw})
    wrong_cap = client.request(
        to_agency="ngo", capability="medical_mutual_aid", idempotency_key="k", trace_id="t"
    )
    assert wrong_cap.state is A2AState.REJECTED and "capability" in (wrong_cap.error or "")

    stranger = A2AClient("rogue-agency", {"ngo": gw})
    untrusted = stranger.request(
        to_agency="ngo", capability="relief_goods", idempotency_key="k2", trace_id="t"
    )
    assert untrusted.state is A2AState.REJECTED and "trusted" in (untrusted.error or "")


def test_a2a_is_idempotent() -> None:
    client = default_mutual_aid_network()
    first = client.request(
        to_agency="qc-cdrrmo",
        capability="medical_mutual_aid",
        idempotency_key="same",
        trace_id="t1",
    )
    second = client.request(
        to_agency="qc-cdrrmo",
        capability="medical_mutual_aid",
        idempotency_key="same",
        trace_id="t2",
    )
    # the partner served the work once and returns the SAME task on a repeat key
    assert first is second
    gw = client._partners["qc-cdrrmo"]
    assert len(gw.received) == 1


def test_a2a_retries_transient_failure_then_succeeds() -> None:
    gw = A2AGateway(
        AgencyProfile(
            "qc-cdrrmo", capabilities=("boat_mutual_aid",), trusted_partners=("marikina-cdrrmo",)
        ),
        flaky_until=1,  # first attempt fails transiently, retry succeeds
    )
    client = A2AClient("marikina-cdrrmo", {"qc-cdrrmo": gw})
    task = client.request(
        to_agency="qc-cdrrmo",
        capability="boat_mutual_aid",
        idempotency_key="k",
        trace_id="t",
        retries=2,
    )
    assert task.state is A2AState.COMPLETED


def test_a2a_no_route_fails_cleanly() -> None:
    client = A2AClient("marikina-cdrrmo", {})
    task = client.request(
        to_agency="atlantis", capability="anything", idempotency_key="k", trace_id="t"
    )
    assert task.state is A2AState.FAILED and "no route" in (task.error or "")
