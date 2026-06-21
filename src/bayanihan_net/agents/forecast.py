"""Hydromet / forecast agent -- the river-gauge feed on the COP.

Posts the river level each tick as an observation (sourced, in the fiction, from the
PAGASA Sto. Nino gauge via an MCP tool). The gauge reading is accurate; the system's
*partial observability* comes not from sensor noise here but from **freshness**: if this
agent is knocked out (a stress injection), its last observation goes stale under the COP
lease and the routing agent's belief lags the true flood -- the stale-COP hazard the
governance layer is designed to catch.
"""

from __future__ import annotations

from ..messages import Envelope, MsgType, ObservationPayload
from .base import Agent


class ForecastAgent(Agent):
    """Publishes river-level observations to the blackboard."""

    role = "hydromet-forecast"
    default_scopes = ("cop:write", "tool:pagasa.read")

    def observe(self, true_river_m: float, tick: int) -> list[Envelope]:
        """Emit this tick's river-level observation for the bus to post to the COP."""
        payload = ObservationPayload(
            kind="river", river_m=true_river_m, note="PAGASA Sto. Nino gauge (mocked)"
        )
        return [
            self._emit(
                msg_type=MsgType.OBSERVATION_POSTED,
                payload=payload,
                trace_id=f"river:{tick}",
                tick=tick,
            )
        ]
