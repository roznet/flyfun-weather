"""Proxy METAR/TAF for a terminal with no report of its own (#767).

Some fields publish no METAR or TAF (EGTF Fairoaks, EGLK Blackbushe); pilots
there read a nearby station's (EGLL, EGLF). The station is already in the
route corridor fetch, so nothing new is fetched: this module only picks which
corridor row stands in for the field, and records it on the field's row as
:class:`ObsProxy`. The station's report is never copied onto the field: every
consumer reads through ``RouteObservations.terminal``.

Rules (agreed on the issue, owner 2026-10-11):

- METAR proxy within :data:`METAR_MAX_NM`, TAF proxy within :data:`TAF_MAX_NM`.
- Automatic pick only when the elevations differ by at most
  :data:`MAX_ELEVATION_DIFF_FT`: ceilings are reported above the station, so
  a station at another height misreads the field's cloud base. Unknown
  elevation fails the gate.
- METAR and TAF chosen independently.
- A METAR proxy whose latest METAR is older than :data:`MAX_METAR_AGE` is
  skipped (EGLF overnight); a TAF proxy needs a TAF valid at ETA.
- Curated overrides (``configs/obs_proxy.json``) replace the automatic
  choice for that field: their stations are tried in order, skip the distance
  and elevation gates, still need a fresh report, and **nothing else is
  tried** when none qualifies. EGLK lists EGLF so that RAF Odiham (EGVO, 6.5
  NM, passes both gates but reports irregularly) is never picked; overnight,
  with EGLF closed, EGLK reads "METAR unavailable". A field's entry may name
  stations per kind (``{"metar": [...], "taf": [...]}``); a kind it leaves out
  falls back to the automatic pick.
- Stable for the flight: a ``pinned`` station that still qualifies is kept
  (the live tick passes the previous pick), so a closer station starting to
  report does not switch it mid-flight.

Pure apart from :func:`load_overrides` (one small JSON file, cached).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from weatherbrief.models.observations import AirportObservation, ObsProxy, RouteObservations

logger = logging.getLogger(__name__)

METAR_MAX_NM = 10.0
TAF_MAX_NM = 15.0
MAX_ELEVATION_DIFF_FT = 100.0
MAX_METAR_AGE = timedelta(minutes=90)

_OVERRIDES_PATH = Path(__file__).resolve().parents[3] / "configs" / "obs_proxy.json"


Overrides = dict[str, dict[str, list[str]]]


def _stations(value) -> list[str] | None:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return None
    return [s.upper() for s in value if isinstance(s, str) and s]


@lru_cache(maxsize=4)
def load_overrides(path: str | None = None) -> Overrides:
    """Field ICAO -> {"metar": [...], "taf": [...]} from ``configs/obs_proxy.json``.

    A plain list (or string) applies to both kinds. A missing or unreadable
    file means no overrides (the automatic pick still runs); it is logged,
    never raised. Cached per process: an edit to the file takes effect on the
    next restart.
    """
    p = Path(path) if path else _OVERRIDES_PATH
    try:
        data = json.loads(p.read_text())
    except FileNotFoundError:
        return {}
    except Exception:
        logger.warning("Unreadable observation-proxy overrides %s — ignored", p, exc_info=True)
        return {}
    return parse_overrides(data.get("overrides") or {})


def parse_overrides(raw: dict) -> Overrides:
    """The config's ``overrides`` block, normalised (see :func:`load_overrides`)."""
    out: Overrides = {}
    for field, value in raw.items():
        if isinstance(value, dict):
            kinds = {k: _stations(value.get(k)) for k in ("metar", "taf") if k in value}
            kinds = {k: v for k, v in kinds.items() if v is not None}
        else:
            both = _stations(value)
            kinds = {"metar": both, "taf": list(both)} if both is not None else {}
        if kinds:
            out[field.upper()] = kinds
    return out


def _distance_bearing(field: AirportObservation, station: AirportObservation) -> tuple[float, float] | None:
    """(distance NM, true bearing field -> station), or None when unplaceable."""
    if None in (field.lat, field.lon, station.lat, station.lon):
        return None
    from euro_aip.models.navpoint import NavPoint

    bearing, dist = NavPoint(latitude=field.lat, longitude=field.lon).haversine_distance(
        NavPoint(latitude=station.lat, longitude=station.lon),
    )
    return float(dist), float(bearing)


def _has_metar(a: AirportObservation) -> bool:
    return a.has_metar or bool(a.metar_raw)


def _fresh_metar(a: AirportObservation, now: datetime) -> bool:
    return _has_metar(a) and a.metar_time is not None and now - a.metar_time <= MAX_METAR_AGE


def _usable_taf(a: AirportObservation) -> bool:
    return a.has_taf and a.taf_valid_at_eta is True


