"""Cloud detail on verification observations (#793): layers, lowest base, CB/TCU.

``verification_observations`` kept a single ``ceiling_ft`` (lowest BKN/OVC).
For the VFR question the base that matters is often a FEW/SCT one, and the
convective calibration (#791) needs to know whether a CB or TCU was reported.
Both were only in ``metar_raw``. :func:`metar_cloud_fields` derives three
columns from it:

- ``cloud_layers`` — JSON list of ``{"amount", "base_ft", "type"}`` in report
  order, from the observed part only (a ``TEMPO BKN010TCU`` is a forecast). A
  vertical visibility is kept as amount ``"VV"``. ``[]`` means the report gave
  no cloud (CAVOK, NSC, NCD, SKC); ``None`` means it could not be parsed.
- ``lowest_cloud_base_ft`` — lowest FEW/SCT/BKN/OVC base. VV is excluded: it is
  a vertical visibility into an obscuration, not a cloud base, and
  ``ceiling_ft`` (euro_aip) excludes it the same way.
- ``convective_cloud`` — ``"CB"`` if any CB group, else ``"TCU"``, else None.
  Read by the same rule as the live layer (``observed_cloud_types``), so the
  calibration and the flight-day alerts agree on what "a CB was reported" means.

Ingest and backfill both go through :func:`metar_cloud_fields` on the raw
text, so a backfilled row is identical to one written at ingest.

Backfill
--------
Rows written before migration 107 have ``cloud_layers`` NULL.
:func:`backfill_cloud_layers` fills them from ``metar_raw``, keyset-paginated by
primary key with a commit per batch (resumable, off-peak; same reasoning as
``verification_report_type``). Months already pruned from MySQL exist only in the
Parquet archive, which is never rewritten: :func:`derive_archived_month` writes a
side file ``DATA_DIR/archive/derived/observation_clouds/YYYY-MM.parquet`` keyed by
observation ``id``, which joins to ``archive/verification/observations`` in DuckDB.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import bindparam, func, select, update
from sqlalchemy.orm import Session

from weatherbrief.db.models import VerificationObservationRow

logger = logging.getLogger(__name__)

_DEFAULT_BATCH = 5_000

#: Amounts that are a cloud base (VV is not; see the module docstring).
_CLOUD_AMOUNTS = ("FEW", "SCT", "BKN", "OVC")

_VV_RE = re.compile(r"(?<!\S)VV(\d{3}|///)(?!\S)")


def metar_cloud_fields(
    raw: str | None, reference: datetime | None = None,
) -> dict[str, object] | None:
    """The three cloud columns for one raw METAR, or None if it cannot be parsed.

    ``reference`` is the observation time. Only the parser uses it, to resolve
    the report's day-of-month without warning on old reports; the date itself is
    not part of the result.
    """
    from euro_aip.briefing.weather.parser import WeatherParser

    from weatherbrief.tasks.live_significance import (
        metar_observed_part,
        observed_cloud_types,
    )

    if not raw or not raw.strip():
        return None
    # Everything below reads the observed part only. The parser drops trend and
    # remark groups itself today; cutting first makes that true by construction.
    observed = metar_observed_part(raw)
    try:
        report = WeatherParser.parse_metar(observed, reference=reference)
    except Exception:
        logger.debug("METAR parse failed: %s", raw[:80], exc_info=True)
        return None
    if report is None:
        return None

    layers: list[dict[str, object]] = []
    for c in report.clouds or []:
        amount = c.get("quantity")
        if amount not in _CLOUD_AMOUNTS:
            continue
        layers.append({"amount": amount, "base_ft": c.get("height"), "type": c.get("type")})
    vv = _VV_RE.search(observed)
    if vv is not None:
        height = vv.group(1)
        layers.append({
            "amount": "VV",
            "base_ft": int(height) * 100 if height.isdigit() else None,
            "type": None,
        })

    bases = [
        int(layer["base_ft"]) for layer in layers
        if layer["amount"] in _CLOUD_AMOUNTS and layer["base_ft"] is not None
    ]
    types = observed_cloud_types(observed)
    convective = "CB" if "CB" in types else ("TCU" if "TCU" in types else None)
    return {
        "cloud_layers": json.dumps(layers, separators=(",", ":")),
        "lowest_cloud_base_ft": min(bases) if bases else None,
        "convective_cloud": convective,
    }


# ---------------------------------------------------------------------------
# Database backfill
# ---------------------------------------------------------------------------


@dataclass
class CloudBackfillResult:
    scanned: int = 0
    updated: int = 0
    unparseable: int = 0
    with_cb: int = 0
    with_tcu: int = 0

    def render(self) -> str:
        return "\n".join([
            f"  rows needing cloud detail {self.scanned:>12,}",
            f"  -> derived                {self.updated:>12,}",
            f"     with CB                {self.with_cb:>12,}",
            f"     with TCU (no CB)       {self.with_tcu:>12,}",
            f"  unparseable (left NULL)   {self.unparseable:>12,}",
        ])


def backfill_cloud_layers(
    db: Session, batch_size: int = _DEFAULT_BATCH, dry_run: bool = False,
) -> CloudBackfillResult:
    """Derive the cloud columns for rows whose ``cloud_layers`` is NULL.

    A dry run parses and counts without writing. Unparseable reports stay NULL
    and are re-tried by a later run (they are rare, and a parser fix may cure
    them).
    """
    out = CloudBackfillResult()
    bounds = db.execute(
        select(func.min(VerificationObservationRow.id), func.max(VerificationObservationRow.id))
    ).one()
    if bounds[0] is None:
        return out
    lo, hi = int(bounds[0]), int(bounds[1])
    stmt = (
        update(VerificationObservationRow)
        .where(VerificationObservationRow.id == bindparam("_id"))
        .values(
            cloud_layers=bindparam("_layers"),
            lowest_cloud_base_ft=bindparam("_base"),
            convective_cloud=bindparam("_conv"),
        )
    )
    while lo <= hi:
        top = lo + batch_size
        rows = db.execute(
            select(
                VerificationObservationRow.id,
                VerificationObservationRow.metar_raw,
                VerificationObservationRow.observation_time,
            )
            .where(VerificationObservationRow.id >= lo)
            .where(VerificationObservationRow.id < top)
            .where(VerificationObservationRow.cloud_layers.is_(None))
            .where(VerificationObservationRow.metar_raw.isnot(None))
        ).all()
        params = []
        for row_id, raw, obs_time in rows:
            out.scanned += 1
            fields = metar_cloud_fields(raw, obs_time)
            if fields is None:
                out.unparseable += 1
                continue
            out.updated += 1
            if fields["convective_cloud"] == "CB":
                out.with_cb += 1
            elif fields["convective_cloud"] == "TCU":
                out.with_tcu += 1
            params.append({
                "_id": row_id,
                "_layers": fields["cloud_layers"],
                "_base": fields["lowest_cloud_base_ft"],
                "_conv": fields["convective_cloud"],
            })
        if params and not dry_run:
            db.connection().execute(stmt, params)
            db.commit()
        logger.info(
            "cloud-layer backfill: ids [%d, %d) done, %d derived so far%s",
            lo, top, out.updated, " (dry run)" if dry_run else "",
        )
        lo = top
    return out


# ---------------------------------------------------------------------------
# Archived months (already pruned from MySQL)
# ---------------------------------------------------------------------------


def derived_clouds_root() -> Path:
    from weatherbrief.tasks.verification_tiering import data_dir

    return data_dir() / "archive" / "derived" / "observation_clouds"


def derive_archived_month(period: str, *, out_root: Path | None = None) -> int | None:
    """Write the side file for one archived observations month; returns its row
    count, or None when that month was never archived.

    Overwrites: the file is a pure function of the archive and this module.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    from weatherbrief.tasks.archive import archive_file_path

    src = archive_file_path("observations", period)
    if not src.exists():
        return None
    table = pq.read_table(src, columns=["id", "metar_raw", "observation_time"])
    ids, layers, bases, convective = [], [], [], []
    for row_id, raw, obs_time in zip(
        table.column("id").to_pylist(),
        table.column("metar_raw").to_pylist(),
        table.column("observation_time").to_pylist(),
    ):
        fields = metar_cloud_fields(raw, obs_time)
        if fields is None:
            continue
        ids.append(row_id)
        layers.append(fields["cloud_layers"])
        bases.append(fields["lowest_cloud_base_ft"])
        convective.append(fields["convective_cloud"])
    out = pa.table({
        "id": pa.array(ids, pa.int64()),
        "cloud_layers": pa.array(layers, pa.string()),
        "lowest_cloud_base_ft": pa.array(bases, pa.int32()),
        "convective_cloud": pa.array(convective, pa.string()),
    })
    root = out_root or derived_clouds_root()
    root.mkdir(parents=True, exist_ok=True)
    dest = root / f"{period}.parquet"
    tmp = dest.with_suffix(".parquet.tmp")
    pq.write_table(out, tmp, compression="zstd")
    tmp.replace(dest)
    return len(ids)
