"""Pure response shapers shared by every agent-facing connector.

These functions take the raw JSON the weatherbrief REST API returns and compress
it into the compact, LLM-sized structures the connectors expose to Claude (MCP)
and ChatGPT (Custom GPT / OpenAPI). They are pure ``dict -> dict`` transforms
with no I/O, so the same shaping — and the same meteorological guardrails baked
into it — is reused by both front-doors.

Guardrail note (do not "optimise" away): the cross-check / per-model detail is
display-only **context for discussion, never a downgrade signal** (#178). The
hint flags are deliberately neutral (``cross_check_present`` / ``per_model_present``)
so a connector LLM is not primed to argue a red down to amber.
"""

from __future__ import annotations

from typing import Any

CROSS_CHECK_NOTE = (
    "Cross-check notes are display-only context for discussion, not a "
    "downgrade signal. Explain the grade with them; do not argue it down."
)

MITIGATION_NOTE = (
    "Mitigations are advice only; they never change the grade. Each reports the "
    "status of the specific sub-issue it addresses if applied (mitigated_status), "
    "NOT the advisory overall — explain the trade-off (e.g. 'flying 6,000 ft would "
    "improve that sub-issue to GREEN — the advisory itself stays RED'), do not "
    "downgrade."
)

CONVECTIVE_NOTE = (
    "thermo.peak.el_top_ft is parcel-derived (the equilibrium level the digest "
    "narrates as 'convective tops'), NOT the model's convective cloud field. "
    "nwp.max_cover_pct ~0 means the model's own convective scheme is quiet "
    "('blue sky'); high CAPE with cover ~0 is the expected pattern, not a "
    "contradiction. assessment_method is which derivation graded the route."
)

ALTERNATES_NOTE = (
    "These are WEATHER-improvement divert candidates — airports near the "
    "destination whose forecast fixes a specific destination deficiency "
    "(flight category, wind, or crosswind) — NOT legally filed operational "
    "alternates. The FAA/EASA 'alternate required?' trigger is advisory, "
    "computed from forecast ceiling/visibility against estimated approach "
    "minima (EASA is a Likely/Marginal/Unlikely band, not a published-plate "
    "lookup; FAA uses fixed regulatory values). Operational suitability — "
    "opening hours, customs/PPR, fuel, NOTAMs, approach availability and "
    "currency — is NOT evaluated here. Combine these with airport/AIP data "
    "before advising a pilot on a divert."
)


def briefing_freshness_status(freshness: dict) -> dict[str, Any]:
    """Map a ``/packs/freshness`` payload to the connector's status + stale note.

    Prefers the tiered refresh-gate decision (what the web refresh button
    actually does at this lead time) over the raw ``fresh`` min-rule: the
    min-rule flips to stale whenever *any* model has a newer run, even when the
    gate has decided a refresh isn't worthwhile yet — which surfaced as a false
    "needs refresh" right after a manual refresh.

    Returns ``{"status": "ready"|"stale", "is_fresh": bool}`` and, when stale,
    ``stale_models`` + a human ``stale_note`` whose wording matches the web
    button (it reuses the gate's own ``reason``). The caller merges these keys
    into its result envelope.
    """
    decision = freshness.get("refresh_decision") or {}
    mode = decision.get("mode")
    if mode is not None:
        # "none" -> nothing worthwhile to refresh; "full"/"realtime" -> an
        # update the button would actually fetch.
        is_fresh = mode == "none"
    else:
        # Gate didn't run (no flight ctx) — fall back to the min-rule.
        is_fresh = freshness.get("fresh", True)

    out: dict[str, Any] = {
        "status": "ready" if is_fresh else "stale",
        "is_fresh": is_fresh,
    }
    if not is_fresh:
        out["stale_models"] = freshness.get("stale_models", [])
        # Use the gate's own reason so the note matches the web button — and
        # only when a refresh is genuinely worthwhile (mode != "none").
        note = decision.get("reason") or "Newer model data is available for this briefing."
        note += " Call refresh_briefing for the latest data."
        pending_models = decision.get("pending_models", [])
        if pending_models:
            note += f" Awaiting updated runs: {', '.join(pending_models)}."
        eta_useful = decision.get("eta_useful")
        if eta_useful:
            note += f" Useful refresh ETA: {eta_useful}."
        out["stale_note"] = note
    return out


