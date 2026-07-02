"""Timing-scenario models — the Flexibility scan artifact (`time_options.json`).

Implements the artifact side of ``designs/plans/timing-scenario-plan.md``: a
per-pack record of candidate departure times graded against the planned time.
Posture is inherited from the mitigation framework — an **attention-director,
never a verdict**: candidates carry explicit model-coverage labels
(``confidence``) and the scan never auto-switches the plan.

Confidence ladder (the honesty ladder from the plan):

* ``confirmed_in_window`` — the candidate's whole flight fits inside every
  fetched model's enriched window, so the multi-model grade is as trustworthy
  as the briefing itself. Free (pure re-analysis of pack data).
* ``ecmwf_only`` — graded on daylight-extended ECMWF only (slice 2); other
  models not yet checked. Provisional by construction.
* ``confirmed`` — a user-tapped multi-model confirm (slice 3) upgraded (or
  downgraded — a designed outcome, not an error) the provisional grade.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

#: Flexibility modes (mirrors ``Flight.flexibility``).
FlexibilityMode = Literal["none", "alternate", "same_day", "prev_day", "next_day"]

#: Candidate model-coverage labels — see module docstring.
TimeConfidence = Literal["confirmed_in_window", "ecmwf_only", "confirmed"]


class TimeConfirmation(BaseModel):
    """Result of a multi-model check of one candidate (slice 3 on-tap confirm,
    or filled at scan time when the candidate is in-window)."""

    models_checked: list[str]
    assessment: str  # GREEN / AMBER / RED
    assessment_reason: str = ""
    better_than_baseline: bool
    improves: list[str] = Field(default_factory=list)
    worsens: list[str] = Field(default_factory=list)
    confirmed_at: datetime


class TimeCandidate(BaseModel):
    """One graded departure time.

    ``departure_shift_hours`` is the essential coordinate — shifting departure
    moves *every* route point's ETA (``analyze_all_route_points`` re-derives
    per-point valid-times), so a candidate is a shifted flight, not a single
    valid-time. ``valid_times`` records the per-point ETAs the grade actually
    read — the honesty-audit trail for the coverage check.
    """

    departure_time: datetime
    departure_shift_hours: float
    valid_times: list[datetime] = Field(default_factory=list)

    assessment: str  # GREEN / AMBER / RED (per ``models_used``)
    assessment_reason: str = ""
    models_used: list[str] = Field(default_factory=list)

    # Full-picture diff vs the baseline grade over the SAME model set — never
    # a window that fixed icing but quietly introduced a crosswind.
    improves: list[str] = Field(default_factory=list)
    worsens: list[str] = Field(default_factory=list)
    # Net severity drop summed over the scan-class advisory set (the ranking
    # objective; decision D). Positive == better on the timing-sensitive axes.
    margin: float = 0.0

    confidence: TimeConfidence
    is_baseline: bool = False   # the planned departure, graded identically
    is_alternate: bool = False  # the pinned Alternate-time row (Flexibility)
    confirmed: TimeConfirmation | None = None  # slice-3 on-tap result
    # True while an on-tap confirm is queued/running for this candidate —
    # the polling client renders "checking all models…" off this flag.
    confirm_pending: bool = False


class TimeScanBaseline(BaseModel):
    """The planned departure graded through the same path as the candidates —
    the diff denominator (never compared against the multi-model headline of a
    different model set).

    ``ecmwf_assessment`` is the ECMWF-only view of the planned time — the diff
    denominator for ``ecmwf_only`` provisional candidates (slice 2), so their
    improves/worsens compare like with like. None when no daylight extension
    ran (pure in-window scan)."""

    departure_time: datetime
    assessment: str
    assessment_reason: str = ""
    models_used: list[str] = Field(default_factory=list)
    ecmwf_assessment: str | None = None
    ecmwf_assessment_reason: str | None = None


class ModelCoverage(BaseModel):
    """Honest gradable span for one model, derived from the saved pack.

    ``start``/``end`` bound the per-point valid-times a candidate may use for
    this model. Rule-window ∩ data-marker: the fetch window rule bounds the
    trailing forward-fill smear (GFS CLW / cloud diagnostics forward-fill past
    the last native anchor), while the data marker catches enrichment that
    failed inside the rule window. ``uniform`` marks OM-only models whose
    fidelity doesn't change across the fetched day (their icing/cloud inputs
    are synthesized at the flight window too)."""

    model: str
    start: datetime | None = None
    end: datetime | None = None
    uniform: bool = False


class TimeScanWindow(BaseModel):
    """The searched departure window and what clipped it."""

    start: datetime
    end: datetime
    flexibility: FlexibilityMode
    daylight_clipped: bool = False
    horizon_clipped: bool = False


class TimeWindowScan(BaseModel):
    """The `time_options.json` artifact — everything the scenario UI renders."""

    schema_version: int = 1
    flexibility: FlexibilityMode
    baseline: TimeScanBaseline
    window: TimeScanWindow | None = None  # None for pure "alternate" mode
    candidates: list[TimeCandidate] = Field(default_factory=list)

    # Departure times considered but refused because some model's enriched
    # coverage didn't span their flight — the honesty guardrail made visible
    # (audit trail; the UI may render "N hours not checkable yet").
    refused_times: list[datetime] = Field(default_factory=list)

    coverage: list[ModelCoverage] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)  # fetched model set
    # Staleness key (decision H): a refresh landing on the same ECMWF run may
    # reuse this scan; a new run invalidates it. None when ECMWF absent.
    ecmwf_run_ts: int | None = None
    generated_at: datetime

    def candidate_at(self, departure: datetime) -> TimeCandidate | None:
        """Find the candidate for a departure time (minute tolerance)."""
        for c in self.candidates:
            if abs((c.departure_time - departure).total_seconds()) < 60:
                return c
        return None


class TimeScanStatus(BaseModel):
    """Small `time_scan_status.json` sidecar — what the polling endpoint
    returns while (or instead of) the artifact.

    ``skipped`` carries a machine reason (e.g. ``"flexibility_none"``,
    ``"no_coverage"``) so the client can distinguish "nothing to show" from
    "still looking"."""

    status: Literal["pending", "running", "done", "failed", "skipped"]
    flexibility: FlexibilityMode = "none"
    reason: str = ""
    updated_at: datetime
