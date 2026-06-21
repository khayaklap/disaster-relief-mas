"""Contract tests: the message envelope is well-formed, strict, and complete."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bayanihan_net.config import AssetType, IncidentType
from bayanihan_net.messages import (
    PAYLOAD_MODELS,
    BidPayload,
    Envelope,
    IncidentReportPayload,
    MsgType,
    Priority,
    SecurityContext,
    deterministic_id,
    make_envelope,
)


def _ctx() -> SecurityContext:
    return SecurityContext(role="sensing-scout", scopes=("cop:write",))


def test_every_message_type_has_a_payload_model() -> None:
    # The protocol is closed: no message type may exist without a typed body.
    assert set(PAYLOAD_MODELS) == set(MsgType)


def test_make_envelope_is_deterministic_and_validates() -> None:
    payload = IncidentReportPayload(
        incident_id="INC-1",
        node="T",
        barangay="Tumana",
        itype=IncidentType.RESCUE,
        people=40,
        severity=0.9,
        reported_tick=3,
    )
    env = make_envelope(
        agent_id="sensing-1",
        msg_type=MsgType.INCIDENT_REPORT,
        payload=payload,
        trace_id="trace-INC-1",
        tick=3,
        security_context=_ctx(),
        seq=0,
        priority=Priority.CRITICAL,
        idempotency_key="T:rescue:3",
    )
    # deterministic id derived from sender/tick/seq/type
    assert env.message_id == deterministic_id("sensing-1", 3, 0, MsgType.INCIDENT_REPORT.value)
    assert env.idempotency_key == "T:rescue:3"
    assert env.schema_version == "v1"
    # the typed payload round-trips back out of the envelope
    parsed = env.typed_payload()
    assert isinstance(parsed, IncidentReportPayload)
    assert parsed.people == 40 and parsed.itype is IncidentType.RESCUE


def test_payload_is_strict_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        BidPayload(  # type: ignore[call-arg]
            task_id="T1",
            asset_id="BOAT-1",
            asset_type=AssetType.BOAT,
            eta_ticks=2.0,
            capacity=10,
            route_risk=0.3,
            feasible=True,
            local_cost=2.0,
            bogus_field="nope",
        )


def test_envelope_typed_payload_rejects_mismatched_body() -> None:
    # An envelope whose payload doesn't match its declared msg_type must fail validation.
    env = Envelope(
        trace_id="t",
        message_id="m",
        idempotency_key="m",
        agent_id="a",
        msg_type=MsgType.BID,
        tick=0,
        security_context=_ctx(),
        payload={"not": "a bid"},
    )
    with pytest.raises(ValidationError):
        env.typed_payload()