def summarize_advisories(advisories: dict) -> list[dict]:
    """Extract advisory information for the agent, retaining per-model detail.

    Unlike a flat status list, this keeps each model's status/detail and the
    ``cross_check`` note plus the ``parameters_used`` thresholds, so the agent
    can see the per-model split and the reasoning behind a grade directly in
    the briefing output. That visible detail is the primary discoverability
    hook (Layer A): it provokes the follow-up question and a ``detail_tool``
    pointer names the drill-down tool.

    The hint flags are deliberately **neutral** (``cross_check_present`` /
    ``per_model_present``, never "model_disagreement"): a valenced flag would
    prime the exact red->amber downgrade the guardrail forbids. The cross-check
    is display-only **context for discussion**, never a downgrade signal
    (#178) — high CAPE matters even when the deterministic model scheme is
    quiet.
    """
    catalog = {c.get("id"): c for c in advisories.get("catalog", [])}
    results = []
    for adv in advisories.get("advisories", []):
        adv_id = adv.get("advisory_id")
        per_model: list[dict] = []
        cross_check_present = False
        for m in adv.get("per_model", []):
            entry_m: dict[str, Any] = {
                "model": m.get("model"),
                "status": m.get("status"),
                "detail": m.get("detail"),
                "affected_pct": m.get("affected_pct"),
            }
            cc = m.get("cross_check")
            if cc:
                cross_check_present = True
                entry_m["cross_check"] = cc
            per_model.append(entry_m)

        # Mitigations (#330): advice-only options that would improve a flagged
        # sub-issue. A neutral presence hook mirrors ``cross_check_present`` — it
        # NEVER alters the grade. The full objects expand only in the same
        # non-green/flagged window as cross_check/per_model (kept compact for
        # green-and-quiet advisories; the agent can still drill in explicitly).
        aggregate_mitigations = adv.get("aggregate_mitigations") or []

        entry: dict[str, Any] = {
            "id": adv_id,
            "status": adv.get("aggregate_status"),
            "detail": adv.get("aggregate_detail"),
            "cross_check_present": cross_check_present,
            "aggregate_mitigations_present": bool(aggregate_mitigations),
            "per_model_present": bool(per_model),
        }
        cat = catalog.get(adv_id)
        if cat:
            entry["name"] = cat.get("name")
            entry["category"] = cat.get("category")

        # Layer A: expand the full per-model detail + thresholds, and point the
        # agent at the drill-down tool, only when there is something worth
        # explaining (a non-green grade or a cross-check note). Green-and-quiet
        # advisories keep just the ``*_present`` flags so every get_briefing
        # call stays compact — their per-model data is almost always noise, and
        # the agent can still call get_advisory_detail explicitly if asked.
        if entry["status"] in ("amber", "red") or cross_check_present:
            entry["per_model"] = per_model
            entry["parameters_used"] = adv.get("parameters_used", {})
            if aggregate_mitigations:
                entry["aggregate_mitigations"] = aggregate_mitigations
            entry["detail_tool"] = "get_advisory_detail"

        results.append(entry)
    return results


def summarize_altitude_table(table: dict) -> dict:
    """Summarize the altitude advisory table for the agent."""
    return {
        "cruise_altitude_ft": table.get("cruise_altitude_ft"),
        "best_below_cruise": table.get("best_below_cruise"),
        "best_above_cruise": table.get("best_above_cruise"),
        "advisory_names": table.get("advisory_names", {}),
        "rows": [
            {
                "altitude_ft": row.get("altitude_ft"),
                "red_count": row.get("red_count"),
                "amber_count": row.get("amber_count"),
                "green_count": row.get("green_count"),
                "statuses": row.get("statuses"),
            }
            for row in table.get("rows", [])
        ],
    }


