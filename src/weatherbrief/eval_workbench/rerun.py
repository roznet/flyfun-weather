"""Re-run route advisories from a pack's saved data and diff vs the baseline.

Concept: a corpus pack stores ``route_analyses.json`` (the derived per-point
analyses) + ``cross_section.json`` + ``elevation_profile.json``, which is enough
to re-evaluate *all* advisory logic with no re-fetch (see
``run_advisories_from_pack``). Comparing that re-run against the pack's saved
``route_advisories.json`` answers "did an advisory-code change move the rating
for this real briefing?".

Caveats (dev-only tooling):
* ``cross_section.json`` is stripped from prod packs at T1 retention (~30 days),
  so a faithful re-run only works on packs captured fresh.
* The saved manifest records ``aggregation`` + ``models`` but not the full
  per-evaluator params/methods, so the re-run reuses what it can and otherwise
  falls back to current defaults — the diff is best read as a change detector,
  not a perfectly param-pinned regression (that needs a frozen baseline run with
  a pinned eval profile, a planned follow-up).
* Airport-condition advisories depend on a DB-backed recompute callback that we
  do not wire here, so they may differ spuriously and are flagged in the diff.
"""

from __future__ import annotations

import json
from pathlib import Path

from weatherbrief.models import AdvisoryAggregation, RouteAdvisoriesManifest

# Advisories whose re-run needs a DB-backed recompute we intentionally skip
# here; flagged so a diff consumer can discount them.
#
# ``approach_feasibility`` (#509) belongs here for a second reason: it also
# needs ``airports_db_path`` (nav.db procedures), which this module never passes
# to ``run_advisories_from_pack``, so its re-run is always UNAVAILABLE.
#
# NB ``airport_conditions`` matches no registered advisory id — the airport
# category is flight_category / airport_wind / density_altitude / llws — so the
# first two are currently unflagged. Pre-existing, left alone here rather than
# silently changing diff output for advisories this change does not touch.
_AIRPORT_CONDITION_ADVISORIES = {
    "airport_conditions", "density_altitude", "llws", "approach_feasibility",
}


def load_saved_manifest(pack_dir: Path) -> RouteAdvisoriesManifest | None:
    """The pack's saved advisory baseline, or None if absent."""
    path = pack_dir / "route_advisories.json"
    if not path.exists():
        return None
    return RouteAdvisoriesManifest.model_validate_json(path.read_text(encoding="utf-8"))


def rerun_manifest(
    pack_dir: Path, baseline: RouteAdvisoriesManifest | None
) -> RouteAdvisoriesManifest:
    """Recompute advisories from the pack's saved analyses without persisting.

    Reuses the baseline's aggregation + models + altitudes so the diff isolates
    the advisory logic rather than configuration. Raises RuntimeError if the
    pack lacks the artifacts needed to recompute (e.g. a T1-stripped pack with
    no ``cross_section.json``).
    """
    from weatherbrief.tasks.advise import run_advisories_from_pack

    # Pre-check the inputs the recompute needs. ``cross_section.json`` is stripped
    # from prod packs at T1 (~30 days), so stale packs can't be re-run — surface
    # that as a clean message rather than a deep traceback.
    missing = []
    if not (pack_dir / "route_analyses.json").exists():
        missing.append("route_analyses.json")
    if not (pack_dir / "cross_section.json").exists() and not (
        pack_dir / "cross_section.json.gz"
    ).exists():
        missing.append("cross_section.json")
    if missing:
        raise RuntimeError(
            f"missing {', '.join(missing)} (T1-stripped — pull this pack fresh)"
        )

    aggregation = (
        AdvisoryAggregation(baseline.aggregation)
        if baseline and baseline.aggregation
        else None
    )
    try:
        result = run_advisories_from_pack(
            pack_dir,
            cruise_altitude_ft=(baseline.cruise_altitude_ft or None) if baseline else None,
            flight_ceiling_ft=(baseline.flight_ceiling_ft or None) if baseline else None,
            advisory_models=(baseline.models or None) if baseline else None,
            aggregation=aggregation,
            persist=False,
        )
    except Exception as exc:  # noqa: BLE001 — any failure becomes a clean 422
        raise RuntimeError(f"advisory re-run failed: {exc}") from exc
    if result.manifest is None:
        raise RuntimeError(result.error or "advisory re-run produced no manifest")
    return result.manifest


