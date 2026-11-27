#!/usr/bin/env python3
"""Transport for front-review: pull analysis charts, trimmed Hewson grids and METARs
from prod into the local case library.

    pull CASES [--days 2]      every DWD / Met Office analysis on prod from the last
                               DAYS days that has a matching Hewson run, plus the
                               trimmed grids, the terrain cache and the METARs the
                               case needs. Idempotent: files already in CASES are kept.
    list CASES                 analysis times in the library and what each one has

Prod keeps ~2 days (DWD 8 cycles, Met Office 6, Hewson 4 inits per model), so a
daily run never loses a case; skipping two days does.

Matching: an analysis at T pairs with the Hewson run initialised at T floored to
00/12Z, at hour T - init (0 or 6). 12Z grids exist from ~20Z, DWD 18Z from ~20:30Z.

Library layout (CASES = LOCAL_DATA_DIR/reviews/front_cases):
    terrain_mask.npz
    grids/<model>/<init>.npz            trimmed: hours 0-9 + background_<level>
    charts/dwd/<T>/ana.png, ana_w1600.png
    charts/mo/<T>/ana.gif
    metars/<YYYY-MM-DD>.jsonl           routine METARs, [day - 6 h, day + 30 h)
    metars/manifest.json                window end each file was fetched for

Reuses the ssh / container helpers of prod-briefings-review/scripts/fetch.py.
Status lines `ok|problem|unknown  what  (evidence)` on stderr; exit 0/1/2 as there.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shlex
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "pbr_fetch", HERE.parent.parent / "prod-briefings-review" / "scripts" / "fetch.py")
pbr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pbr)  # type: ignore[union-attr]
hosts = pbr.hosts
run, text, status, Failed, SSH, SCP = pbr.run, pbr.text, pbr.status, pbr.Failed, pbr.SSH, pbr.SCP

MODELS = ("ecmwf", "gfs", "icon")
SOURCES = {"dwd": ("dwd_charts", ["ana.png", "ana_w1600.png"]),
           "mo": ("metoffice_charts", ["ana.gif"])}


def _server():
    v = hosts.server_values("SERVER_SSH", "HOST_DATA_DIR", "SERVER_CONTAINER", "CONTAINER_DATA_DIR")
    return v["SERVER_SSH"], v["HOST_DATA_DIR"], v["SERVER_CONTAINER"], v["CONTAINER_DATA_DIR"]


def parse_cycle(name: str) -> datetime:
    return datetime.strptime(name, "%Y-%m-%dT%HZ").replace(tzinfo=timezone.utc)


def cycle_name(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%HZ")


def hewson_init(t: datetime) -> datetime:
    return t.replace(hour=0 if t.hour < 12 else 12, minute=0, second=0, microsecond=0)


def init_file(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ") + ".npz"


def remote_inventory(ssh: str, data: str) -> dict:
    """One ssh: which analyses and Hewson snapshots prod has right now."""
    q = shlex.quote(data)
    r = run([*SSH, ssh, f"cd {q} && ls -1 dwd_charts/*/ana.png metoffice_charts/*/ana.gif "
             f"hewson/*/*.npz 2>/dev/null || true"], what="list prod files")
    inv = {"dwd": set(), "mo": set(), "hewson": {m: set() for m in MODELS}}
    for line in text(r).split():
        parts = line.split("/")
        if parts[0] == "dwd_charts":
            inv["dwd"].add(parts[1])
        elif parts[0] == "metoffice_charts":
            inv["mo"].add(parts[1])
        elif parts[0] == "hewson" and parts[1] in MODELS:
            inv["hewson"][parts[1]].add(parts[2])
    return inv


def _scp_get(ssh: str, remote: str, local: Path) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    part = local.with_name(local.name + ".part")
    run([*SCP, f"{ssh}:{remote}", str(part)], what=f"scp {remote}")
    part.replace(local)


def _trim(ssh: str, ctr: str, cdata: str, model: str, init_name: str, local: Path) -> None:
    """Trim a snapshot inside the container and stream the small file back."""
    out = f"/tmp/{pbr.tag()}-{model}.npz"

    def body(script):
        src = f"{cdata}/hewson/{model}/{init_name}"
        run([*SSH, ssh, " ".join(shlex.quote(x) for x in
                                 ["docker", "exec", ctr, "python", script, src, out])],
            what=f"trim {model} {init_name}", timeout=600)
        local.parent.mkdir(parents=True, exist_ok=True)
        part = local.with_name(local.name + ".part")
        pbr.get(ssh, ctr, out, part)
        part.replace(local)

    try:
        pbr.with_container_file(ssh, ctr, HERE / "trim_snapshot.py", body)
    finally:
        pbr.cleanup(ssh, ctr, [out])


def _metars(ssh: str, ctr: str, day: str, cases: Path, now: datetime) -> None:
    """METARs for [day - 6 h, day + 30 h): an analysis at T needs T ± 6 h."""
    mdir = cases / "metars"
    mdir.mkdir(parents=True, exist_ok=True)
    manifest_p = mdir / "manifest.json"
    manifest = json.loads(manifest_p.read_text()) if manifest_p.exists() else {}
    start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) - timedelta(hours=6)
    end = start + timedelta(hours=36)
    have = manifest.get(day)
    want_end = min(end, now)
    if have and datetime.fromisoformat(have) >= want_end - timedelta(minutes=30):
        return
    local = mdir / f"{day}.jsonl"

    def body(script):
        cmd = " ".join(shlex.quote(x) for x in [
            "docker", "exec", ctr, "python", script,
            start.strftime("%Y-%m-%d %H:%M"), want_end.strftime("%Y-%m-%d %H:%M")])
        part = local.with_name(local.name + ".part")
        with part.open("wb") as fh:
            run([*SSH, ssh, cmd], what=f"metars {day}", stdout=fh, timeout=600)
        part.replace(local)

    pbr.with_container_file(ssh, ctr, HERE / "metar_dump.py", body)
    manifest[day] = want_end.isoformat()
    manifest_p.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    n = sum(1 for _ in local.open())
    status("ok" if n else "unknown", f"metars {day}", f"{n} rows to {want_end:%d %H:%M}Z")


def cmd_pull(a) -> None:
    cases = Path(a.cases)
    ssh, data, ctr, cdata = _server()
    inv = remote_inventory(ssh, data)
    now = datetime.now(timezone.utc)
    oldest = now - timedelta(days=a.days)

    if not (cases / "terrain_mask.npz").exists():
        _scp_get(ssh, f"{data}/hewson/terrain_mask.npz", cases / "terrain_mask.npz")
        status("ok", "terrain_mask.npz")

    times = sorted({parse_cycle(c) for c in inv["dwd"] | inv["mo"]})
    days = set()
    for t in times:
        if t < oldest:
            continue
        init = hewson_init(t)
        hour = int((t - init).total_seconds() // 3600)
        models = [m for m in MODELS if init_file(init) in inv["hewson"][m]]
        if not models:
            status("unknown", f"{cycle_name(t)}", f"no Hewson run {init:%d %H}Z on prod yet -- later")
            continue
        got = []
        for src, (sub, files) in SOURCES.items():
            if cycle_name(t) not in inv[src]:
                continue
            for f in files:
                local = cases / "charts" / src / cycle_name(t) / f
                if not local.exists():
                    _scp_get(ssh, f"{data}/{sub}/{cycle_name(t)}/{f}", local)
            got.append(src)
        for m in models:
            local = cases / "grids" / m / init_file(init)
            if not local.exists():
                _trim(ssh, ctr, cdata, m, init_file(init), local)
        missing = sorted(set(MODELS) - set(models))
        status("ok", f"{cycle_name(t)}", f"charts {'+'.join(got)}, Hewson {init:%d %H}Z +{hour}h "
               f"{'+'.join(models)}" + (f", missing {'+'.join(missing)}" if missing else ""))
        days.add(t.strftime("%Y-%m-%d"))
    for day in sorted(days):
        _metars(ssh, ctr, day, cases, now)


def cmd_list(a) -> None:
    cases = Path(a.cases)
    for src in SOURCES:
        for d in sorted((cases / "charts" / src).glob("*")):
            t = parse_cycle(d.name)
            init = hewson_init(t)
            ms = [m for m in MODELS if (cases / "grids" / m / init_file(init)).exists()]
            scored = (cases / "scores" / f"{d.name}.json").exists()
            print(f"{src:4} {d.name}  Hewson {init:%d %H}Z +{(t - init).seconds // 3600}h "
                  f"{','.join(ms) or '-'}  {'scored' if scored else 'unscored'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull"); p.add_argument("cases"); p.add_argument("--days", type=float, default=2.0)
    p = sub.add_parser("list"); p.add_argument("cases")
    a = ap.parse_args(argv)
    try:
        {"pull": cmd_pull, "list": cmd_list}[a.cmd](a)
    except Failed as e:
        status("problem", e.what, e.evidence)
        return e.code
    except subprocess.TimeoutExpired as e:
        status("problem", "timeout", str(e)[:200])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
