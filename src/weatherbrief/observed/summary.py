"""Deterministic "Observed now" readout.

No LLM.  The same string is rendered in the briefing section, the PDF and the
digest context, so it must be reproducible from the payload alone — the digest
quotes it as fact, and a sentence that varied run-to-run would make the
briefing look like it changed when only the phrasing did.

Written in aviation shorthand (ICAO codes, flight levels, dBZ, NM) so it needs
no per-locale translation, matching the convention ``RefreshDelta`` already
uses for the worsened-conditions banner.

Three things the wording is careful about:

* **It never asserts a clear sky it did not see.**  Where coverage is
  insufficient the line says "no radar coverage over N of M points"; it does
  not say "no echo".
* **Every clause carries its own age.**  The four sources do not share an
  instant and the summary does not pretend otherwise.
* **It describes, it does not grade.**  Phase 1 computes no verdict, so there
  is no "significant" or "hazardous" anywhere in here — just what was
  measured, where, and how long ago.
"""

from __future__ import annotations

from weatherbrief.models.observed import (
    ObservedAnnulus,
    ObservedConditions,
    ObservedField,
    ObservedFlashField,
    ObservedTopsField,
)

# Reflectivity at which an echo is worth naming in a one-line summary.  Below
# this is cloud and drizzle returns that a pilot would not route around; the
# annuli carry every value regardless, this only governs the prose.
ECHO_MENTION_DBZ = 20.0


def build_summary(conditions: ObservedConditions) -> list[str]:
    """One clause per available source, in order of operational bite."""
    lines: list[str] = []
    by_station = {s.id: s for s in conditions.stations}
    widest = max(conditions.radii_nm) if conditions.radii_nm else 0.0

    for clause in (
        _lightning_clause(conditions.lightning, by_station, widest),
        _reflectivity_clause(conditions.reflectivity, by_station, widest),
        _rain_rate_clause(conditions.rain_rate, by_station, widest),
        _tops_clause(conditions.cloud_tops, by_station, widest),
        _coverage_clause(conditions.reflectivity, widest),
    ):
        if clause:
            lines.append(clause)

    if not lines:
        missing = [s.source for s in conditions.sources if not s.available]
        if missing:
            return ["No observed data available along the route."]
        return []
    return lines


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
        total = _station_count(field, widest)
        if looked == 0:
            return ""
        if looked == total:
            return (
                f"Radar: no echo above {ECHO_MENTION_DBZ:.0f} dBZ along the "
                f"route ({_age(field)})."
            )
        return (
            f"Radar: no echo above {ECHO_MENTION_DBZ:.0f} dBZ where the radar "
            f"covers the route ({looked} of {total} points, {_age(field)})."
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
    return (
        f"Rain rate to {annulus.max_value:.1f} mm/h near "
        f"{_where(station_id, by_station)} ({_age(field)})."
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
    window = field.window_minutes or 10.0
    if total == 0:
        return f"Lightning: none within {widest:.0f} NM in the last {window:.0f} min."
    # Discs overlap, so a flash near two adjacent route points is counted
    # twice; say "detections" rather than implying a flash census.
    return (
        f"Lightning: {total} flash detections within {widest:.0f} NM of the route "
        f"in the last {window:.0f} min, nearest {nearest:.0f} NM at "
        f"{_where(nearest_station, by_station)} ({_age(field)})."
    )


def _tops_clause(field: ObservedTopsField | None, by_station, widest) -> str:
    if field is None:
        return ""
    highest: float | None = None
    highest_station: str | None = None
    multilayer = 0
    covered = 0
    clear = 0
    for station in field.stations:
        for annulus in station.annuli:
            if annulus.radius_nm != widest:
                continue
            if annulus.insufficient_coverage:
                continue
            covered += 1
            if annulus.detected_px == 0:
                clear += 1
            if annulus.highest_fl is not None and (
                highest is None or annulus.highest_fl > highest
            ):
                highest = annulus.highest_fl
                highest_station = station.station_id
            if int(annulus.quality_method.get("9", 0)) > 0:
                multilayer += 1
    if covered == 0:
        return ""
    if highest is None:
        return f"Cloud tops: clear over the whole corridor ({_age(field)})."
    parts = [
        f"Cloud tops to FL{highest:.0f} near {_where(highest_station, by_station)}"
    ]
    if clear:
        parts.append(f"clear at {clear} of {covered} points")
    if multilayer:
        # quality_method 9 is the retrieval's own multi-layer-suspect flag —
        # the case where a single cloud-top number is least trustworthy.
        parts.append(f"multi-layer suspected at {multilayer} of {covered}")
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
        return "Radar: no coverage anywhere along this route."
    return f"Radar: no coverage over {blind} of {total} route points."


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
    minutes = field.age_minutes
    if minutes < 1:
        return "observed just now"
    return f"observed {minutes:.0f} min ago"