def diff_manifests(
    saved: RouteAdvisoriesManifest | None, candidate: RouteAdvisoriesManifest
) -> list[dict]:
    """Per-advisory changes between saved baseline and the re-run candidate.

    Returns a row per advisory whose aggregate status or any per-model status
    changed (or that appeared/disappeared). Each row:
    ``{advisory_id, saved, candidate, per_model_changes, airport_conditions_flag}``.
    """

    def by_id(m: RouteAdvisoriesManifest | None) -> dict:
        return {a.advisory_id: a for a in (m.advisories if m else [])}

    s_idx, c_idx = by_id(saved), by_id(candidate)
    changes: list[dict] = []
    for aid in sorted(set(s_idx) | set(c_idx)):
        s, c = s_idx.get(aid), c_idx.get(aid)
        s_agg = s.aggregate_status.value if s else None
        c_agg = c.aggregate_status.value if c else None

        def per_model(a):
            return {m.model: m.status.value for m in a.per_model} if a else {}

        s_pm, c_pm = per_model(s), per_model(c)
        pm_changes = {
            model: {"saved": s_pm.get(model), "candidate": c_pm.get(model)}
            for model in sorted(set(s_pm) | set(c_pm))
            if s_pm.get(model) != c_pm.get(model)
        }
        if s_agg == c_agg and not pm_changes:
            continue
        changes.append({
            "advisory_id": aid,
            "saved": s_agg,
            "candidate": c_agg,
            "per_model_changes": pm_changes,
            "airport_conditions_flag": aid in _AIRPORT_CONDITION_ADVISORIES,
        })
    return changes


def rerun_manifest_deep(
    pack_dir: Path, baseline: RouteAdvisoriesManifest | None
) -> RouteAdvisoriesManifest:
    """Like rerun_manifest but ALSO recomputes the soundings first.

    Use this when the code change is in the *sounding/analysis* layer
    (assess_convective_thermo etc.), not just the advisory evaluator — the saved
    route_analyses.json holds the OLD soundings, so a plain re-grade would show
    no diff. Works in a throwaway copy so the saved pack is never mutated.
    Needs cross_section + route_points.
    """
    import shutil
    import tempfile

    from weatherbrief.eval_workbench.situations import load_snapshot_from_pack
    from weatherbrief.tasks.advise import run_advisories_from_pack
    from weatherbrief.tasks.analyze import run_analysis_from_pack
    from weatherbrief.tasks.artifacts import save_route_analyses

    has_cs = (pack_dir / "cross_section.json").exists() or (
        pack_dir / "cross_section.json.gz"
    ).exists()
    if not has_cs or not (pack_dir / "route_points.json").exists():
        raise RuntimeError("missing cross_section / route_points (pull this pack fresh)")
    snap = load_snapshot_from_pack(pack_dir)
    if snap is None:
        raise RuntimeError("no snapshot")

    tmp = Path(tempfile.mkdtemp(prefix="rerun_deep_"))
    try:
        dst = tmp / "pack"
        shutil.copytree(pack_dir, dst)
        # Recompute the soundings with current code AND write them back, because
        # ``run_advisories_from_pack`` below re-reads ``route_analyses.json`` off
        # disk. ``run_analysis_from_pack`` only *returns* the manifest — it
        # writes nothing — so for two years this call recomputed 4,000+ soundings
        # and threw every one away, and ``--deep`` reported "no change" for
        # exactly the sounding-layer changes it exists to validate (#578).
        analysis = run_analysis_from_pack(dst, snap.route, snap.departure_time)
        if analysis.route_analyses_manifest is None:
            raise RuntimeError("deep re-run recomputed no analyses")
        # No sidecar: nothing downstream of here reads it, and it costs a gzip
        # of every profile in the pack.
        save_route_analyses(dst, analysis.route_analyses_manifest, sidecar=False)
        aggregation = (
            AdvisoryAggregation(baseline.aggregation)
            if baseline and baseline.aggregation
            else None
        )
        result = run_advisories_from_pack(
            dst,
            cruise_altitude_ft=(baseline.cruise_altitude_ft or None) if baseline else None,
            flight_ceiling_ft=(baseline.flight_ceiling_ft or None) if baseline else None,
            advisory_models=(baseline.models or None) if baseline else None,
            aggregation=aggregation,
            persist=False,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if result.manifest is None:
        raise RuntimeError(result.error or "deep re-run produced no manifest")
    return result.manifest


def rerun_diff(
    pack_dir: Path, deep: bool = False, check_invariants: bool = False
) -> dict:
    """Full re-run + diff for one pack. Returns a JSON-serialisable summary.

    ``deep=True`` recomputes the soundings first (for sounding/analysis-layer
    changes); otherwise only the advisory evaluators are re-run.

    ``check_invariants=True`` also runs the published-extent invariants
    (``analysis/advisories/invariants.py``) over the re-run manifest and counts
    the aggregates that mask a flagged model. A diff answers "did this change
    move a rating?"; the invariants answer "is what we publish self-consistent
    at all?" — and only real packs carry the geometry that breaks it (#578).
    """
    from weatherbrief.analysis.advisories import invariants

    saved = load_saved_manifest(pack_dir)
    candidate = (
        rerun_manifest_deep(pack_dir, saved) if deep else rerun_manifest(pack_dir, saved)
    )
    changes = diff_manifests(saved, candidate)
    out = {
        "had_baseline": saved is not None,
        "changed_count": len(changes),
        "changes": changes,
    }
    if check_invariants:
        out["invariant_violations"] = [
            str(v) for v in invariants.check_manifest(candidate)
        ]
        out["masked_flagged"] = [
            f"{advisory_id}/{m.model} {m.status.value} at {m.affected_pct}%"
            for advisory_id, m in invariants.masked_pairs(candidate.advisories)
        ]
    return out
