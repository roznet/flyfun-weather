"""Deterministic "Observed now" readout.

No LLM.  The same string is rendered in the briefing section, the PDF and the
digest context, so it must be reproducible from the payload alone — the digest
quotes it as fact, and a sentence that varied run-to-run would make the
briefing look like it changed when only the phrasing did.

Written in aviation shorthand (ICAO codes, ft MSL, dBZ, NM) so it needs
no per-locale translation, matching the convention ``RefreshDelta`` already
uses for the worsened-conditions banner.

Three things the wording is careful about:

* **It never asserts a clear sky it did not see.**  Where coverage is
  insufficient the line says "no radar coverage over N of M points"; it does
  not say "no echo".
* **Every clause carries its own acquisition time.**  Immutable UTC stamps
  remain truthful in saved briefings; the four sources do not share an instant.
* **It describes, it does not grade.**  Phase 1 computes no verdict, so there
  is no "significant" or "hazardous" anywhere in here — just what was
  measured, where, and how long ago.
"""

from __future__ import annotations

from datetime import timedelta, timezone

from weatherbrief.models.observed import (
    ObservedAnnulus,
    ObservedConditions,
    ObservedField,
    ObservedFlashField,
    ObservedSummaryEntry,
    ObservedTopsField,
)

# Reflectivity at which an echo is worth naming in a one-line summary. Below
# this are weaker echoes; the annuli carry every value regardless,
# and this threshold only governs concise prose.
ECHO_MENTION_DBZ = 20.0


#: Which metric-catalog card explains each clause kind.  Radar, rain rate and
#: lightning all sit on the one "Observed Radar & Lightning" card; cloud tops
#: have their own.  Coverage qualifies the radar clauses, so it points at the
#: same card (whose `limitations` is where the 49.4%-nodata fact lives).
_METRIC_FOR_KIND = {
    "lightning": "observed_surface",
    "reflectivity": "observed_surface",
    "rain_rate": "observed_surface",
    "coverage": "observed_surface",
    "cloud_tops": "observed_tops",
}


def build_summary_entries(conditions: ObservedConditions) -> list[ObservedSummaryEntry]:
    """One clause per available source, in order of operational bite.

    Structured rather than plain strings because the clauses are not uniformly
    shaped — "Radar: peak 38 dBZ…" against "Rain rate to 1.8 mm/h…" — so a
    client rendering them as per-source rows cannot recover the source from the
    prose without guessing.
    """
    by_station = {s.id: s for s in conditions.stations}
    widest = max(conditions.radii_nm) if conditions.radii_nm else 0.0

    clauses = (
        ("lightning", _lightning_clause(conditions.lightning, by_station, widest)),
        ("reflectivity", _reflectivity_clause(conditions.reflectivity, by_station, widest)),
        ("rain_rate", _rain_rate_clause(conditions.rain_rate, by_station, widest)),
        ("cloud_tops", _tops_clause(conditions.cloud_tops, by_station, widest)),
        ("coverage", _coverage_clause(conditions.reflectivity, widest)),
    )
    entries = [
        ObservedSummaryEntry(kind=kind, text=text, metric_id=_METRIC_FOR_KIND.get(kind, ""))
        for kind, text in clauses
        if text
    ]

    if not entries:
        missing = [s.source for s in conditions.sources if not s.available]
        if missing:
            return [
                ObservedSummaryEntry(
                    kind="unavailable",
                    text="No observed data available along the route.",
                )
            ]
    return entries


def build_summary(conditions: ObservedConditions) -> list[str]:
    """Plain-string form of :func:`build_summary_entries`, for PDF and digest."""
    return [entry.text for entry in build_summary_entries(conditions)]


# --- per-source clauses ----------------------------------------------------


