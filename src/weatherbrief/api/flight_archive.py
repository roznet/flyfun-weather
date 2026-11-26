"""Past-flights archive query (#1031): ``GET /api/flights/archive``.

Owned flights only (never subscribed), Past section only (ended and not in
the Recent nudge, the same rule as ``GET /api/flights``). Every filter,
count, calendar colour and map bundle is computed here so the clients render
and count nothing (architecture principle: nothing derived on the phone).

Filters, all optional and combined with AND:

- ``q``: the flights-list route matcher (``api/flight_search``), unchanged.
- ``tags``: archive tags (``ICE,TS,...``), OR within the list, read from
  the forecast tags or the reported tags per ``source``.
- ``outcome``: ``flown,cancelled,monitoring,debriefed,differs``, OR.
- ``hide``: the same outcome keys, excluded (the one-tap "hide cancelled and
  monitor only" is ``hide=cancelled,monitoring``).
- ``from`` / ``to``: departure date (UTC), inclusive.

Facets: per tag, the flights matching every filter except ``tags`` that carry
that tag (in the current ``source``); per outcome, every filter except
``outcome`` (``hide`` still applies). So a chip's count is what you get by
turning that chip on alone. ``not_recorded`` counts the flights in the
current filter whose forecast tags were never recorded.

The query reads ``flight_facts`` (``storage/flight_facts.py``); a flight
with no row yet (before the backfill) is listed with its codes and
"not recorded" tags.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from weatherbrief.api.flight_search import matches as search_matches, parse_query
from weatherbrief.db.models import FlightFactsRow
from weatherbrief.storage.flight_facts import ARCHIVE_TAGS, facts_dict, worst_status

OUTCOMES: list[str] = ["flown", "cancelled", "monitoring", "debriefed", "differs"]
VIEWS = ("list", "calendar", "map")
SOURCES = ("forecast", "reported")
MAX_LIMIT = 200


def parse_csv(raw: str | None, allowed: list[str], name: str) -> list[str]:
    """Split a comma list, upper/lower-cased like ``allowed``; ValueError on
    an unknown value."""
    if not raw:
        return []
    upper = allowed[0].isupper()
    vals = [v.strip().upper() if upper else v.strip().lower() for v in raw.split(",") if v.strip()]
    bad = [v for v in vals if v not in allowed]
    if bad:
        raise ValueError(f"Unknown {name}: {', '.join(bad)}")
    return vals


def _outcome_keys(decision: str | None, facts: dict) -> set[str]:
    keys = set()
    if decision in ("flown", "cancelled", "monitoring"):
        keys.add(decision)
    if decision is not None:
        keys.add("debriefed")
    if facts["differs_from_forecast"]:
        keys.add("differs")
    return keys


def _source_tags(item: dict, source: str) -> set[str]:
    return set(item["facts"]["forecast_tags"]) if source == "forecast" else set(item["facts"]["reported_tags"])


def past_items(db: Session, user_id: str, *, now: datetime | None = None) -> list[dict]:
    """Every owned Past flight with its facts, newest first."""
    from weatherbrief.api.flights import _classify_section, _compute_recent_section
    from weatherbrief.api.trips import bulk_trip_refs
    from weatherbrief.storage.debriefs import bulk_get_debriefs
    from weatherbrief.storage.flights import list_flights

    now = now or datetime.now(timezone.utc)
    flights = list_flights(db, user_id)
    debriefs = bulk_get_debriefs(db, [f.id for f in flights])
    recent = _compute_recent_section([(f, debriefs.get(f.id)) for f in flights], now=now)
    past = [
        f for f in flights
        if _classify_section(f, has_debrief=f.id in debriefs, recent_set=recent, now=now) == "past"
    ]
    if not past:
        return []
    facts_rows = {
        r.flight_id: r for r in db.execute(
            select(FlightFactsRow).where(FlightFactsRow.flight_id.in_([f.id for f in past]))
        ).scalars().all()
    }
    trips = bulk_trip_refs(db, user_id)
    items = []
    for f in past:
        facts = facts_dict(facts_rows.get(f.id))
        d = debriefs.get(f.id)
        decision = d.decision.value if d is not None else None
        wps = [w.upper() for w in (f.waypoints or [])]
        trip = trips.get(f.id)
        items.append({
            "flight": f,
            "facts": facts,
            "decision": decision,
            "outcomes": _outcome_keys(decision, facts),
            "dep": facts["departure_icao"] or (wps[0] if wps else None),
            "dest": facts["destination_icao"] or (wps[-1] if len(wps) >= 2 else None),
            "trip": trip,
        })
    return items


def _filter(items, *, tokens, tags, source, outcome, hide, date_from, date_to, skip: str | None = None):
    out = []
    for it in items:
        f = it["flight"]
        if tokens and not search_matches(f.waypoints, f.route_name, tokens, f.description,
                                         it["trip"].name if it["trip"] else None):
            continue
        day = f.departure_time.astimezone(timezone.utc).date()
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        if hide and it["outcomes"] & set(hide):
            continue
        if skip != "tags" and tags and not (_source_tags(it, source) & set(tags)):
            continue
        if skip != "outcome" and outcome and not (it["outcomes"] & set(outcome)):
            continue
        out.append(it)
    return out


def _row(it: dict) -> dict:
    f, facts = it["flight"], it["facts"]
    return {
        "id": f.id,
        "departure_time": f.departure_time.astimezone(timezone.utc).isoformat(),
        "departure": it["dep"],
        "destination": it["dest"],
        "route_name": f.route_name,
        "trip": {"id": it["trip"].id, "name": it["trip"].name} if it["trip"] else None,
        "forecast_tags": facts["forecast_tags"],
        "forecast_worst": worst_status(facts["forecast_tags"].values()),
        "tags_recorded": facts["tags_recorded"],
        "reported_tags": facts["reported_tags"],
        "decision": it["decision"],
        "debriefed": it["decision"] is not None,
        "differs_from_forecast": facts["differs_from_forecast"],
        "distance_nm": facts["distance_nm"],
    }


def _calendar(items, year: int) -> list[dict]:
    days: dict[date, list] = {}
    for it in items:
        day = it["flight"].departure_time.astimezone(timezone.utc).date()
        if day.year == year:
            days.setdefault(day, []).append(it)
    return [
        {"date": d.isoformat(), "count": len(its),
         "worst": worst_status(s for it in its for s in it["facts"]["forecast_tags"].values())}
        for d, its in sorted(days.items())
    ]


def _map(items) -> dict:
    """Airports and route bundles. A flight with no resolved destination, or
    one that lands where it left (a local flight), counts at its departure
    airport only and draws no route; one with no departure coordinates is
    ``unplaced``."""
    airports: dict[str, dict] = {}
    routes: dict[tuple[str, str], dict] = {}
    unplaced = 0
    for it in items:  # newest first: the first line seen per pair is the newest
        facts = it["facts"]
        dep, dest = it["dep"], it["dest"]
        if facts["dep"] is None or dep is None:
            unplaced += 1
            continue
        ends = [(dep, facts["dep"])]
        if dest and facts["dest"] is not None and dest != dep:
            ends.append((dest, facts["dest"]))
        for icao, (lat, lon) in ends:
            a = airports.setdefault(icao, {"icao": icao, "lat": lat, "lon": lon, "count": 0})
            a["count"] += 1
        if len(ends) == 2:
            key = tuple(sorted((dep, dest)))
            r = routes.setdefault(key, {"a": key[0], "b": key[1], "count": 0, "worst": None, "line": None})
            r["count"] += 1
            r["worst"] = worst_status([r["worst"], *facts["forecast_tags"].values()])
            if r["line"] is None and facts["line"]:
                line = facts["line"]
                # Draw every bundle from its alphabetical first end.
                r["line"] = line if dep == key[0] else list(reversed(line))
    return {
        "airports": sorted(airports.values(), key=lambda a: (-a["count"], a["icao"])),
        "routes": sorted(routes.values(), key=lambda r: (-r["count"], r["a"], r["b"])),
        "unplaced": unplaced,
    }


def build_archive(
    db: Session,
    user_id: str,
    *,
    q: str | None = None,
    tags: list[str] | None = None,
    source: Literal["forecast", "reported"] = "forecast",
    outcome: list[str] | None = None,
    hide: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    view: Literal["list", "calendar", "map"] = "list",
    year: int | None = None,
    limit: int = 20,
    offset: int = 0,
    now: datetime | None = None,
) -> dict:
    """The archive response for one request (module docstring)."""
    now = now or datetime.now(timezone.utc)
    items = past_items(db, user_id, now=now)
    kw = dict(tokens=parse_query(q), tags=tags or [], source=source, outcome=outcome or [],
              hide=hide or [], date_from=date_from, date_to=date_to)
    matched = _filter(items, **kw)
    without_tags = _filter(items, **kw, skip="tags")
    without_outcome = _filter(items, **kw, skip="outcome")
    facets = {
        "source": source,
        "tags": {t: sum(1 for it in without_tags if t in _source_tags(it, source)) for t in ARCHIVE_TAGS},
        "outcomes": {o: sum(1 for it in without_outcome if o in it["outcomes"]) for o in OUTCOMES},
        "not_recorded": sum(1 for it in matched if not it["facts"]["tags_recorded"]),
    }
    out: dict = {"view": view, "total": len(matched), "facets": facets}
    if view == "list":
        out.update(limit=limit, offset=offset, rows=[_row(it) for it in matched[offset:offset + limit]])
    elif view == "calendar":
        y = year or now.year
        out.update(year=y, days=_calendar(matched, y))
    else:
        out.update(_map(matched))
    return out
