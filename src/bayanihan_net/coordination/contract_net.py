"""Contract-net allocation: the auction that assigns scarce assets to incidents.

A prioritized incident becomes a *task* (a call-for-proposals); capable agents submit
:class:`BidPayload` proposals; the coordinator scores them and awards. The defining design
choice -- the lever for **incentive alignment** -- is that the winner is chosen by a
**global social-welfare score** (:func:`score_bid`), not by the bidder's own local cost.
A bidder reports its selfish ``local_cost``; the auctioneer ignores it for ranking and
instead maximizes severity-weighted people-served-per-time, discounted for flood risk.
This is what stops the system from optimizing local utilization (whoever is nearest/idlest)
at the expense of global harm reduction. The *equity* lever lives one level up -- in which
incidents win the scarce assets first (the engine's fairness-weighted task ordering) --
because a per-incident multiplier here would scale all of an incident's bids equally and so
could never change which asset wins it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..incidents import Incident
from ..messages import BidPayload, Envelope

# How hard the welfare score discounts a fully-flooded (risk = 1.0) route: at most halves it.
# A modelling weight, not a tunable -- kept here, named, rather than as a bare literal.
_RISK_DISCOUNT = 0.5


def score_bid(bid: BidPayload, incident: Incident) -> float:
    """The GLOBAL social-welfare value of awarding this bid (higher is better).

    Maximizes severity-weighted people served per unit time-to-arrival, discounted by the
    route's flood risk. Infeasible bids score ``-inf`` so they can never win.
    """
    if not bid.feasible:
        return float("-inf")
    served = min(bid.capacity, incident.people)
    welfare = incident.severity * served / (bid.eta_ticks + 1.0)
    # route_risk is constructed in [0, 1] (network._edge_risk), so the discount stays in [0.5, 1].
    welfare *= 1.0 - _RISK_DISCOUNT * bid.route_risk  # a flooded route may fail / endanger crew
    return welfare


@dataclass(frozen=True)
class ScoredBid:
    """A bid paired with its global welfare score (and the envelope it arrived in)."""

    bid: BidPayload
    envelope: Envelope
    score: float


def score_bids(bids: list[Envelope], incident: Incident) -> list[ScoredBid]:
    """Score every bid envelope for a task against the global welfare objective."""
    scored: list[ScoredBid] = []
    for env in bids:
        bid = env.typed_payload()
        assert isinstance(bid, BidPayload)
        scored.append(ScoredBid(bid, env, score_bid(bid, incident)))
    return scored


def select_winner(scored: list[ScoredBid]) -> ScoredBid | None:
    """Pick the welfare-maximizing feasible bid. Ties break deterministically on the
    smaller ``asset_id`` so the auction is reproducible. Returns ``None`` if nothing is
    feasible (the signal for the coordinator to escalate an unmet incident)."""
    feasible = [s for s in scored if s.bid.feasible and s.score > float("-inf")]
    if not feasible:
        return None
    feasible.sort(key=lambda s: (-s.score, s.bid.asset_id))
    return feasible[0]
