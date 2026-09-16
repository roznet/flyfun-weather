"""Intensity classes for observed radar reflectivity and rain rate.

The observed clauses used to read "peak 44 dBZ" and "rain rate to 18 mm/h",
which is precise and, for a pilot scanning a briefing, close to unreadable.
This module attaches the word: "heavy echo, peak 44 dBZ".

Four properties carry the design.

**One ladder, defined in dBZ, derived in mm/h.**  The categories are the
NWS VIP levels, the only published word-scale for reflectivity in aviation use
(AIM: "avoid level 3 or greater") and the scale the airborne-radar colour
convention approximates.  The rain-rate boundaries are then *derived* from the
dBZ ones through Marshall-Palmer ``Z = 200 R^1.6``, rather than taken from a
surface-observation table, so the two clauses in the same paragraph cannot
contradict each other.  They very nearly did: on the old ramps a 35 dBZ echo
was "moderate" in the legend while its ~5.6 mm/h equivalent was HEAVY to
``analysis.sounding.precipitation._classify_intensity``.

The derived boundaries land where the published tables do anyway -- 2.5 mm/h is
exactly the WMO light/moderate line, and 10/30/50 track the UK Met Office
*shower* table -- so the ladder is standards-compatible without being a copy of
any one of them.  Exact Z-R values against the rounded boundaries used here:

    18 dBZ -> 0.49      light floor            0.5
    30 dBZ -> 2.73      light/moderate         2.5
    41 dBZ -> 13.3      moderate/heavy        10
    46 dBZ -> 27.3      heavy/very heavy      30
    50 dBZ -> 48.6      very heavy/extreme    50

Rounding costs at most ~2 dBZ, well inside the factor-of-two spread between
Z-R relations (convective ``300 R^1.4`` and snow ``2000 R^2.0`` differ by far
more), which is also why this module offers no dBZ->mm/h converter: OPERA ships
RATE with its own conversion already applied and we read that product rather
than second-guessing it.  ``test_intensity.py`` pins the two ladders against
each other so a future edit to one cannot silently drift from the other.

**Not regional.**  Unlike visibility or QNH (see ``weatherbrief.units``), this
ladder does not switch on the route's region.  OPERA is a pan-European
composite with no US coverage, so a US table would never be exercised; and
there is no European dBZ word-scale to switch *to*, only per-service colour
choices.  Two pilots reading the same pixel must get the same word.

**Phase-neutral.**  The word qualifies an *echo*, never "rain".  OPERA RATE is
liquid-equivalent and the observed payload carries no precipitation phase at
all, so the same 30 dBZ is ~2.7 mm/h as rain and roughly a third of that as
snow -- while snow is the bigger visibility problem.  Callers say "heavy echo"
and "heavy precip", and ``enroute_precip`` remains the only surface that knows
the phase.

**A class, not a verdict.**  ``designs/current-conditions.md`` sets the phase-1
rule that this surface describes and does not grade.  "Heavy" here restates the
measurement on a published scale; it is not a claim about this flight.  Callers
keep the number in the sentence so the word stays a gloss, and nothing here
reaches ``HighlightSeverity`` or any advisory status.
"""

from __future__ import annotations

from enum import Enum


class EchoIntensity(str, Enum):
    """Intensity class shared by the reflectivity and rain-rate clauses.

    VIP 5 (intense) and VIP 6 (extreme) are merged: a one-line summary does not
    earn six words, and both mean "do not go near it".
    """

    LIGHT = "light"            # VIP 1
    MODERATE = "moderate"      # VIP 2
    HEAVY = "heavy"            # VIP 3
    VERY_HEAVY = "very_heavy"  # VIP 4
    EXTREME = "extreme"        # VIP 5-6


#: Lower bound of each class in dBZ, ascending. VIP level boundaries.
DBZ_BANDS: tuple[tuple[float, EchoIntensity], ...] = (
    (18.0, EchoIntensity.LIGHT),
    (30.0, EchoIntensity.MODERATE),
    (41.0, EchoIntensity.HEAVY),
    (46.0, EchoIntensity.VERY_HEAVY),
    (50.0, EchoIntensity.EXTREME),
)

#: Lower bound of each class in mm/h, ascending. Marshall-Palmer equivalents of
#: :data:`DBZ_BANDS`, rounded (see the module docstring for the exact values).
RATE_BANDS: tuple[tuple[float, EchoIntensity], ...] = (
    (0.5, EchoIntensity.LIGHT),
    (2.5, EchoIntensity.MODERATE),
    (10.0, EchoIntensity.HEAVY),
    (30.0, EchoIntensity.VERY_HEAVY),
    (50.0, EchoIntensity.EXTREME),
)

#: English word per class.  The clause prose this feeds is English throughout
#: ("Radar: peak 44 dBZ within 20 NM of ..."), so translating the word alone
#: would produce a mixed-language sentence.  Clients that want a localised
#: rendering build it from ``ObservedSummaryEntry.category`` and their own
#: locale files instead of parsing the prose.
INTENSITY_LABELS: dict[EchoIntensity, str] = {
    EchoIntensity.LIGHT: "light",
    EchoIntensity.MODERATE: "moderate",
    EchoIntensity.HEAVY: "heavy",
    EchoIntensity.VERY_HEAVY: "very heavy",
    EchoIntensity.EXTREME: "extreme",
}


def _classify(
    value: float | None, bands: tuple[tuple[float, EchoIntensity], ...]
) -> EchoIntensity | None:
    """Highest band whose floor ``value`` reaches, or None below the first.

    None means "below the scale", not "unknown": a 12 dBZ return is a real
    detection that no published level names, and the caller says so by omitting
    the word rather than by inventing a sixth class for it.
    """
    if value is None:
        return None
    match: EchoIntensity | None = None
    for floor, intensity in bands:
        if value >= floor:
            match = intensity
        else:
            break
    return match


def classify_dbz(dbz: float | None) -> EchoIntensity | None:
    """Intensity class for a reflectivity in dBZ (None below VIP 1)."""
    return _classify(dbz, DBZ_BANDS)


def classify_rate(mm_h: float | None) -> EchoIntensity | None:
    """Intensity class for a surface rain rate in mm/h (None below 0.5)."""
    return _classify(mm_h, RATE_BANDS)


def intensity_label(intensity: EchoIntensity | None) -> str:
    """English word for an intensity class ("" when there is no class)."""
    if intensity is None:
        return ""
    return INTENSITY_LABELS[intensity]
