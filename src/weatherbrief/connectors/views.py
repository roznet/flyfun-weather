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

CONVECTIVE_NOTE = (
    "thermo.peak.el_top_ft is parcel-derived (the equilibrium level the digest "
    "narrates as 'convective tops'), NOT the model's convective cloud field. "
    "nwp.max_cover_pct ~0 means the model's own convective scheme is quiet "
    "('blue sky'); high CAPE with cover ~0 is the expected pattern, not a "
    "contradiction. assessment_method is which derivation graded the route."
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

        entry: dict[str, Any] = {
            "id": adv_id,
            "status": adv.get("aggregate_status"),
            "detail": adv.get("aggregate_detail"),
            "cross_check_present": cross_check_present,
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
        per_model.append(entry_m)

    result: dict[str, Any] = {
        "advisory_id": adv.get("advisory_id"),
        "aggregate_status": adv.get("aggregate_status"),
        "aggregate_detail": adv.get("aggregate_detail"),
        "per_model": per_model,
        "parameters_used": adv.get("parameters_used", {}),
        "cross_check_note": CROSS_CHECK_NOTE,
    }
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
