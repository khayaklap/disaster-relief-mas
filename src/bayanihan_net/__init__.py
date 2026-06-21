"""Bayanihan-Net: a multi-agent coordination system for typhoon-induced urban flood
response in the Marikina River basin (Metro Manila).

Design methodology (mirrors the two prior course assignments): the *safety-critical*
control plane -- the message schema, the blackboard's leases/idempotency, the
contract-net allocation and its incentive scoring, the governance policy, and the
escalation/rollback logic -- is **pure, deterministic Python** that is unit-testable
with no API key and no torch. The stochastic / heavy parts (the seeded scenario world and
the offline RL routing study) sit *around* that core and feed it only as recommendations.
"RL informs; code and humans decide."
"""

__version__ = "0.1.0"