def _alt_criterion(c: dict | None) -> dict[str, Any] | None:
    """Compact one ceiling/visibility criterion assessment (forecast vs band)."""
    if not c:
        return None
    return {
        "forecast": c.get("forecast"),
        "required_min": c.get("required_min"),
        "required_max": c.get("required_max"),
        "unit": c.get("unit"),
        "verdict": c.get("verdict"),
    }


def _alt_trigger(t: dict | None) -> dict[str, Any] | None:
    """Compact one regulatory destination trigger (FAA or EASA)."""
    if not t:
        return None
    return {
        "status": t.get("status"),
        "reason": t.get("reason"),
        "source": t.get("source"),
        "triggered_by_tempo": t.get("triggered_by_tempo", False),
        "ceiling": _alt_criterion(t.get("ceiling")),
        "visibility": _alt_criterion(t.get("visibility")),
    }


def _alt_candidate(a: dict) -> dict[str, Any]:
    """Compact one divert-candidate airport: weather + suitability + vs-dest flags."""
    out: dict[str, Any] = {
        "icao": a.get("icao"),
        "name": a.get("name"),
        "distance_from_dest_nm": a.get("distance_from_dest_nm"),
        "position": a.get("position"),
        # consensus weather at ETA
        "flight_category": a.get("flight_category"),
        "wind_speed_kt": a.get("wind_speed_kt"),
        "crosswind_kt": a.get("crosswind_kt"),
        "ceiling_ft": a.get("ceiling_ft"),
        "visibility_m": a.get("visibility_m"),
        "best_runway_id": a.get("best_runway_id"),
        # suitability — the hooks for an operational (AIP/airport-data) cross-check
        "has_instrument_approach": a.get("has_instrument_approach"),
        "best_approach_type": a.get("best_approach_type"),
        "longest_runway_ft": a.get("longest_runway_ft"),
        "has_hard_runway": a.get("has_hard_runway"),
        "point_of_entry": a.get("point_of_entry"),
        "is_major": a.get("is_major"),
        # vs-destination
        "better_category": a.get("better_category"),
        "better_wind": a.get("better_wind"),
        "better_crosswind": a.get("better_crosswind"),
        "dominates_destination": a.get("dominates_destination"),
    }
    # Regulatory alternate-minima qualification (compact: verdict + provenance).
    for regime in ("faa", "easa"):
        q = a.get(regime)
        if q:
            out[regime] = {"verdict": q.get("verdict"), "source": q.get("source")}
    return out


def summarize_alternates(alt: dict, *, max_candidates: int = 30) -> dict[str, Any]:
    """Shape a snapshot ``RouteAlternates`` block into the compact connector view.

    Weather-improvement divert candidates plus the advisory FAA/EASA
    "alternate required?" trigger and the nearest-improving pick per deficient
    axis. See ``ALTERNATES_NOTE`` for the weather-vs-operational caveat the
    connectors surface alongside this — operational suitability is intentionally
    out of scope here and must be cross-checked against airport/AIP data.

    ``max_candidates`` bounds the (already backend-ranked, closest-first) list so
    a large catchment can't blow the context window; the overflow count is
    reported as ``candidates_truncated``.
    """
    out: dict[str, Any] = {
        "destination": {
            "icao": alt.get("destination_icao"),
            "flight_category": alt.get("destination_category"),
            "crosswind_kt": alt.get("destination_crosswind_kt"),
            "ceiling_ft": alt.get("destination_ceiling_ft"),
            "visibility_m": alt.get("destination_visibility_m"),
        },
        "eta": alt.get("eta"),
        "corridor_nm": alt.get("corridor_nm"),
        "radius_nm": alt.get("radius_nm"),
        "candidates_evaluated": alt.get("candidates_evaluated"),
        "approach_filter_relaxed": alt.get("approach_filter_relaxed", False),
        "note": ALTERNATES_NOTE,
    }

    req = alt.get("alternate_requirement")
    if req:
        out["requirement"] = {
            "faa": _alt_trigger(req.get("faa")),
            "easa": _alt_trigger(req.get("easa")),
            "caveats": req.get("caveats", []),
        }

    nearest = [
        {
            "axis": p.get("axis"),
            "icao": p.get("icao"),
            "distance_from_dest_nm": p.get("distance_from_dest_nm"),
            "position": p.get("position"),
        }
        for p in alt.get("nearest_improving", [])
        if p.get("icao")
    ]
    if nearest:
        out["nearest_improving"] = nearest

    candidates = alt.get("alternates", []) or []
    out["candidates"] = [_alt_candidate(a) for a in candidates[:max_candidates]]
    if len(candidates) > max_candidates:
        out["candidates_truncated"] = len(candidates) - max_candidates

    return out


