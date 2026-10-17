"""Server-built surface-chart manifest for a briefing pack (#799).

The web briefing decides which chart sources to show, their tab labels and
captions in TypeScript (``briefing-ui.ts`` ``renderDwdCharts`` +
``utils/temsi.ts``). iOS gets the same answer from here instead of a Swift twin
of those rules: ``PackMetaResponse.charts`` lists, per allowed source, every
chart with its label, caption, image URL, overlay key and a cache key.

Gating matches the web: a source appears when the pack is in its coverage,
within its horizon and has a run cycle; Météo-France additionally needs a live
(non-evicted) option. Met Office appears only when ``METOFFICE_CHARTS_PUBLIC``
is set — the manifest is the same for every caller, so admins do not get it
here (accepted in the issue: admins still have it on the web).

**Cache keys.** ``cache_key`` names the exact bytes a client may keep forever:
``<source>/<run_cycle>/<stem>``, where ``stem`` carries a version token taken
from the chart's own meta (its ``Last-Modified``, or TEMSI's ``date_run``).
The token matters: a DWD cycle is keyed by the *analysis*, and its forecast
charts are replaced in place when the next ICON run lands under the same
cycle; TEMSI re-fetches a validity when AEROWEB re-issues it. Without the
token a client would keep the superseded image under a key whose caption now
describes the new one.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel, Field

from weatherbrief.fetch.chart_cache import parse_run_cycle_dt
from weatherbrief.models import BriefingPackMeta

_DWD_ATTRIBUTION = "Deutscher Wetterdienst (DWD), CC BY 4.0"
_METOFFICE_ATTRIBUTION = "Met Office · © Crown copyright"
_METEOFRANCE_ATTRIBUTION = "Météo-France — AEROWEB"

# Mirrors web/ts/utils/temsi.ts — keep the two in step.
_TEMSI_ZONE_LABEL = {"france": "France", "euroc": "Europe"}
_TEMSI_ZONE_DETAIL = {
    "france": "TEMSI France — SFC to FL150",
    "euroc": "TEMSI EUROC — FL100 to FL450",
}

_TOKEN_STRIP = re.compile(r"[^0-9A-Za-z]")


class PackChartResponse(BaseModel):
    """One chart a client can show in the briefing's chart panel."""

    # Unique within its source. DWD / Met Office: the forecast offset id
    # ("ana", "036"…). Météo-France: "<zone>|<run_cycle>", as the web uses.
    chart_id: str
    label: str
    caption: str
    valid_time: str | None = None
    init_time: str | None = None
    # API path (query included), already percent-encoded: GET it as is.
    url: str
    # Key into the source's overlay JSON (``overlay_url``).
    overlay_key: str
    # "<source>/<run_cycle>/<stem>" — bytes under one key never change.
    cache_key: str
    file_ext: str
    # Bytes are in the server's shared cache right now. False → the URL will
    # 410; show "no longer available" rather than hiding the chart.
    available: bool
    # Worth downloading for offline use (around the ETD), see ``_mark_prefetch``.
    prefetch: bool = False


class PackChartSourceResponse(BaseModel):
    source: str  # "dwd" | "metoffice" | "meteofrance"
    label: str
    attribution: str
    default_chart_id: str
    overlay_url: str
    charts: list[PackChartResponse] = Field(default_factory=list)


