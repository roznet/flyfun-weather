"""Setup-interview presets (#387, slice 3).

A small declarative structure mapping identity-level questions ("VFR only?",
"FIKI-equipped?", "how conservative are your minimums?") to patches over advisory
settings. It cannot be derived from the ``audience`` tier because a single answer
spans several advisories (FIKI enables ``fiki_icing`` AND disables
``icing_escape``).

Invariants (relied on by clients for idempotent, conflict-free application):

- Every option of a question declares the **same key set** (so re-answering a
  question is reversible — switching to another option overwrites exactly the
  keys the previous one wrote).
- Sibling questions declare **disjoint keys** (so answers never fight over a
  key on merge).

Built dynamically off the live catalog so the "standard / default" options carry
the real catalog defaults (no hand-copied numbers that could drift).
"""

from __future__ import annotations

from weatherbrief.analysis.advisories.registry import get_catalog
from weatherbrief.models import Interview, InterviewOption, InterviewQuestion


def _param_default(catalog: dict, advisory_id: str, key: str) -> float:
    """Look up a param's catalog default (raises KeyError if the key is unknown,
    which the interview validity test turns into a failure)."""
    entry = catalog[advisory_id]
    for p in entry.parameters:
        if p.key == key:
            return p.default
    raise KeyError(f"{advisory_id}.{key}")


def get_interview() -> Interview:
    """Return the setup-interview structure (v1 — 3 questions)."""
    catalog = {e.id: e for e in get_catalog()}

    # --- Q3 owned params: the conservative-minima set (pilot-choice values). ---
    # Values flagged for @roznet review in the PR — they are personal minima,
    # not meteorology calibration. The "standard" option restores catalog
    # defaults for the very same keys so the toggle is fully reversible.
    conservative_params: dict[str, dict[str, float]] = {
        "flight_category": {"red_ceiling_ft": 1500, "red_vis_sm": 5},
        "airport_wind": {"crosswind_green_kt": 10, "crosswind_red_kt": 20},
    }
    standard_params: dict[str, dict[str, float]] = {
        advisory_id: {
            key: _param_default(catalog, advisory_id, key) for key in keys
        }
        for advisory_id, keys in conservative_params.items()
    }

    return Interview(
        questions=[
            InterviewQuestion(
                id="flight_rules",
                title="What kind of flying do you do?",
                help="Controls whether the IFR feasibility advisory is shown.",
                options=[
                    InterviewOption(
                        id="ifr_capable",
                        label="IFR capable",
                        description="Instrument-rated and equipped — keep IFR feasibility.",
                        enabled={"ifr_feasibility": True},
                    ),
                    InterviewOption(
                        id="vfr_only",
                        label="VFR only",
                        description="No IFR — hide the IFR feasibility advisory.",
                        enabled={"ifr_feasibility": False},
                    ),
                ],
            ),
            InterviewQuestion(
                id="icing_equipage",
                title="Is your aircraft FIKI-certified?",
                help="Picks the icing advisory that matches your certification.",
                options=[
                    InterviewOption(
                        id="not_fiki",
                        label="Not FIKI",
                        description="No known-icing certification — use the icing-escape advisory.",
                        enabled={"fiki_icing": False, "icing_escape": True},
                    ),
                    InterviewOption(
                        id="fiki",
                        label="FIKI",
                        description="Certified for flight into known icing — use the FIKI icing advisory.",
                        enabled={"fiki_icing": True, "icing_escape": False},
                    ),
                ],
            ),
            InterviewQuestion(
                id="minimums",
                title="Personal minimums",
                help="Sets a few personal-minimum thresholds. Aggregation and engine choices are unaffected.",
                options=[
                    InterviewOption(
                        id="standard",
                        label="Standard",
                        description="Default thresholds.",
                        params=standard_params,
                    ),
                    InterviewOption(
                        id="conservative",
                        label="Conservative",
                        description="Higher ceiling/visibility floors and tighter crosswind limits.",
                        params=conservative_params,
                    ),
                ],
            ),
        ]
    )
