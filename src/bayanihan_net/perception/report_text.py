"""Deterministic free-text rendering of a structured citizen report.

Real disaster reports arrive as **free text** -- a hotline call, an SMS, a social-media
post -- not as typed records. This simulation generates structured :class:`RawReport`
ground truth directly (``scenario.py``); this module renders that ground truth *back* into
a realistic free-text utterance, so the optional advisory LLM perception layer
(:mod:`bayanihan_net.perception.llm_extract`) has something to extract *from*.

The rendering is **deterministic**: a given report always yields the same text (the
phrasing is chosen by a process-independent hash of ``report_id``), so enabling the advisory
layer never introduces randomness and the default pipeline stays byte-identical. It is a
realistic *stand-in* for a real report stream, not a model of how residents actually phrase
emergencies -- flagged honestly, like every other illustrative fixture in this project.
"""

from __future__ import annotations

from ..config import IncidentType
from ..scenario import RawReport

# A few phrasings per incident type, selected deterministically by the report id, so the
# text carries real variation (what the LLM must parse) while staying fully reproducible.
# ASCII-only on purpose (portable across platforms); "po" / "baha" are common Taglish terms.
_TEMPLATES: dict[IncidentType, tuple[str, ...]] = {
    IncidentType.RESCUE: (
        "Hello po, tulong! Around {people} people are stranded on rooftops in {barangay}, water rising fast.",
        "Emergency in {barangay} -- about {people} residents trapped by floodwater and need rescue boats.",
        "Please send help to {barangay}, {people} of us are stuck, baha na po, we cannot get out.",
    ),
    IncidentType.MEDICAL: (
        "Medical emergency in {barangay}: roughly {people} people injured and need a medical team.",
        "Tulong po, {people} sick and elderly residents in {barangay} need medical evacuation.",
        "{people} casualties in {barangay} -- we need medics urgently, the clinic is flooded.",
    ),
    IncidentType.RELIEF: (
        "{barangay} is cut off; about {people} people here have had no food or clean water since morning.",
        "Relief needed in {barangay}: {people} residents stranded without supplies, baha sa lahat ng kalye.",
        "We are {people} families in {barangay} waiting for relief goods, the water is not going down.",
    ),
}


def render_report_text(report: RawReport) -> str:
    """Render a structured ``RawReport`` as a realistic, deterministic free-text utterance.

    The text always states the headcount and implies the incident type -- exactly the two
    facts the advisory LLM is asked to recover and the output guardrail re-validates.
    """
    templates = _TEMPLATES[report.itype]
    return templates[_stable_index(report.report_id, len(templates))].format(
        people=report.people, barangay=report.barangay
    )


def _stable_index(key: str, n: int) -> int:
    """A deterministic index in ``[0, n)`` from a string key.

    Uses a character-sum rather than the builtin ``hash`` so the choice is **process
    independent** (no ``PYTHONHASHSEED`` dependence), preserving byte-identical runs.
    """
    return sum(ord(c) for c in key) % n