def alternates_hook(alt: dict) -> dict[str, Any]:
    """Lightweight get_briefing signal that points the agent at get_alternates.

    Mirrors the advisory ``cross_check_present`` / ``detail_tool`` pattern
    (Layer A discoverability): just the required-flag, the candidate count, and
    the nearest-improving picks — enough to know a divert question is worth a
    drill-in, without paying the full candidate list on the briefing hot path.
    """
    req = alt.get("alternate_requirement") or {}
    faa = (req.get("faa") or {}).get("status")
    easa = (req.get("easa") or {}).get("status")
    candidates = alt.get("alternates", []) or []
    nearest = [
        {
            "axis": p.get("axis"),
            "icao": p.get("icao"),
            "distance_from_dest_nm": p.get("distance_from_dest_nm"),
        }
        for p in alt.get("nearest_improving", [])
        if p.get("icao")
    ]
    out: dict[str, Any] = {
        "destination": alt.get("destination_icao"),
        "candidate_count": len(candidates),
        "detail_tool": "get_alternates",
    }
    if faa is not None or easa is not None:
        out["alternate_required"] = {"faa": faa, "easa": easa}
    if nearest:
        out["nearest_improving"] = nearest
    return out


def advisory_detail(adv: dict, catalog_entry: dict | None) -> dict[str, Any]:
    """Build a generic per-model drill-down summary for one advisory."""
    per_model: list[dict] = []
    for m in adv.get("per_model", []):
        entry_m: dict[str, Any] = {
            "model": m.get("model"),
            "status": m.get("status"),
            "detail": m.get("detail"),
            "affected_pct": m.get("affected_pct"),
            "affected_nm": m.get("affected_nm"),
            "total_nm": m.get("total_nm"),
        }
        cc = m.get("cross_check")
        if cc:
            entry_m["cross_check"] = cc
        # Per-model mitigations (#330): advice-only options for this model's view.
        # Verbatim Mitigation objects; advice only, never a downgrade.
        mits = m.get("mitigations")
        if mits:
            entry_m["mitigations"] = mits
        per_model.append(entry_m)

    result: dict[str, Any] = {
        "advisory_id": adv.get("advisory_id"),
        "aggregate_status": adv.get("aggregate_status"),
        "aggregate_detail": adv.get("aggregate_detail"),
        "per_model": per_model,
        "parameters_used": adv.get("parameters_used", {}),
        "cross_check_note": CROSS_CHECK_NOTE,
    }
    # Aggregate mitigations + guardrail (#330): the two-layer drill-in (mirrors
    # cross_check). Only attach the note when there is something to explain, so
    # mitigation-free advisories stay quiet. Never changes the grade.
    aggregate_mitigations = adv.get("aggregate_mitigations") or []
    has_mitigations = bool(aggregate_mitigations) or any(
        m.get("mitigations") for m in adv.get("per_model", [])
    )
    if aggregate_mitigations:
        result["aggregate_mitigations"] = aggregate_mitigations
    if has_mitigations:
        result["mitigation_note"] = MITIGATION_NOTE
    if catalog_entry:
        result["name"] = catalog_entry.get("name")
        result["category"] = catalog_entry.get("category")
        result["description"] = catalog_entry.get("description")
    return result


