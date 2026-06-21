"""MCP-style tool boundary: scoped discovery + policy-gated, trace-propagating invocation.

The Model Context Protocol pattern (from the course's interoperability material) is about
giving an agent *scoped* access to an external tool/context source: the tool declares its
name, parameters, and the capability scope it requires, and every call is checked against
the caller's :class:`SecurityContext` before it runs. Here the upstreams (PAGASA river
gauge, MMDA road closures, DOH hospital beds) are mocked, but the boundary is real: an
agent without the right scope is refused, and every call carries a ``trace_id`` so the hop
is auditable end to end.

This is deliberately *not* an LLM tool-calling layer -- it is the governed adapter that a
tool-using agent would sit behind, which is the part that matters for safety.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..governance.policy import is_authorized
from ..messages import SecurityContext


@dataclass(frozen=True)
class MCPTool:
    """A tool's self-description (the discoverable contract)."""

    name: str
    description: str
    required_scope: str  # capability scope the caller must hold
    params: tuple[str, ...]  # declared parameter names


@dataclass(frozen=True)
class MCPResult:
    """The outcome of a tool call, with the trace id propagated through the hop."""

    tool: str
    trace_id: str
    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None


class MCPAdapter:
    """A single policy-gated tool endpoint over a (mocked) upstream handler."""

    def __init__(self, tool: MCPTool, handler: Callable[..., dict[str, Any]]) -> None:
        self.tool = tool
        self._handler = handler

    def call(self, ctx: SecurityContext, trace_id: str, **params: Any) -> MCPResult:
        """Invoke the tool if the caller is scoped for it; never raises (denial/bad-args ->
        a clean ``ok=False`` result, mirroring how a real adapter should behave at a boundary)."""
        if not is_authorized(ctx, self.tool.required_scope):
            return MCPResult(
                self.tool.name,
                trace_id,
                ok=False,
                error=f"denied: scope '{self.tool.required_scope}' required",
            )
        # enforce the tool's declared parameter contract rather than passing arbitrary kwargs
        if set(params) != set(self.tool.params):
            return MCPResult(
                self.tool.name,
                trace_id,
                ok=False,
                error=f"bad params: expected {self.tool.params}, got {tuple(params)}",
            )
        return MCPResult(self.tool.name, trace_id, ok=True, data=self._handler(**params))


class MCPRegistry:
    """A discoverable set of MCP tools (the 'what can I call, and may I?' surface)."""

    def __init__(self, adapters: list[MCPAdapter]) -> None:
        self._adapters = {a.tool.name: a for a in adapters}

    def discover(self) -> list[MCPTool]:
        """The tool descriptor of every registered adapter (scoped capability discovery)."""
        return [a.tool for a in self._adapters.values()]

    def get(self, name: str) -> MCPAdapter:
        """Fetch a registered adapter by its tool name."""
        return self._adapters[name]


# mocked upstream handlers (deterministic; the boundary is what is real)
def _pagasa_handler(*, river_m: float) -> dict[str, Any]:
    return {"gauge": "sto_nino", "river_m": river_m, "source": "PAGASA (mock)"}


def _mmda_handler(*, tick: int) -> dict[str, Any]:
    # The mock reports no closures beyond what the flood model already encodes, so wiring it
    # in cross-checks the boundary without perturbing the routing belief.
    return {"tick": tick, "closures": [], "source": "MMDA (mock)"}


def _doh_handler(*, beds_total: int) -> dict[str, Any]:
    return {"beds_available": beds_total, "source": "DOH/Amang Rodriguez (mock)"}


def default_registry() -> MCPRegistry:
    """Three external feeds exposed at the (scope-gated) MCP boundary. Only ``pagasa.river`` is
    wired into the live run loop (the forecast agent sources the river through it); ``mmda.closures``
    and ``doh.beds`` are registered and scope-declared -- demonstrating the discovery surface --
    but are not invoked in the current loop."""
    return MCPRegistry(
        [
            MCPAdapter(
                MCPTool("pagasa.river", "river-gauge reading", "tool:pagasa.read", ("river_m",)),
                _pagasa_handler,
            ),
            MCPAdapter(
                MCPTool("mmda.closures", "road-closure feed", "tool:mmda.read", ("tick",)),
                _mmda_handler,
            ),
            MCPAdapter(
                MCPTool(
                    "doh.beds", "hospital-bed availability", "tool:hospital.read", ("beds_total",)
                ),
                _doh_handler,
            ),
        ]
    )