def _reflectivity_clause(field: ObservedField | None, by_station, widest) -> str:
    """Peak echo, or a coverage-scoped statement that there is none.

    The two halves are asymmetric on purpose, because the evidence is:

    * A **detection** is positive evidence and is reported wherever it is
      found, including from a disc that is mostly ``nodata``.  The radar
      genuinely saw that echo in the part it could see; suppressing it because
      the surrounding disc is poorly covered would hide a real cell, which is
      strictly more dangerous than reporting it.
    * An **absence** is only as good as the coverage behind it.  "No echo
      along the route" asserted off one covered point in fifty is the
      clear-versus-unknown conflation this whole payload exists to prevent, so
      the claim is scoped to the part of the route the radar can actually see.
    """
    if field is None:
        return ""
    best = _peak(field, widest)
    if best is None or best[1].max_value is None or best[1].max_value < ECHO_MENTION_DBZ:
        looked = _stations_with_coverage(field, widest)
        if looked == 0:
            return ""
        valid_px, total_px = _covered_sample_counts(field, widest)
        sample_coverage = 100 * valid_px / total_px if total_px else 0
        return (
            f"Radar: no echo above {ECHO_MENTION_DBZ:.0f} dBZ in covered radar "
            f"samples along the route ({sample_coverage:.0f}% sample coverage, "
            f"{_age(field)})."
        )
    station_id, annulus = best
    caveat = " (partial radar coverage there)" if annulus.insufficient_coverage else ""
    return (
        f"Radar: peak {annulus.max_value:.0f} dBZ within {widest:.0f} NM of "
        f"{_where(station_id, by_station)}{caveat} ({_age(field)})."
    )


def _rain_rate_clause(field: ObservedField | None, by_station, widest) -> str:
    if field is None:
        return ""
    best = _peak(field, widest)
    if best is None or not best[1].max_value:
        return ""
    station_id, annulus = best
    caveat = ""
    if annulus.coverage_fraction < 1:
        caveat = f" ({annulus.coverage_fraction * 100:.0f}% rain-rate sample coverage there)"
    return (
        f"Rain rate to {annulus.max_value:.1f} mm/h near "
        f"{_where(station_id, by_station)}{caveat} ({_age(field)})."
    )


def _lightning_clause(field: ObservedFlashField | None, by_station, widest) -> str:
    if field is None:
        return ""
    total = 0
    nearest: float | None = None
    nearest_station: str | None = None
    for station in field.stations:
        for annulus in station.annuli:
            if annulus.radius_nm != widest:
                continue
            total += annulus.flash_count
            if annulus.flash_count and annulus.nearest_flash_nm is not None:
                if nearest is None or annulus.nearest_flash_nm < nearest:
                    nearest = annulus.nearest_flash_nm
                    nearest_station = station.station_id
    if total == 0:
        return (
            f"Lightning: no flashes detected within {widest:.0f} NM of the route "
            f"({_age(field)})."
        )
    # Discs overlap, so a flash near two adjacent route points is counted
    # twice; say "detections" rather than implying a flash census.
    proximity = (
        f", nearest {nearest:.0f} NM at {_where(nearest_station, by_station)}"
        if nearest is not None else ""
    )
    return (
        f"Lightning: {total} flash detections within {widest:.0f} NM of the route "
        f"({_age(field)}){proximity}."
    )


