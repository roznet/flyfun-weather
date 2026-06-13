"""Regulatory "alternate required?" assessment models (issue #249).

A planning-grade, advisory layer that answers two questions for a briefing,
computed two ways — **FAA (14 CFR 91.169)** and **EASA Part-NCO** —:

1. **Destination trigger** — is a filed alternate required? (FAA: Yes/No;
   EASA: No / Marginal / Required).
2. **Per-candidate qualification** — does each weather-computed divert
   candidate meet alternate minima at its ETA? (FAA: Yes/No;
   EASA: Likely / Marginal / Unlikely).

Ceiling and visibility are evaluated independently from **real forecast data**
(TAF when available, NWP consensus otherwise). What we lack is the published
plate minima (DA/MDA and RVR/visibility) — we only know the approach *type*. So
the EASA requirement is computed transparently from an estimated plate-minima
**range** per approach class, and the unknown is expressed as a confidence band:
**Likely / Marginal / Unlikely**. FAA thresholds are fixed regulatory numbers,
so FAA stays binary.

See ``designs/alternate-requirement.md`` for the full design.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class BandVerdict(str, Enum):
    """Per-candidate qualification confidence band.

    ``LIKELY`` clears even the worst-case plate minima; ``UNLIKELY`` fails even
    the best-case; ``MARGINAL`` falls inside the proxy band — genuinely
    undeterminable without the published plate. FAA collapses the band (fixed
    thresholds) so it only ever yields ``LIKELY`` / ``UNLIKELY``.
    """

    LIKELY = "likely"
    MARGINAL = "marginal"
    UNLIKELY = "unlikely"


class TriggerVerdict(str, Enum):
    """Destination "is an alternate required?" verdict.

    FAA only ever yields ``NOT_REQUIRED`` / ``REQUIRED`` (fixed thresholds).
    """

    NOT_REQUIRED = "not_required"
    MARGINAL = "marginal"
    REQUIRED = "required"


class CriterionAssessment(BaseModel):
    """One criterion (ceiling or visibility) evaluated against a requirement band.

    ``forecast`` is the worst-in-window real forecast value (``None`` means it
    could not be determined, OR — for ceiling — that there is no ceiling layer;
    the ``verdict`` disambiguates: a good verdict with ``forecast=None`` is
    "no ceiling", a bad verdict with ``forecast=None`` is "missing"). The
    requirement band ``[required_min, required_max]`` collapses to a single value
    for FAA (``required_min == required_max``).
    """

    label: str  # "ceiling" | "visibility"
    unit: str  # "ft" | "m" | "SM"
    forecast: float | None
    required_min: float  # best-case plate requirement (band low end)
    required_max: float  # worst-case (conservative) requirement; == min for FAA
    # A BandVerdict value (qualification) or a TriggerVerdict value (trigger).
    verdict: Literal["likely", "marginal", "unlikely", "not_required", "required"]


class RegAlternateTrigger(BaseModel):
    """Destination "alternate required?" assessment for one regulatory regime."""

    regime: Literal["faa", "easa"]
    status: TriggerVerdict  # FAA: only NOT_REQUIRED / REQUIRED
    reason: str
    source: Literal["taf", "nwp", "none"]
    triggered_by_tempo: bool = False
    ceiling: CriterionAssessment
    visibility: CriterionAssessment


class AlternateQual(BaseModel):
    """Per-candidate alternate-minima qualification for one regime."""

    regime: Literal["faa", "easa"]
    verdict: BandVerdict  # combined (worst of the two criteria)
    reason: str
    ceiling: CriterionAssessment
    visibility: CriterionAssessment


class ConditionalGroup(BaseModel):
    """A TAF TEMPO/PROB change group overlapping the ETA window (descriptive).

    Surfaced so the pilot can see *why* the window worst-case is what it is and
    how each conditional was treated. ``counted`` reflects our (conservative)
    policy — TEMPO and PROB40 count toward the verdict, PROB30 is noted only.
    The verdict logic is unchanged by this; these are for display.
    """

    kind: str  # "TEMPO" | "PROB30" | "PROB40" | "PROB30 TEMPO" | "PROB40 TEMPO"
    probability: int | None = None
    ceiling_ft: float | None = None  # None = no ceiling layer in this group
    visibility_m: float | None = None
    validity: str | None = None  # e.g. "0406/0408"
    counted: bool = True  # did it affect the (conservative) verdict?


class AlternateRequirement(BaseModel):
    """Regulatory alternate-requirement assessment for a route's destination."""

    destination_icao: str
    eta: datetime | None = None
    faa: RegAlternateTrigger
    easa: RegAlternateTrigger
    caveats: list[str] = Field(default_factory=list)
    # TAF-path transparency (None on the NWP fallback path): the steady-state
    # (main body / FM / BECMG) worst over the window, plus the conditional
    # TEMPO/PROB groups that overlap it. Lets the UI show steady-state vs the
    # conditional-driven worst case.
    main_body_ceiling_ft: float | None = None
    main_body_visibility_m: float | None = None
    conditionals: list[ConditionalGroup] = Field(default_factory=list)
    computed_at: datetime | None = None