def convective_detail(route_analyses: dict, models: list[str]) -> dict[str, Any]:
    """Per-model convective drill-down from the route analyses soundings.

    Splits the two independent derivations so a digest that narrates "tops to
    27,000 ft" reconciles with a quiet model scheme:

    - ``thermo`` — the CAPE/parcel view: CAPE range across the route and the
      peak point (CAPE, the equilibrium-level top the digest calls "convective
      tops", location, and the forecast valid-time vs flight ETA so the diurnal
      timing is explicit).
    - ``nwp`` — the model's own convective scheme: max convective cover %
      (``~0`` is the machine-readable "blue sky" signal) and its convective top.
    - ``assessment_method`` — which derivation actually graded the route.

    cover ~0 next to high CAPE is the expected pattern, not a contradiction —
    this surfaces the data so it can be explained, never argued down.
    """
    points = route_analyses.get("analyses", [])
    out: dict[str, Any] = {}
    for model in models:
        thermo_capes: list[float] = []
        thermo_peak: dict | None = None
        thermo_peak_cape: float | None = None  # raw CAPE for comparison (output rounds)
        nwp_max_cover: float | None = None
        nwp_peak_top: float | None = None
        method_counts: dict[str, int] = {}
        for p in points:
            sounding = (p.get("sounding") or {}).get(model)
            if not sounding:
                continue
            resolved = sounding.get("convective")
            method = resolved.get("method") if resolved else None
            if method:
                method_counts[method] = method_counts.get(method, 0) + 1

            # Parcel/CAPE view — explicit thermo, falling back to the resolved
            # assessment for old packs that never split the two.
            thermo = sounding.get("convective_thermo") or resolved
            if thermo:
                cape = thermo.get("cape_jkg")
                if cape is not None:
                    thermo_capes.append(cape)
                    if thermo_peak is None or cape > thermo_peak_cape:
                        thermo_peak_cape = cape
                        top = thermo.get("top_ft")
                        thermo_peak = {
                            "cape_jkg": round(cape),
                            "el_top_ft": round(top) if top is not None else None,
                            "risk_level": thermo.get("risk_level"),
                            "point_index": p.get("point_index"),
                            "distance_nm": p.get("distance_from_origin_nm"),
                            "waypoint_icao": p.get("waypoint_icao"),
                            "valid_time": p.get("forecast_hour"),
                            "eta": p.get("interpolated_time"),
                        }

            # Model's own convective scheme.
            nwp = sounding.get("convective_nwp")
            if nwp:
                cover = nwp.get("cover_pct")
                if cover is not None:
                    nwp_max_cover = cover if nwp_max_cover is None else max(nwp_max_cover, cover)
                top = nwp.get("top_ft")
                if top is not None:
                    nwp_peak_top = top if nwp_peak_top is None else max(nwp_peak_top, top)

        if not thermo_capes and not method_counts and nwp_max_cover is None:
            continue
        out[model] = {
            "assessment_method": (
                max(method_counts, key=lambda k: method_counts[k]) if method_counts else None
            ),
            "method_counts": method_counts,
            "thermo": {
                "cape_range_jkg": (
                    [round(min(thermo_capes)), round(max(thermo_capes))] if thermo_capes else None
                ),
                "peak": thermo_peak,
            },
            "nwp": nwp_block(nwp_max_cover, nwp_peak_top),
        }
    return out


def nwp_block(max_cover: float | None, peak_top: float | None) -> dict[str, Any]:
    """Model convective-scheme summary. ``max_cover_pct`` and ``peak_top_ft``
    are maximized independently across the route, so they may come from
    different points — the note guards against reading them as one peak."""
    block: dict[str, Any] = {
        "max_cover_pct": round(max_cover) if max_cover is not None else None,
        "peak_top_ft": round(peak_top) if peak_top is not None else None,
    }
    if max_cover is not None or peak_top is not None:
        block["note"] = (
            "max_cover_pct and peak_top_ft are independent route-wide maxima "
            "and may come from different points"
        )
    return block