def _tops_clause(field: ObservedTopsField | None, by_station, widest) -> str:
    if field is None:
        return ""
    highest: float | None = None
    highest_station: str | None = None
    total = 0
    incomplete = 0
    peak_partial = False
    covered = 0
    clear = 0
    for station in field.stations:
        for annulus in station.annuli:
            if annulus.radius_nm != widest:
                continue
            total += 1
            partial = annulus.coverage_fraction < 1
            incomplete += int(partial)
            if not annulus.insufficient_coverage:
                covered += 1
                if annulus.detected_px == 0:
                    clear += 1
            # Coverage limits negative claims, not positive detections.
            if annulus.detected_px and annulus.highest_fl is not None and (
                highest is None or annulus.highest_fl > highest
            ):
                highest = annulus.highest_fl
                highest_station = station.station_id
                peak_partial = partial
    if total == 0:
        return ""
    if highest is None:
        if covered == 0:
            return f"Cloud tops: insufficient coverage at all {total} sampled route points ({_age(field)})."
        if not incomplete and clear == total:
            return f"Cloud tops: no cloud detected across the sampled corridor ({_age(field)})."
        return (
            f"Cloud tops: no cloud detected in covered portions at {clear} of {total} "
            f"sampled route points; data unavailable or incomplete at {incomplete} of {total} "
            f"points ({_age(field)})."
        )
    parts = [
        f"Cloud tops to {highest * 100:,.0f} ft MSL (geometric) near "
        f"{_where(highest_station, by_station)}"
    ]
    if peak_partial:
        parts.append("partial cloud-top coverage there")
    if clear:
        parts.append(f"no cloud detected in covered portions at {clear} of {total} sampled points")
    if incomplete:
        parts.append(f"data unavailable or incomplete at {incomplete} of {total} points")
    return f"{', '.join(parts)} ({_age(field)})."


def _coverage_clause(field: ObservedField | None, widest) -> str:
    """Say where the radar cannot see, distinctly from where it sees nothing."""
    if field is None:
        return ""
    total = 0
    blind = 0
    for station in field.stations:
        for annulus in station.annuli:
            if annulus.radius_nm != widest:
                continue
            total += 1
            if annulus.insufficient_coverage:
                blind += 1
    if total == 0 or blind == 0:
        return ""
    if blind == total:
        return f"Radar: no coverage anywhere along this route ({_age(field)})."
    return f"Radar: no coverage over {blind} of {total} route points ({_age(field)})."


# --- helpers ---------------------------------------------------------------


def _peak(field: ObservedField, widest: float):
    """Station whose widest disc holds the largest value."""
    best: tuple[str, ObservedAnnulus] | None = None
    for station in field.stations:
        for annulus in station.annuli:
            if annulus.radius_nm != widest or annulus.max_value is None:
                continue
            if best is None or annulus.max_value > best[1].max_value:
                best = (station.station_id, annulus)
    return best


def _stations_with_coverage(field: ObservedField, widest: float) -> int:
    return sum(
        1
        for station in field.stations
        for annulus in station.annuli
        if annulus.radius_nm == widest and not annulus.insufficient_coverage
    )


def _covered_sample_counts(field: ObservedField, widest: float) -> tuple[int, int]:
    """Valid and total pixels for annuli that meet the absence-claim floor."""
    valid_px = 0
    total_px = 0
    for station in field.stations:
        for annulus in station.annuli:
            if annulus.radius_nm != widest or annulus.insufficient_coverage:
                continue
            valid_px += annulus.valid_px
            total_px += annulus.total_px
    return valid_px, total_px


def _station_count(field: ObservedField, widest: float) -> int:
    return sum(
        1
        for station in field.stations
        for annulus in station.annuli
        if annulus.radius_nm == widest
    )


def _where(station_id: str | None, by_station) -> str:
    """Name a place the way a pilot would: an ICAO, else a route distance."""
    station = by_station.get(station_id) if station_id else None
    if station is None:
        return "the route"
    if station.name:
        return station.name
    if station.enroute_distance_nm is not None:
        return f"{station.enroute_distance_nm:.0f} NM along the route"
    return "the route"


def _age(field) -> str:
    """Immutable acquisition stamp, safe to quote from a saved briefing.

    DBZH is a max-reflectivity composite of contributing scans from its
    preceding ten-minute window, not an instantaneous snapshot.
    """
    end = field.valid_time.astimezone(timezone.utc)
    if field.window_minutes > 0:
        start = end - timedelta(minutes=field.window_minutes)
        end_format = "%H:%M" if start.date() == end.date() else "%Y-%m-%d %H:%M"
        return f"observed {start:%Y-%m-%d %H:%M}–{end.strftime(end_format)} UTC"
    return f"observed {end:%Y-%m-%d %H:%M} UTC"
