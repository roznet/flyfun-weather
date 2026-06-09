"""EDR calibration persistence + readout (issue #221).

DB-facing companion to :mod:`weatherbrief.analysis.sounding.edr`:

- :func:`flush_accumulator` — additively upsert one standalone run's
  accumulated ln-moments into ``edr_calibration_accumulator``.
- :func:`load_coefficients` — load the frozen remap coefficients ``(a, b)``
  for a ``(model, band)`` at runtime.
- a small CLI (``python -m weatherbrief.tasks.edr_calibration``) that dumps the
  current moments, derived ``a, b``, and sample EDR at p50/p90/p99 of ``D`` so
  a maintainer can eyeball whether the remap lands in sane severity bands.

**v0 coefficient source.** Coefficients are derived *live from the table*
(stored moments + the published C1/C2 climatology) via :func:`load_coefficients`.
Hard-freezing them as committed constants is deliberately deferred: the
accumulators need ~1–2 weeks of runs to converge, so frozen constants would be
meaningless today. Once converged, a maintainer reads them off the CLI and may
populate :data:`weatherbrief.analysis.sounding.edr` — but no display surface
consumes EDR in v0, so nothing depends on the choice yet.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from weatherbrief.analysis.sounding.edr import (
    C1_C2_BY_BAND,
    DIAGNOSTIC_RICHARDSON,
    VAR_EPS,
    EdrAccumulator,
    coefficients_from_accumulator,
    diagnostic_to_edr,
)
from weatherbrief.db.models import EdrCalibrationAccumulatorRow

logger = logging.getLogger(__name__)


def flush_accumulator(db: Session, acc: EdrAccumulator) -> int:
    """Additively upsert an accumulator's moments into the calibration table.

    One UPDATE-add (or INSERT) per ``(model, diagnostic, band)`` key, committed
    once. Additive so repeated cycles compose; the standalone cycle is a single
    server-side process so there is no cross-process write race on a key.

    Fails silent: calibration must never break a forecast run. Returns the
    number of keys written (0 on empty accumulator or on error).
    """
    rows = acc.rows()
    if not rows:
        return 0
    now = datetime.now(timezone.utc)
    written = 0
    try:
        for model, diagnostic, band, n, sum_ln, sum_ln2 in rows:
            if n <= 0:
                continue
            existing = db.execute(
                select(EdrCalibrationAccumulatorRow)
                .where(EdrCalibrationAccumulatorRow.model == model)
                .where(EdrCalibrationAccumulatorRow.diagnostic == diagnostic)
                .where(EdrCalibrationAccumulatorRow.band == band)
            ).scalar_one_or_none()
            if existing is not None:
                existing.n += n
                existing.sum_ln += sum_ln
                existing.sum_ln2 += sum_ln2
                existing.updated_at = now
            else:
                db.add(EdrCalibrationAccumulatorRow(
                    model=model,
                    diagnostic=diagnostic,
                    band=band,
                    n=n,
                    sum_ln=sum_ln,
                    sum_ln2=sum_ln2,
                    updated_at=now,
                ))
            written += 1
        db.commit()
    except Exception:
        logger.warning("EDR calibration flush failed, rolling back", exc_info=True)
        db.rollback()
        return 0
    logger.info("EDR calibration: flushed %d (model, diagnostic, band) keys", written)
    return written


def load_coefficients(
    db: Session, model: str, band: str, diagnostic: str = DIAGNOSTIC_RICHARDSON,
) -> tuple[float, float] | None:
    """Load the frozen remap coefficients ``(a, b)`` for one (model, band).

    Reads the accumulated moments and derives ``a, b`` from them plus the
    published C1/C2 climatology for the band. Returns ``None`` if the band is
    unknown, the accumulator row is missing, or there are too few samples.
    """
    c1_c2 = C1_C2_BY_BAND.get(band)
    if c1_c2 is None:
        return None
    row = db.execute(
        select(EdrCalibrationAccumulatorRow)
        .where(EdrCalibrationAccumulatorRow.model == model)
        .where(EdrCalibrationAccumulatorRow.diagnostic == diagnostic)
        .where(EdrCalibrationAccumulatorRow.band == band)
    ).scalar_one_or_none()
    if row is None:
        return None
    return coefficients_from_accumulator(row.n, row.sum_ln, row.sum_ln2, *c1_c2)


# Standard-normal quantiles for the percentile readout.
_Z = {"p50": 0.0, "p90": 1.2815515594, "p99": 2.3263478740}


def _readout_rows(db: Session) -> list[dict]:
    """Build the CLI readout: moments, derived a/b, and sample EDR percentiles.

    Because ln(D) is modelled as Gaussian (that is the remap's own assumption),
    the p50/p90/p99 of D follow directly from the moments — no sample archive
    needed: ``ln(D_p) = ⟨ln D⟩ + z_p·SD[ln D]``.
    """
    rows = db.execute(
        select(EdrCalibrationAccumulatorRow).order_by(
            EdrCalibrationAccumulatorRow.model,
            EdrCalibrationAccumulatorRow.diagnostic,
            EdrCalibrationAccumulatorRow.band,
        )
    ).scalars().all()

    out: list[dict] = []
    for r in rows:
        c1_c2 = C1_C2_BY_BAND.get(r.band)
        mean = r.sum_ln / r.n if r.n else float("nan")
        sd = float("nan")
        if r.n >= 2:
            var = r.sum_ln2 / r.n - mean * mean
            # Floor at VAR_EPS to match coefficients_from_accumulator, so the
            # percentile readout below uses the same SD the a/b math does (else
            # near-zero variance collapses p50/p90/p99 to equal values).
            sd = math.sqrt(max(var, VAR_EPS))
        coeffs = (
            coefficients_from_accumulator(r.n, r.sum_ln, r.sum_ln2, *c1_c2)
            if c1_c2 else None
        )
        entry = {
            "model": r.model,
            "diagnostic": r.diagnostic,
            "band": r.band,
            "n": r.n,
            "mean_ln_d": mean,
            "sd_ln_d": sd,
            "a": coeffs[0] if coeffs else None,
            "b": coeffs[1] if coeffs else None,
            "edr": {},
        }
        if coeffs and math.isfinite(sd):
            a, b = coeffs
            for label, z in _Z.items():
                d_p = math.exp(mean + z * sd)
                entry["edr"][label] = diagnostic_to_edr(d_p, a, b)
        out.append(entry)
    return out


def _format_readout(rows: list[dict]) -> str:
    if not rows:
        return "No EDR calibration data accumulated yet."
    header = (
        f"{'model':<8}{'diag':<12}{'band':<10}{'n':>12}"
        f"{'<lnD>':>10}{'SD lnD':>10}{'a':>10}{'b':>10}"
        f"{'EDR p50':>10}{'EDR p90':>10}{'EDR p99':>10}"
    )
    lines = [header, "-" * len(header)]

    def fmt(v, prec=3):
        return f"{v:.{prec}f}" if isinstance(v, (int, float)) and v == v else "—"

    for r in rows:
        edr = r["edr"]
        lines.append(
            f"{r['model']:<8}{r['diagnostic']:<12}{r['band']:<10}{r['n']:>12}"
            f"{fmt(r['mean_ln_d']):>10}{fmt(r['sd_ln_d']):>10}"
            f"{fmt(r['a']):>10}{fmt(r['b']):>10}"
            f"{fmt(edr.get('p50')):>10}{fmt(edr.get('p90')):>10}{fmt(edr.get('p99')):>10}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m weatherbrief.tasks.edr_calibration",
        description="Dump EDR calibration moments + derived remap coefficients.",
    )
    parser.add_argument(
        "command", nargs="?", default="show", choices=["show"],
        help="show: print current moments, a/b, and sample EDR percentiles.",
    )
    args = parser.parse_args(argv)

    from flyfun_common.db import SessionLocal, get_engine

    get_engine()  # configures SessionLocal's bind for standalone CLI use
    db = SessionLocal()
    try:
        rows = _readout_rows(db)
    finally:
        db.close()

    if args.command == "show":
        print(_format_readout(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