def resolve_obs_proxy(
    field: AirportObservation,
    corridor: list[AirportObservation],
    *,
    kind: str,
    elevations: dict[str, float | None],
    overrides: Overrides | None = None,
    now: datetime,
    pinned: str | None = None,
) -> ObsProxy | None:
    """The station standing in for ``field``'s ``kind`` ("metar" / "taf").

    Candidates are the corridor rows that already carry the report. With a
    curated override for this field and kind, only its stations, in order;
    otherwise the automatic candidates, nearest first. Either way the
    ``pinned`` station goes first while it still qualifies. None when nothing
    qualifies (the field stays "unavailable", which is the honest answer).
    """
    max_nm = METAR_MAX_NM if kind == "metar" else TAF_MAX_NM
    usable = (lambda a: _fresh_metar(a, now)) if kind == "metar" else _usable_taf
    by_icao = {a.icao.upper(): a for a in corridor if a.icao.upper() != field.icao.upper()}
    curated_kinds = (overrides or {}).get(field.icao.upper(), {})
    curated = curated_kinds.get(kind)
    if curated is not None:
        curated = [s for s in curated if s != field.icao.upper()]
    f_elev = elevations.get(field.icao)

    def candidate(icao: str, source: str) -> ObsProxy | None:
        station = by_icao.get(icao)
        if station is None or not usable(station):
            return None
        geo = _distance_bearing(field, station)
        s_elev = elevations.get(station.icao)
        diff = None if f_elev is None or s_elev is None else s_elev - f_elev
        if source == "auto":
            if geo is None or geo[0] > max_nm:
                return None
            if diff is None or abs(diff) > MAX_ELEVATION_DIFF_FT:
                return None
        return ObsProxy(
            icao=station.icao,
            distance_nm=round(geo[0], 1) if geo is not None else 0.0,
            bearing_deg=round(geo[1]) % 360 if geo is not None else None,
            elevation_diff_ft=round(diff) if diff is not None else None,
            source=source,
        )

    if curated is not None:
        order = curated
        if pinned and pinned.upper() in curated:
            order = [pinned.upper()] + [s for s in curated if s != pinned.upper()]
        for icao in order:
            hit = candidate(icao, "override")
            if hit is not None:
                return hit
        return None
    if pinned:
        hit = candidate(pinned.upper(), "auto")
        if hit is not None:
            return hit
    autos = []
    for icao in by_icao:
        hit = candidate(icao, "auto")
        if hit is not None:
            autos.append(hit)
    return min(autos, key=lambda p: (p.distance_nm, p.icao)) if autos else None


def apply_obs_proxies(
    observations: RouteObservations,
    fields: list[str],
    *,
    elevations: dict[str, float | None],
    now: datetime,
    overrides: Overrides | None = None,
    pinned: dict[str, tuple[str | None, str | None]] | None = None,
) -> list[str]:
    """Set ``metar_proxy`` / ``taf_proxy`` on each terminal in ``fields`` that
    lacks its own report. Returns the ICAOs that got a proxy (for the caller
    to rebuild their runway + wind picture). ``pinned``: field -> (METAR
    station, TAF station) from the previous pick.

    A field with its own METAR keeps ``metar_proxy`` None; one that publishes
    a TAF (even one not valid at ETA) keeps ``taf_proxy`` None: an expired TAF
    at a field that issues them reads "no TAF for ETA", not another station's.
    """
    changed: list[str] = []
    seen: set[str] = set()
    for icao in fields:
        field = observations.airport(icao)
        if field is None or field.icao in seen:
            continue
        seen.add(field.icao)
        pin_m, pin_t = (pinned or {}).get(field.icao.upper(), (None, None))
        field.metar_proxy = None if _has_metar(field) else resolve_obs_proxy(
            field, observations.airports, kind="metar", elevations=elevations,
            overrides=overrides, now=now, pinned=pin_m,
        )
        field.taf_proxy = None if (field.has_taf or field.taf_raw) else resolve_obs_proxy(
            field, observations.airports, kind="taf", elevations=elevations,
            overrides=overrides, now=now, pinned=pin_t,
        )
        if field.metar_proxy is not None or field.taf_proxy is not None:
            changed.append(field.icao)
            picked = (
                field.metar_proxy.icao if field.metar_proxy else None,
                field.taf_proxy.icao if field.taf_proxy else None,
            )
            # INFO only when the pick is new or switched; a pinned repeat on
            # every live tick is DEBUG.
            logger.log(
                logging.DEBUG if picked == (pin_m, pin_t) else logging.INFO,
                "Observation proxy for %s: METAR %s, TAF %s", field.icao,
                field.metar_proxy.icao if field.metar_proxy else "-",
                field.taf_proxy.icao if field.taf_proxy else "-",
            )
    return changed


def pins_from(observations: RouteObservations | None) -> dict[str, tuple[str | None, str | None]]:
    """The previous pick per field, for :func:`apply_obs_proxies`'s ``pinned``."""
    if observations is None:
        return {}
    return {
        a.icao.upper(): (
            a.metar_proxy.icao if a.metar_proxy else None,
            a.taf_proxy.icao if a.taf_proxy else None,
        )
        for a in observations.airports
        if a.metar_proxy is not None or a.taf_proxy is not None
    }