class PackChartsManifest(BaseModel):
    # Route inside at least one source's coverage. With no sources this means
    # "charts expected but unavailable" (beyond horizon / refresh failed), as
    # opposed to a route the charts don't cover at all.
    in_coverage: bool = False
    sources: list[PackChartSourceResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_caption(dt: datetime) -> str:
    """``2026-05-08 06Z`` — same shape as the web's ``formatUtcCaption``."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %HZ")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _version_token(meta: dict | None) -> str | None:
    """A compact token identifying the cached bytes, from the chart's meta."""
    if not meta:
        return None
    raw = meta.get("last_modified") or meta.get("date_run") or meta.get("etag")
    if not raw:
        return None
    parsed = _parse_iso(str(raw))
    if parsed is not None:
        return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    token = _TOKEN_STRIP.sub("", str(raw))[:24]
    return token or None


def _cache_key(source: str, run_cycle: str, stem: str, meta: dict | None) -> str:
    token = _version_token(meta)
    return f"{source}/{run_cycle}/{stem}-{token}" if token else f"{source}/{run_cycle}/{stem}"


def temsi_tab_label(zone: str, run_cycle: str, show_day: bool) -> str:
    """Port of ``temsiTabLabel``: ``"France 15Z"`` (``"France 31/15Z"`` with day)."""
    zone_label = _TEMSI_ZONE_LABEL.get(zone, zone)
    m = re.match(r"^\d{4}-\d{2}-(\d{2})T(\d{2})Z$", run_cycle)
    if not m:
        return f"{zone_label} {run_cycle}"
    return f"{zone_label} {m[1]}/{m[2]}Z" if show_day else f"{zone_label} {m[2]}Z"


def temsi_validity_offset(valid: datetime, departure: datetime) -> str | None:
    """Port of ``temsiValidityOffset``: ``"2 h before departure"``, None within 30 min."""
    diff_min = round((valid - departure).total_seconds() / 60)
    if abs(diff_min) <= 30:
        return None
    rounded = round(abs(diff_min) / 60 * 2) / 2
    amount = f"{int(rounded)}" if rounded == int(rounded) else f"{rounded:.1f}"
    return f"{amount} h {'before' if diff_min < 0 else 'after'} departure"


CycleMemo = dict[tuple[str, str], tuple[dict[str, dict], frozenset[str]]]
"""``(source, run_cycle)`` → that cycle's meta and charts on disk, shared by the
manifests of one request so a pack-history list reads each cycle once."""


def _cycle_snapshot(
    source: str, module, data_dir: Path, run_cycle: str, memo: CycleMemo | None,
) -> tuple[dict[str, dict], frozenset[str]]:
    if memo is None:
        return module.cycle_snapshot(data_dir, run_cycle)
    key = (source, run_cycle)
    if key not in memo:
        memo[key] = module.cycle_snapshot(data_dir, run_cycle)
    return memo[key]


def _offset_label(chart_id: str, offset_h: int) -> str:
    return "Analysis" if chart_id == "ana" else f"+{offset_h}h"


# ---------------------------------------------------------------------------
# Per-source builders
# ---------------------------------------------------------------------------


def _offset_source_charts(
    *,
    source: str,
    module,
    run_cycle: str,
    data_dir: Path,
    base_url: str,
    url_segment: str,
    file_ext: str,
    snapshot: tuple[dict[str, dict], frozenset[str]],
    valid_times: dict[str, tuple[datetime, datetime | None, int]] | None = None,
) -> list[PackChartResponse]:
    """Charts for a run-cycle + forecast-offset source (DWD, Met Office)."""
    from weatherbrief.fetch import dwd_charts

    issued = parse_run_cycle_dt(run_cycle)
    known = valid_times or {}
    cycle_meta, on_disk = snapshot
    charts: list[PackChartResponse] = []
    for chart_id in module.CHART_IDS:
        offset_h = module.FORECAST_OFFSETS_H.get(chart_id, 0)
        meta = cycle_meta.get(chart_id)
        available = chart_id in on_disk

        stamp = known.get(chart_id)
        if stamp is not None:
            valid, init, lead = stamp
            caption = f"Valid {_utc_caption(valid)} · ICON {_utc_caption(init)} run +{lead}h"
            init_iso = _iso_z(init)
        elif issued is not None:
            valid = issued + timedelta(hours=offset_h)
            caption = (
                f"Issued {_utc_caption(issued)}"
                if chart_id == "ana"
                else f"Valid {_utc_caption(valid)} · issued {_utc_caption(issued)} +{offset_h}h"
            )
            init_iso = None
        else:
            valid, caption, init_iso = None, "", None

        stem, url_suffix = chart_id, ""
        if source == "dwd" and chart_id == "ana":
            # Phones only ever take the downscaled analysis (~1600 px).
            stem = f"ana_w{dwd_charts.ANALYSIS_SMALL_WIDTH}"
            url_suffix = f"?w={dwd_charts.ANALYSIS_SMALL_WIDTH}"

        charts.append(PackChartResponse(
            chart_id=chart_id,
            label=_offset_label(chart_id, offset_h),
            caption=caption,
            valid_time=_iso_z(valid) if valid is not None else None,
            init_time=init_iso,
            url=f"{base_url}/{url_segment}/{chart_id}{url_suffix}",
            overlay_key=module.chart_type_for(chart_id),
            cache_key=_cache_key(source, run_cycle, stem, meta),
            file_ext=file_ext,
            available=available,
        ))
    return charts


def _dwd_source(
    meta: BriefingPackMeta, data_dir: Path, base_url: str, memo: CycleMemo | None,
) -> PackChartSourceResponse:
    from weatherbrief.fetch import dwd_charts
    from weatherbrief.fetch.dwd_chart_stamp import ForecastStamp

    run_cycle = meta.dwd_charts_run_cycle or ""
    snapshot = _cycle_snapshot("dwd", dwd_charts, data_dir, run_cycle, memo)
    # Same stamps ``dwd_charts.forecast_stamps`` reads, taken from the snapshot.
    stamps = {}
    for cid in dwd_charts.CHART_IDS:
        s = ForecastStamp.from_meta(snapshot[0].get(cid)) if cid != "ana" else None
        if s is not None:
            stamps[cid] = (s.valid_time, s.init_time, s.lead_h)
    charts = _offset_source_charts(
        source="dwd", module=dwd_charts, run_cycle=run_cycle, data_dir=data_dir,
        base_url=base_url, url_segment="dwd-chart", file_ext="png",
        snapshot=snapshot, valid_times=stamps,
    )
    return PackChartSourceResponse(
        source="dwd",
        label="DWD",
        attribution=_DWD_ATTRIBUTION,
        default_chart_id=meta.dwd_charts_default_id or "ana",
        overlay_url=f"{base_url}/dwd-chart-overlay",
        charts=charts,
    )


def _metoffice_source(
    meta: BriefingPackMeta, data_dir: Path, base_url: str, memo: CycleMemo | None,
) -> PackChartSourceResponse:
    from weatherbrief.fetch import metoffice_charts

    run_cycle = meta.metoffice_charts_run_cycle or ""
    charts = _offset_source_charts(
        source="metoffice", module=metoffice_charts, run_cycle=run_cycle, data_dir=data_dir,
        base_url=base_url, url_segment="metoffice-chart", file_ext="gif",
        snapshot=_cycle_snapshot("metoffice", metoffice_charts, data_dir, run_cycle, memo),
    )
    return PackChartSourceResponse(
        source="metoffice",
        label="Met Office",
        attribution=_METOFFICE_ATTRIBUTION,
        default_chart_id=meta.metoffice_charts_default_id or "ana",
        overlay_url=f"{base_url}/metoffice-chart-overlay",
        charts=charts,
    )


def _meteofrance_source(
    options: list[dict[str, str]],
    default_id: str | None,
    data_dir: Path,
    base_url: str,
    departure: datetime | None,
) -> PackChartSourceResponse:
    from weatherbrief.fetch import meteofrance_charts

    show_day = len({o["run_cycle"][:10] for o in options}) > 1
    charts: list[PackChartResponse] = []
    for o in options:
        zone, run_cycle = o["zone"], o["run_cycle"]
        valid = parse_run_cycle_dt(run_cycle)
        detail = _TEMSI_ZONE_DETAIL.get(zone, zone)
        if valid is None:
            caption = detail
        else:
            gap = temsi_validity_offset(valid, departure) if departure else None
            caption = f"{detail} · valid {_utc_caption(valid)}" + (f" ({gap})" if gap else "")
        charts.append(PackChartResponse(
            chart_id=f"{zone}|{run_cycle}",
            label=temsi_tab_label(zone, run_cycle, show_day),
            caption=caption,
            valid_time=_iso_z(valid) if valid else None,
            url=f"{base_url}/meteofrance-chart/{quote(run_cycle, safe='')}/{quote(zone, safe='')}",
            overlay_key=zone,
            cache_key=_cache_key(
                "meteofrance", run_cycle, zone,
                meteofrance_charts.chart_meta(data_dir, run_cycle, zone),
            ),
            file_ext="png",
            # Options reaching here were already filtered to bytes on disk.
            available=True,
        ))
    return PackChartSourceResponse(
        source="meteofrance",
        label="Météo-France",
        attribution=_METEOFRANCE_ATTRIBUTION,
        default_chart_id=default_id or (charts[0].chart_id if charts else ""),
        overlay_url=f"{base_url}/meteofrance-chart-overlay",
        charts=charts,
    )


# ---------------------------------------------------------------------------
# Offline prefetch subset
# ---------------------------------------------------------------------------


def _mark_prefetch(src: PackChartSourceResponse, departure: datetime | None) -> None:
    """Flag the charts an offline download should fetch (#799 decision 5).

    - DWD: the analysis plus the forecasts bracketing the ETD (latest valid at
      or before it, earliest at or after it).
    - Met Office: the two charts nearest the ETD.
    - Météo-France: every option (already a handful, picked around the ETD).

    Without an ETD the source's default chart stands in for it (plus the DWD
    analysis). Only charts the server still holds are flagged.
    """
    live = [c for c in src.charts if c.available]
    if not live:
        return

    def valid_of(c: PackChartResponse) -> datetime | None:
        return _parse_iso(c.valid_time)

    chosen: set[str] = set()
    if src.source == "meteofrance":
        chosen = {c.chart_id for c in live}
    elif departure is None:
        chosen = {src.default_chart_id}
        if src.source == "dwd":
            chosen.add("ana")
    elif src.source == "dwd":
        chosen.add("ana")
        timed = [(valid_of(c), c) for c in live if c.chart_id != "ana" and valid_of(c)]
        before = [t for t in timed if t[0] <= departure]
        after = [t for t in timed if t[0] >= departure]
        if before:
            chosen.add(max(before, key=lambda t: t[0])[1].chart_id)
        if after:
            chosen.add(min(after, key=lambda t: t[0])[1].chart_id)
    else:
        timed = [(valid_of(c), c) for c in live if valid_of(c)]
        timed.sort(key=lambda t: abs((t[0] - departure).total_seconds()))
        chosen = {c.chart_id for _, c in timed[:2]}

    for c in src.charts:
        c.prefetch = c.available and c.chart_id in chosen


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_pack_charts_manifest(
    meta: BriefingPackMeta,
    *,
    data_dir: Path,
    meteofrance_options: list[dict[str, str]],
    meteofrance_default_id: str | None,
    metoffice_public: bool,
    departure_time: datetime | None = None,
    cycle_memo: CycleMemo | None = None,
) -> PackChartsManifest:
    """The chart panel for one pack: allowed sources, in web order.

    ``meteofrance_options`` must already be filtered to live (non-evicted)
    validities — ``packs._live_meteofrance_options`` does that for the
    response's own ``meteofrance_charts_options`` and is reused here.

    ``cycle_memo``: pass one dict across every pack of a request (the history
    list) so packs sharing a run cycle don't re-read its directory.
    """
    base_url = (
        f"/api/flights/{quote(meta.flight_id, safe='')}/packs/"
        f"{quote(meta.fetch_timestamp.isoformat(), safe='')}"
    )
    departure = departure_time
    if departure is not None and departure.tzinfo is None:
        departure = departure.replace(tzinfo=timezone.utc)

    sources: list[PackChartSourceResponse] = []
    if meta.dwd_charts_in_coverage and meta.dwd_charts_within_horizon and meta.dwd_charts_run_cycle:
        sources.append(_dwd_source(meta, data_dir, base_url, cycle_memo))
    if (
        metoffice_public
        and meta.metoffice_charts_in_coverage
        and meta.metoffice_charts_within_horizon
        and meta.metoffice_charts_run_cycle
    ):
        sources.append(_metoffice_source(meta, data_dir, base_url, cycle_memo))
    if (
        meta.meteofrance_charts_in_coverage
        and meta.meteofrance_charts_within_horizon
        and meteofrance_options
    ):
        sources.append(_meteofrance_source(
            meteofrance_options, meteofrance_default_id, data_dir, base_url, departure,
        ))

    for src in sources:
        _mark_prefetch(src, departure)

    in_coverage = bool(
        meta.dwd_charts_in_coverage
        or (metoffice_public and meta.metoffice_charts_in_coverage)
        or meta.meteofrance_charts_in_coverage
    )
    return PackChartsManifest(in_coverage=in_coverage, sources=sources)
