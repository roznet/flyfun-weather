"""Score every visibility and fog method against METARs (#979 phase 1).

Three parts, all on ``verification_scores.visibility_methods`` (migration
117):

- :func:`score_payload` — what the standalone scoring cycle stores on each
  score: every method of ``analysis/visibility/methods.py`` run on the scored
  snapshot's inputs, plus the night flag. Pure.
- :func:`backfill_visibility_methods` — fills the column on standalone scores
  whose snapshot is still in the database (snapshots prune at 10 days), so
  the methods cover the days between the inputs being logged (migration 116)
  and this scoring being deployed. ``verify visibility-backfill``.
- :func:`visibility_report` / :func:`promotion_verdict` — per model, method,
  lead day and day/night: log-visibility error, POD/FAR/CSI/bias per
  visibility band (< 1.5 km, < 5 km, < 8 km: the flight-category bands, §48),
  fog (< 1 km or FG reported) per rule, and the observed fog rate per fog
  tier; then the promotion bar of meteorology-decisions §80.
  ``verify visibility-report``.

Observed side: the METAR's prevailing visibility, 9999 read as 10 km, and
both sides capped at 10 km for the error (a METAR cannot tell 12 km from 40).
Observed fog = visibility < 1000 m or ``FG`` / ``FZFG`` in the present
weather (shallow, patches, partial and vicinity fog do not count).
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from weatherbrief.analysis.visibility.methods import (
    FOG_RULES,
    FOG_TIERS,
    METHODS_VERSION,
    VISIBILITY_METHODS,
    VisibilityInputs,
    is_night,
    run_all,
)

logger = logging.getLogger(__name__)

#: Visibility bands scored as yes/no events: the flight-category edges (§48).
BANDS_M = (1500.0, 5000.0, 8000.0)
FOG_VIS_M = 1000.0
#: Both sides are capped here for the log error: METARs stop at 9999 m.
SCORE_CAP_M = 10000.0
_FOG_WEATHER = ("FG", "FZFG")

# Promotion bar (meteorology-decisions §80), candidate vs reference on the
# same rows, leads 0-2.
PROMOTION_MAX_DAYS_OUT = 2
PROMOTION_MIN_EVENTS = {5000.0: 40, 1500.0: 15}
PROMOTION_CSI_TOLERANCE = {5000.0: 0.03, 1500.0: 0.05}
PROMOTION_FAR_TOLERANCE_1500 = 0.10
PROMOTION_BIAS_RANGE = (0.5, 2.0)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def score_payload(snap: Any, lat: float | None, lon: float | None) -> str:
    """JSON for ``visibility_methods`` from one snapshot row.

    ``lat``/``lon`` give the night flag (radiation-fog rule); unknown
    coordinates leave it None, which skips that rule only.
    """
    night = (
        is_night(lat, lon, snap.forecast_hour)
        if lat is not None and lon is not None and getattr(snap, "forecast_hour", None)
        else None
    )
    methods = run_all(VisibilityInputs.from_snapshot(snap, is_night=night))
    return json.dumps(
        {"v": METHODS_VERSION, "night": night, "m": methods},
        separators=(",", ":"),
    )


def backfill_visibility_methods(
    db,
    since: datetime,
    coords: dict[str, tuple[float, float]],
    *,
    batch: int = 2000,
    dry_run: bool = False,
) -> dict[str, int]:
    """Fill ``visibility_methods`` on standalone scores since *since*.

    Each score names its snapshot by ``(icao, model, model_init_time)`` and
    the observation time; the snapshot is the one of that key whose forecast
    hour is closest to the observation, within the scoring cycle's ±90 min.
    Scores whose snapshot has been pruned stay NULL (counted as
    ``no_snapshot``). Idempotent: only NULL rows are touched.
    """
    from sqlalchemy import select, tuple_

    from weatherbrief.db.models import AirportForecastSnapshotRow, VerificationScoreRow

    stats = {"scores": 0, "filled": 0, "no_snapshot": 0}
    last_id = 0
    while True:
        scores = db.execute(
            select(VerificationScoreRow)
            .where(
                VerificationScoreRow.source == "standalone",
                VerificationScoreRow.observation_time >= since,
                VerificationScoreRow.visibility_methods.is_(None),
                VerificationScoreRow.id > last_id,
            )
            .order_by(VerificationScoreRow.id)
            .limit(batch)
        ).scalars().all()
        if not scores:
            break
        last_id = scores[-1].id
        stats["scores"] += len(scores)
        keys = list({(s.icao, s.model, s.model_init_time) for s in scores})
        snaps: dict[tuple, list] = {}
        for i in range(0, len(keys), 300):
            chunk = keys[i:i + 300]
            for snap in db.execute(
                select(AirportForecastSnapshotRow).where(
                    tuple_(
                        AirportForecastSnapshotRow.icao,
                        AirportForecastSnapshotRow.model,
                        AirportForecastSnapshotRow.model_init_time,
                    ).in_(chunk)
                )
            ).scalars():
                snaps.setdefault((snap.icao, snap.model, snap.model_init_time), []).append(snap)
        for score in scores:
            snap = _closest_snapshot(
                snaps.get((score.icao, score.model, score.model_init_time), []),
                score.observation_time,
            )
            if snap is None:
                stats["no_snapshot"] += 1
                continue
            lat, lon = coords.get(score.icao, (None, None))
            if not dry_run:
                score.visibility_methods = score_payload(snap, lat, lon)
            stats["filled"] += 1
        if not dry_run:
            db.commit()
    return stats


def _closest_snapshot(candidates: list, obs_time: datetime):
    window = timedelta(minutes=90)
    best, best_delta = None, None
    for snap in candidates:
        delta = abs(snap.forecast_hour - obs_time)
        if delta <= window and (best_delta is None or delta < best_delta):
            best, best_delta = snap, delta
    return best


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def observed_visibility_m(vis_m: float | None) -> float | None:
    if vis_m is None:
        return None
    return SCORE_CAP_M if vis_m >= 9999 else float(vis_m)


def observed_fog(vis_m: float | None, weather: Iterable[str] | None) -> bool | None:
    """< 1 km prevailing or FG / FZFG reported; None if neither is known."""
    codes = [w.lstrip("+-") for w in (weather or [])]
    if any(c in _FOG_WEATHER for c in codes):
        return True
    if vis_m is None:
        return None
    return vis_m < FOG_VIS_M


@dataclass
class _Contingency:
    hits: int = 0
    misses: int = 0
    false_alarms: int = 0
    correct_negatives: int = 0

    def add(self, forecast: bool, observed: bool) -> None:
        if forecast and observed:
            self.hits += 1
        elif observed:
            self.misses += 1
        elif forecast:
            self.false_alarms += 1
        else:
            self.correct_negatives += 1

    @property
    def events(self) -> int:
        return self.hits + self.misses

    @property
    def pod(self) -> float | None:
        return _ratio(self.hits, self.hits + self.misses)

    @property
    def far(self) -> float | None:
        return _ratio(self.false_alarms, self.hits + self.false_alarms)

    @property
    def csi(self) -> float | None:
        return _ratio(self.hits, self.hits + self.misses + self.false_alarms)

    @property
    def bias(self) -> float | None:
        return _ratio(self.hits + self.false_alarms, self.hits + self.misses)


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


@dataclass
class MethodStats:
    """One (model, method, lead bucket, day/night) cell."""

    n: int = 0
    sum_abs_log_err: float = 0.0
    sum_log_err: float = 0.0
    bands: dict[float, _Contingency] = field(
        default_factory=lambda: {b: _Contingency() for b in BANDS_M}
    )
    fog: _Contingency = field(default_factory=_Contingency)
    # fog_tier only: tier -> [n, observed fog]
    tiers: dict[str, list[int]] = field(default_factory=dict)

    @property
    def mae_log10(self) -> float | None:
        return self.sum_abs_log_err / self.n if self.n else None

    @property
    def bias_log10(self) -> float | None:
        return self.sum_log_err / self.n if self.n else None


@dataclass(frozen=True)
class ScoredPair:
    """One score row with its observation, as the report reads it."""

    model: str
    days_out: int
    night: bool | None
    methods: dict[str, Any]
    obs_vis_m: float | None
    obs_fog: bool | None
    # The METAR this score pairs with. Several model runs score the same
    # METAR, so the promotion gate counts distinct observations, not rows.
    obs_key: Any = None


def pairs_from_rows(
    rows: Iterable[tuple], version: str | None = METHODS_VERSION,
) -> list[ScoredPair]:
    """``(model, days_out, visibility_methods, obs visibility_m, obs weather
    [, observation id])`` tuples → pairs. Only rows written by methods *version* (None: any), so a
    re-tuned method is never scored together with its old self."""
    out: list[ScoredPair] = []
    for model, days_out, payload, vis_m, weather, *rest in rows:
        if not payload:
            continue
        try:
            data = json.loads(payload)
            wx = json.loads(weather) if weather else []
        except (TypeError, ValueError):
            continue
        if version is not None and data.get("v") != version:
            continue
        out.append(ScoredPair(
            model=model, days_out=days_out, night=data.get("night"),
            methods=data.get("m", {}),
            obs_vis_m=observed_visibility_m(float(vis_m) if vis_m is not None else None),
            obs_fog=observed_fog(float(vis_m) if vis_m is not None else None, wx),
            obs_key=rest[0] if rest else None,
        ))
    return out


def _lead_bucket(days_out: int) -> str:
    return f"D{days_out}" if days_out <= 2 else "D3+"


def visibility_report(
    pairs: Iterable[ScoredPair], *, split_night: bool = True,
) -> dict[tuple[str, str, str, str], MethodStats]:
    """Aggregate pairs into ``{(model, method, lead, day/night): MethodStats}``.

    ``lead`` is D0, D1, D2 or D3+, plus an ``all`` bucket; ``day/night`` is
    ``day``, ``night``, ``?`` (unknown) and ``all``.

    ``n`` differs by kind: a visibility method counts pairs with an observed
    visibility, a fog rule or tier counts every pair where it was evaluated.
    """
    cells: dict[tuple[str, str, str, str], MethodStats] = {}

    def keys(pair: ScoredPair, method: str):
        leads = (_lead_bucket(pair.days_out), "all")
        dn = "?" if pair.night is None else ("night" if pair.night else "day")
        times = (dn, "all") if split_night else ("all",)
        for lead in leads:
            for t in times:
                yield (pair.model, method, lead, t)

    for pair in pairs:
        for method in VISIBILITY_METHODS:
            fc = pair.methods.get(method)
            if fc is None or pair.obs_vis_m is None:
                continue
            fc_c = min(SCORE_CAP_M, max(1.0, float(fc)))
            ob_c = min(SCORE_CAP_M, max(1.0, pair.obs_vis_m))
            err = math.log10(fc_c) - math.log10(ob_c)
            for key in keys(pair, method):
                st = cells.setdefault(key, MethodStats())
                st.n += 1
                st.sum_abs_log_err += abs(err)
                st.sum_log_err += err
                for band, table in st.bands.items():
                    table.add(fc_c < band, ob_c < band)
                if pair.obs_fog is not None:
                    st.fog.add(fc_c < FOG_VIS_M, pair.obs_fog)
        if pair.obs_fog is None:
            continue
        for rule in FOG_RULES:
            fired = pair.methods.get(rule)
            if fired is None:
                continue
            for key in keys(pair, rule):
                st = cells.setdefault(key, MethodStats())
                st.n += 1
                st.fog.add(bool(fired), pair.obs_fog)
        tier = pair.methods.get("fog_tier")
        if tier in FOG_TIERS:
            for key in keys(pair, "fog_tier"):
                st = cells.setdefault(key, MethodStats())
                st.n += 1
                st.fog.add(tier == "LIKELY", pair.obs_fog)
                counts = st.tiers.setdefault(tier, [0, 0])
                counts[0] += 1
                counts[1] += int(pair.obs_fog)
    return cells


@dataclass(frozen=True)
class PromotionVerdict:
    model: str
    method: str
    reference: str
    passed: bool
    enough_data: bool
    reasons: tuple[str, ...]
    candidate: dict[float, _Contingency]
    baseline: dict[float, _Contingency]


def promotion_verdict(
    pairs: Iterable[ScoredPair], model: str, method: str, reference: str = "native",
) -> PromotionVerdict:
    """Does *method* clear the promotion bar against *reference* (§80)?

    Only rows of *model*, lead 0-2, where both the method and the reference
    have a value and the METAR a visibility, so the two are scored on exactly
    the same hours. Bar:

    1. at least 40 observed < 5 km and 15 observed < 1.5 km events, else
       "not enough data" (not a failure, not a pass). An event is a distinct
       METAR: the several runs that forecast the same hour count once, so the
       gate is about observed hours, not score rows. The skill figures still
       pool every run;
    2. CSI within 0.03 (< 5 km) and 0.05 (< 1.5 km) of the reference's, or
       better;
    3. FAR < 1.5 km at most 0.10 above the reference's (false IFR);
    4. frequency bias 0.5-2 at both bands.
    """
    cand = {b: _Contingency() for b in (5000.0, 1500.0)}
    base = {b: _Contingency() for b in (5000.0, 1500.0)}
    distinct: dict[float, set] = {b: set() for b in cand}
    for p in pairs:
        if p.model != model or p.days_out > PROMOTION_MAX_DAYS_OUT or p.obs_vis_m is None:
            continue
        fc, ref = p.methods.get(method), p.methods.get(reference)
        if fc is None or ref is None:
            continue
        for band in cand:
            observed = p.obs_vis_m < band
            cand[band].add(float(fc) < band, observed)
            base[band].add(float(ref) < band, observed)
            if observed:
                distinct[band].add(p.obs_key if p.obs_key is not None else id(p))
    reasons: list[str] = []
    enough = all(len(distinct[b]) >= n for b, n in PROMOTION_MIN_EVENTS.items())
    if not enough:
        reasons.append(
            ", ".join(f"{len(distinct[b])}/{n} observed hours < {b / 1000:g} km"
                        for b, n in PROMOTION_MIN_EVENTS.items())
        )
        return PromotionVerdict(model, method, reference, False, False,
                                tuple(reasons), cand, base)
    for band, tol in PROMOTION_CSI_TOLERANCE.items():
        c, r = cand[band].csi or 0.0, base[band].csi or 0.0
        if c < r - tol:
            reasons.append(f"CSI < {band / 1000:g} km {c:.2f} vs {reference} {r:.2f} (tolerance {tol})")
    far_c, far_r = cand[1500.0].far, base[1500.0].far
    if far_c is None:
        reasons.append("no forecast < 1.5 km, so no FAR")
    elif far_c > (far_r or 0.0) + PROMOTION_FAR_TOLERANCE_1500:
        reasons.append(f"FAR < 1.5 km {far_c:.2f} vs {reference} {(far_r or 0.0):.2f}")
    lo, hi = PROMOTION_BIAS_RANGE
    for band in cand:
        b = cand[band].bias
        if b is None or not lo <= b <= hi:
            reasons.append(f"bias < {band / 1000:g} km {b if b is None else round(b, 2)} outside {lo}-{hi}")
    return PromotionVerdict(model, method, reference, not reasons, True,
                            tuple(reasons), cand, base)


def load_pairs(db, since: datetime, until: datetime | None = None,
               model: str | None = None,
               version: str | None = METHODS_VERSION) -> list[ScoredPair]:
    """Standalone scores with ``visibility_methods`` joined to their METAR."""
    from sqlalchemy import select

    from weatherbrief.db.models import VerificationObservationRow, VerificationScoreRow

    q = (
        select(
            VerificationScoreRow.model,
            VerificationScoreRow.days_out,
            VerificationScoreRow.visibility_methods,
            VerificationObservationRow.visibility_m,
            VerificationObservationRow.weather,
            VerificationScoreRow.observation_id,
        )
        .join(VerificationObservationRow,
              VerificationObservationRow.id == VerificationScoreRow.observation_id)
        .where(
            VerificationScoreRow.source == "standalone",
            VerificationScoreRow.observation_time >= since,
            VerificationScoreRow.visibility_methods.is_not(None),
        )
    )
    if until is not None:
        q = q.where(VerificationScoreRow.observation_time < until)
    if model is not None:
        q = q.where(VerificationScoreRow.model == model)
    return pairs_from_rows(db.execute(q).all(), version=version)
