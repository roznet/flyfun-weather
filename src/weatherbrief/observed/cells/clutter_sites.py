"""Where flagged echoes have been before: the clutter site memory (#768).

The clutter *level* (``clutter.py``) is per-frame evidence, and on its own it
cannot tell ground clutter from a newborn isolated storm: both are a bare core
in empty air on their first frame.  Over the live shadow run of 2026-10-08 →
10-11, 42 flagged lineages later carried lightning, 35 of them flagged on
their first frames.  What separates them is time: clutter comes back to the
same ground day after day, a storm does not.

So the node remembers, per UTC day, which sites carried a flagged cell, and a
flagged cell is ``suppress``ed only when its site was flagged on at least
``ClutterPolicy.suppress_min_site_days`` distinct **earlier** days.  The
frame's own day never counts — a site's first day can never hide itself — and
neither does anything later, so the decision is causal and a replay over the
same catalogues reproduces it.

Storage, one line per processed frame::

    cells/clutter_sites/<YYYYMMDD>.jsonl   {"valid_time": …, "sites": ["535,53", …]}

The lightning amend (#666) writes a fresh line for its frame; the **last line
for a stamp wins**, so a late flash that clears a flag also clears the site.
A day file is derived data: when it is missing or holds fewer frames than the
day's catalogues (a node upgraded mid-day, a restored archive, the first run
after #768), it is rebuilt from the catalogues themselves — which is also how
the memory is seeded from the ``cells-3`` history, so the rule acts from the
first frame after deploy rather than two days later.  Any catalogue whose
cells carry a ``clutter`` block counts, whatever ``policy_version`` wrote it:
the memory reads the level, and #768 did not change how the level is made.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from .catalogue import cells_dir, read_catalogue
from .levels import suspect
from .policy import ClutterPolicy

SITES_DIR = "clutter_sites"


def site_key(lat: float, lon: float, precision_deg: float) -> str:
    """The site a centroid falls in: grid indices at ``precision_deg``."""
    return f"{round(lat / precision_deg)},{round(lon / precision_deg)}"


def cell_site(cell: dict, policy: ClutterPolicy) -> str | None:
    lat, lon = cell.get("lat"), cell.get("lon")
    if lat is None or lon is None:
        return None
    return site_key(float(lat), float(lon), policy.site_precision_deg)


def flagged_sites(cells: list[dict], policy: ClutterPolicy) -> list[str]:
    """Sorted sites of the flagged cells among ``cells`` (any assessed tier)."""
    return sorted({k for c in cells if suspect(c) and (k := cell_site(c, policy)) is not None})


def day_path(root: Path, day: date) -> Path:
    return cells_dir(root) / SITES_DIR / f"{day:%Y%m%d}.jsonl"


def record(root: Path, valid_time: datetime, cells: list[dict], policy: ClutterPolicy) -> None:
    """Append this frame's flagged sites to its day file (last line per stamp wins)."""
    path = day_path(root, valid_time.date())
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"valid_time": valid_time.isoformat(), "sites": flagged_sites(cells, policy)}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, sort_keys=True) + "\n")


def read_day(path: Path) -> dict[str, list[str]]:
    """``{valid_time: sites}`` from one day file; a torn last line is ignored."""
    out: dict[str, list[str]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return out
    for text in lines:
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            continue  # a crash mid-append; the stamp's catalogue rebuilds it
        if isinstance(row, dict) and isinstance(row.get("sites"), list):
            out[str(row.get("valid_time"))] = [str(s) for s in row["sites"]]
    return out


def _day_catalogues(root: Path, day: date) -> list[Path]:
    directory = cells_dir(root) / "catalogues" / f"{day:%Y%m%d}"
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.json.gz") if not p.name.startswith(".tmp-"))


def rebuild_day(root: Path, day: date, policy: ClutterPolicy) -> dict[str, list[str]]:
    """Re-derive one day file from that day's catalogues and write it atomically."""
    rows: dict[str, list[str]] = {}
    for path in _day_catalogues(root, day):
        cat = read_catalogue(path)
        if cat is None or not cat.get("valid_time"):
            continue
        rows[str(cat["valid_time"])] = flagged_sites(cat.get("cells") or [], policy)
    target = day_path(root, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".tmp-{target.name}")
    tmp.write_text("".join(json.dumps({"valid_time": t, "sites": rows[t]}, sort_keys=True) + "\n"
                           for t in sorted(rows)), encoding="utf-8")
    os.replace(tmp, target)
    return rows


class SiteMemory:
    """The earlier days' flagged sites, cached once a day can no longer change.

    A day can still change while its last frames' lightning may be amended
    (``settle_after`` past its end, judged on the frame's valid time so a
    replay settles at the same point); until then it is re-read each frame —
    a few hundred short lines, a handful of frames a day.
    """

    def __init__(self, settle_after: timedelta = timedelta(minutes=30)) -> None:
        self.settle_after = settle_after
        self._settled: dict[tuple[str, date], frozenset[str]] = {}

    def day_sites(self, root: Path, day: date, valid_time: datetime,
                  policy: ClutterPolicy) -> frozenset[str]:
        key = (str(Path(root).resolve()), day)
        if key in self._settled:
            return self._settled[key]
        rows = read_day(day_path(root, day))
        catalogues = _day_catalogues(root, day)
        if len(rows) < len(catalogues):
            rows = rebuild_day(root, day, policy)
        sites = frozenset(s for v in rows.values() for s in v)
        day_end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=valid_time.tzinfo)
        if valid_time >= day_end + self.settle_after:
            self._settled[key] = sites
        return sites

    def site_days(self, root: Path, valid_time: datetime, sites: list[str],
                  policy: ClutterPolicy) -> dict[str, int]:
        """Per site, the distinct earlier days in the window it was flagged on."""
        counts = {s: 0 for s in sites}
        if not counts:
            return counts
        today = valid_time.date()
        for back in range(1, policy.site_window_days + 1):
            seen = self.day_sites(root, today - timedelta(days=back), valid_time, policy)
            for s in counts:
                if s in seen:
                    counts[s] += 1
        return counts


def annotate(cells: list[dict], site_days: dict[str, int], policy: ClutterPolicy) -> None:
    """Write ``site_days`` and ``suppress`` onto each flagged cell's clutter block.

    Only flagged cells carry them: ``suppress`` absent means "do not hide",
    which is also what every pre-#768 display file says.
    """
    for cell in cells:
        if not suspect(cell):
            continue
        site = cell_site(cell, policy)
        days = site_days.get(site, 0) if site is not None else 0
        block = cell["clutter"]
        block["site_days"] = days
        block["suppress"] = days >= policy.suppress_min_site_days
