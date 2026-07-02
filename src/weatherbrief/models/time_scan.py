"""Pydantic models for the timing-scenario scan (``time_options.json``).

The scan answers *"a better departure time may exist"* for hazards that
genuinely vary through the day. It is an **attention-director, never a
verdict** — a neutral soft hook, provisional until the user asks for a
multi-model confirm. See ``designs/plans/timing-scenario-plan.md``.

Honesty ladder encoded in the fields:

    SCANNING → CANDIDATES (ECMWF-only, provisional) → CONFIRMED (multi-model)

``TimeCandidate.confidence`` is ``"ecmwf_only"`` until a
:class:`TimeConfirmation` is attached on user tap. The confirm-downgrade
(*"you tapped a suggestion, it turned out worse"*) is a first-class designed
outcome — ``TimeConfirmation.better_than_baseline`` can be ``False``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TimeConfirmation(BaseModel):
    """Multi-model check of one candidate, filled on user tap (deferred cost).

    The candidate's ICON/GFS GRIB is off the flight window, so producing this
    triggers a deferred fetch+decode — the heavy cost we gate on demonstrated
    user intent. Cached on the candidate once computed.
    """

    models_checked: list[str] = Field(default_factory=list)
    assessment: str = "GREEN"            # GREEN/AMBER/RED across the checked models
    assessment_reason: str = ""
    # Did the full multi-model check still agree the window is better than the
    # planned time? ``False`` is the on-brand downgrade case, not an error.
    better_than_baseline: bool = False
    improves: list[str] = Field(default_factory=list)
    worsens: list[str] = Field(default_factory=list)
    detail: str = ""                     # human phrasing incl. the downgrade case
    confirmed_at: datetime | None = None


class TimeCandidate(BaseModel):
    """One candidate departure time, graded ECMWF-only (provisional).

    A candidate is a **departure shift**: moving departure moves *every* route
    point's ETA (``valid_times``), which is why we store the shift, not a single
    valid-time. ``improves``/``worsens`` are the FULL-picture diff vs the
    baseline (so we never surface a window that fixed icing but quietly
    introduced a crosswind); ``margin`` ranks on the trigger (scan-class) set.
    """

    departure_time: datetime
    departure_shift_hours: float          # signed hours vs the planned departure
    valid_times: list[datetime] = Field(default_factory=list)  # per-route-point ETAs
    ecmwf_assessment: str = "GREEN"       # GREEN/AMBER/RED, ECMWF-only
    ecmwf_assessment_reason: str = ""
    improves: list[str] = Field(default_factory=list)  # advisory ids improved vs baseline
    worsens: list[str] = Field(default_factory=list)   # advisory ids worsened vs baseline
    margin: float = 0.0                   # trigger-weighted improvement (ranking key)
    confidence: Literal["ecmwf_only", "confirmed"] = "ecmwf_only"
    is_preferred: bool = False            # the pilot's pinned preferred departure time
    is_baseline: bool = False             # the planned departure (shift 0)
    confirmed: TimeConfirmation | None = None


class TimeScanBaseline(BaseModel):
    """The planned departure, graded ECMWF-only — the diff reference."""

    departure_time: datetime
    ecmwf_assessment: str = "GREEN"
    ecmwf_assessment_reason: str = ""


class TimeScanWindow(BaseModel):
    """The searched departure window and why it stops where it does.

    ``horizon_clipped`` marks the hard fidelity edge: beyond the ECMWF
    0–90h (168h at 00/12z) horizon even the scan would drop to degraded OM, so
    the window visibly stops there rather than silently degrading.
    """

    start: datetime                       # earliest candidate departure
    end: datetime                         # latest candidate departure
    cadence_hours: float = 1.0            # nominal native cadence (1h ≤90h)
    daylight_clipped: bool = False        # bounded by daylight (RouteSunAnalysis)
    horizon_clipped: bool = False         # bounded by ECMWF fidelity horizon
    day_flex: str = "day"                 # "day" | "prev" | "next"


class TimeWindowScan(BaseModel):
    """Top-level ``time_options.json`` artifact.

    Old packs (no scan) simply have no file — the endpoint 404s and the client
    shows nothing, so there is no back-compat surface to defend.
    """

    baseline: TimeScanBaseline
    window: TimeScanWindow
    candidates: list[TimeCandidate] = Field(default_factory=list)
    # scan-class advisories RED/AMBER at the planned time that triggered the scan
    scan_flagged: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)   # models available for a confirm pass
    ecmwf_run_ts: int | None = None       # ECMWF init unix ts (staleness on refresh)
    generated_at: datetime | None = None
    # Whether the extended (daylight-enriched) cross-section artifact was written.
    cross_section_ext: bool = False

    def candidate_at(self, departure_time: datetime) -> TimeCandidate | None:
        """Find a candidate by departure time (used by the confirm endpoint)."""
        for c in self.candidates:
            if c.departure_time == departure_time:
                return c
        return None
