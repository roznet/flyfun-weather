"""Nightly archive and retention for the observed-cells root (#658).

pack → verify → prune on a synthetic root, the NAS plan, the event
classification, and restore + replay reproducing a day's catalogues.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from weatherbrief.observed.cells import DEFAULT_POLICY
from weatherbrief.observed.cells import archive as A
from weatherbrief.observed.cells.__main__ import main
from weatherbrief.observed.cells.catalogue import catalogue_path, failure_path, write_catalogue
from weatherbrief.observed.cells.runner import FrameCache, Workspace, analyse_tick, replay
from weatherbrief.observed.frames import SOURCE_EUMETSAT_LI, SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE, FrameStore

from .cells_helpers import scene, write_dbzh

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
TODAY = NOW.date()
SOURCES = (SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE, SOURCE_EUMETSAT_LI)


def _at(day, hhmm: str) -> datetime:
    return datetime(day.year, day.month, day.day, int(hhmm[:2]), int(hhmm[2:]), tzinfo=timezone.utc)


def _frame(root: Path, source: str, t: datetime, payload: bytes = b"payload") -> None:
    ext = "h5" if source.startswith("opera") else "nc"
    d = root / source
    d.mkdir(parents=True, exist_ok=True)
    stamp = t.strftime("%Y%m%dT%H%M")
    (d / f"{stamp}.{ext}").write_bytes(payload + stamp.encode())
    (d / f"{stamp}.json").write_text(json.dumps({"valid_time": t.isoformat()}))


def _catalogue(root: Path, t: datetime, cells=(), unavailable=(), policy="cells-1+aaaa", rev="abc123") -> None:
    write_catalogue(catalogue_path(root, t), {
        "schema": "observed-cells/1", "policy_version": policy, "code_revision": rev,
        "valid_time": t.isoformat(), "unavailable": list(unavailable), "cells": list(cells),
    })


def _day(root: Path, day, stamps=("0600", "1800")) -> None:
    """Frames of every source + catalogues, scores and runs for one day."""
    for hhmm in stamps:
        t = _at(day, hhmm)
        for source in SOURCES:
            _frame(root, source, t)
        _catalogue(root, t)
    cells = root / "cells"
    for sub in ("scores", "runs"):
        (cells / sub).mkdir(parents=True, exist_ok=True)
        (cells / sub / f"{A.day_str(day)}.jsonl").write_text('{"row": 1}\n')


def _nas_sums(staging: Path, out: Path, corrupt: str | None = None) -> Path:
    """What the job computes on the NAS copy: ``sha256sum`` lines, ``./``-relative."""
    lines = []
    for p in sorted(staging.rglob("*.tar*")):
        rel = p.relative_to(staging).as_posix()
        digest = A.sha256_file(p)
        if corrupt and rel.endswith(corrupt):
            digest = "0" * 64
        lines.append(f"{digest}  ./{rel}")
    out.write_text("\n".join(lines) + "\n")
    return out


def _archive(root: Path, day, tmp_path: Path, corrupt: str | None = None) -> dict:
    staging = A.default_staging(root)
    A.pack_day(root, day, staging, sources=SOURCES, now=NOW)
    sums = _nas_sums(staging, tmp_path / f"sums-{A.day_str(day)}.txt", corrupt)
    return A.verify_day(root, day, staging, sums, now=NOW)


# --- pack ------------------------------------------------------------------------


def test_pack_writes_tars_and_a_manifest_with_gaps_versions_and_sums(tmp_path):
    root = tmp_path / "root"
    day = TODAY - timedelta(days=3)
    _day(root, day)
    _catalogue(root, _at(day, "1200"), policy="cells-2+bbbb", rev="def456")
    failure_path(root, _at(day, "1205")).write_text("{}")
    out = tmp_path / "staging"
    m = A.pack_day(root, day, out, sources=SOURCES, now=NOW, code_revision="packer")

    d = A.day_str(day)
    paths = {t["path"]: t for t in m["tars"]}
    assert set(paths) == {f"{s}/2026/{d}.tar" for s in SOURCES} | {f"cells/2026/{d}.tar.gz"}
    dbzh = paths[f"opera_dbzh/2026/{d}.tar"]
    assert dbzh["files"] == 4  # two payloads + two sidecars
    assert dbzh["sha256"] == A.sha256_file(out / dbzh["path"])
    # cells: 3 catalogues + 1 failed marker + scores + runs
    assert paths[f"cells/2026/{d}.tar.gz"]["files"] == 6
    assert m["frames"][SOURCE_OPERA_DBZH]["expected"] == 288
    assert m["frames"][SOURCE_OPERA_DBZH]["present"] == 2
    assert m["frames"][SOURCE_OPERA_DBZH]["gaps"][0] == [f"{d}T0000", f"{d}T0555"]
    assert m["frames"][SOURCE_OPERA_RATE]["expected"] == 96
    assert m["policy_versions"] == {"cells-1+aaaa": 2, "cells-2+bbbb": 1}
    assert m["code_revisions"] == {"abc123": 2, "def456": 1}
    assert m["failed_markers"] == 1
    assert m["event"]["policy"]["min_flashes"] == 10  # thresholds travel with the answer
    assert A.read_manifest(out / f"manifest/{d}.json")["day"] == d


def test_repacking_gives_identical_tars(tmp_path):
    root = tmp_path / "root"
    day = TODAY - timedelta(days=3)
    _day(root, day)
    first = A.pack_day(root, day, tmp_path / "a", sources=SOURCES, now=NOW)
    second = A.pack_day(root, day, tmp_path / "b", sources=SOURCES, now=NOW + timedelta(hours=1))
    assert [t["sha256"] for t in first["tars"]] == [t["sha256"] for t in second["tars"]]


def test_pack_refuses_an_incomplete_or_already_verified_day(tmp_path):
    root = tmp_path / "root"
    _day(root, TODAY)
    with pytest.raises(ValueError, match="not complete"):
        A.pack_day(root, TODAY, tmp_path / "s", sources=SOURCES, now=NOW)
    # Yesterday packs only after the settle time.
    yday = TODAY - timedelta(days=1)
    with pytest.raises(ValueError, match="not complete"):
        A.pack_day(root, yday, tmp_path / "s", sources=SOURCES, now=A.day_start(TODAY) + timedelta(minutes=10))
    old = TODAY - timedelta(days=5)
    _day(root, old)
    assert _archive(root, old, tmp_path)["verified"]
    with pytest.raises(ValueError, match="already verified"):
        A.pack_day(root, old, tmp_path / "s", sources=SOURCES, now=NOW)


def test_pending_lists_complete_unverified_days(tmp_path):
    root = tmp_path / "root"
    for n in (0, 1, 2, 3):
        _day(root, TODAY - timedelta(days=n))
    assert _archive(root, TODAY - timedelta(days=3), tmp_path)["verified"]
    assert A.pending_days(root, NOW) == [TODAY - timedelta(days=2), TODAY - timedelta(days=1)]


# --- verify + prune ----------------------------------------------------------------


@pytest.fixture
def hot(tmp_path):
    """Days at -100, -50, -40 (unverified), -2, -1 and today."""
    root = tmp_path / "root"
    days = {k: TODAY - timedelta(days=n) for k, n in
            {"old": 100, "mid": 50, "unv": 40, "d2": 2, "d1": 1, "today": 0}.items()}
    for day in days.values():
        _day(root, day)
    for key in ("old", "mid", "d2", "d1"):
        assert _archive(root, days[key], tmp_path)["verified"]
    return root, days


def _exists(root, source, t):
    return FrameStore(root).has(source, t)


def test_prune_keeps_48h_of_frames_and_90d_of_cells_only_for_verified_days(hot):
    root, days = hot
    result = A.prune(root, now=NOW, execute=True)
    assert result["kept_unarchived"] == [] and result["skipped"] == []
    for source in SOURCES:
        # Verified and past 48 h: gone.
        for key in ("old", "mid"):
            assert not _exists(root, source, _at(days[key], "0600"))
            assert not _exists(root, source, _at(days[key], "1800"))
        # Two days ago: 06:00 is 54 h old, 18:00 only 42 h.
        assert not _exists(root, source, _at(days["d2"], "0600"))
        assert _exists(root, source, _at(days["d2"], "1800"))
        # Not verified, yesterday, today: untouched.
        for key in ("unv", "d1", "today"):
            assert _exists(root, source, _at(days[key], "0600"))
    # Analysis: only the day that ended more than 90 days ago.
    assert not catalogue_path(root, _at(days["old"], "0600")).exists()
    assert not (root / "cells" / "catalogues" / A.day_str(days["old"])).exists()
    assert not (root / "cells" / "scores" / f"{A.day_str(days['old'])}.jsonl").exists()
    assert catalogue_path(root, _at(days["mid"], "0600")).exists()
    assert (root / "cells" / "runs" / f"{A.day_str(days['mid'])}.jsonl").exists()
    # Staging of verified days is cleared — the NAS has it.
    assert not any(p.is_file() for p in A.default_staging(root).rglob("*"))


def test_prune_is_a_dry_run_unless_executed(hot):
    root, days = hot
    result = A.prune(root, now=NOW)
    assert result["deleted"] and result["freed_bytes"] > 0
    assert _exists(root, SOURCE_OPERA_DBZH, _at(days["old"], "0600"))
    assert catalogue_path(root, _at(days["old"], "0600")).exists()


def test_cli_prune_defaults_to_dry_run(hot, monkeypatch, capsys):
    root, days = hot
    monkeypatch.setenv("WB_CELLS_ROOT", str(root))
    assert main(["archive", "prune"]) == 0
    assert "would delete" in capsys.readouterr().out
    assert _exists(root, SOURCE_OPERA_DBZH, _at(days["old"], "0600"))


def test_prune_never_touches_today_or_yesterday_even_with_a_marker(hot):
    root, days = hot
    # Forge a marker for today listing every file, as a buggy job might.
    files = [p for p in root.rglob("*") if p.is_file() and A.day_str(days["today"]) in p.name]
    manifest = {"schema": A.MANIFEST_SCHEMA, "day": A.day_str(days["today"]), "tars": [],
                "members": {"x": [[p.relative_to(root).as_posix(), p.stat().st_size] for p in files]}}
    marker = A.verified_marker(root, days["today"])
    marker.write_text(json.dumps({"manifest": manifest}))
    A.prune(root, now=NOW, keep_frames=timedelta(0), keep_cells=timedelta(0), execute=True)
    for key in ("today", "d1"):
        assert _exists(root, SOURCE_OPERA_DBZH, _at(days[key], "0600"))
        assert catalogue_path(root, _at(days[key], "0600")).exists()


def test_files_that_arrived_after_packing_are_kept(hot):
    root, days = hot
    late = _at(days["old"], "2355")
    _frame(root, SOURCE_OPERA_DBZH, late)
    with (root / "cells" / "runs" / f"{A.day_str(days['old'])}.jsonl").open("a") as h:
        h.write('{"row": 2}\n')
    result = A.prune(root, now=NOW, execute=True)
    assert _exists(root, SOURCE_OPERA_DBZH, late)
    assert (root / "cells" / "runs" / f"{A.day_str(days['old'])}.jsonl").exists()
    assert f"cells/runs/{A.day_str(days['old'])}.jsonl" in result["kept_unarchived"]


def test_a_sha_mismatch_is_not_verified_and_nothing_is_pruned(tmp_path):
    root = tmp_path / "root"
    day = TODAY - timedelta(days=100)
    _day(root, day)
    result = _archive(root, day, tmp_path, corrupt="opera_rate/2026/" + A.day_str(day) + ".tar")
    assert not result["verified"]
    assert "sha256 mismatch" in result["problems"][0]
    assert not A.verified_marker(root, day).exists()
    assert A.prune(root, now=NOW, execute=True)["deleted"] == []
    assert _exists(root, SOURCE_OPERA_DBZH, _at(day, "0600"))


def test_a_tar_missing_from_the_remote_sums_is_not_verified(tmp_path):
    root = tmp_path / "root"
    day = TODAY - timedelta(days=5)
    _day(root, day)
    staging = A.default_staging(root)
    A.pack_day(root, day, staging, sources=SOURCES, now=NOW)
    sums = _nas_sums(staging, tmp_path / "sums.txt")
    sums.write_text("\n".join(line for line in sums.read_text().splitlines() if "cells/" not in line))
    result = A.verify_day(root, day, staging, sums, now=NOW)
    assert not result["verified"] and "not in remote sums" in result["problems"][0]



def test_a_file_that_grows_mid_pack_records_the_archived_size_and_is_kept(tmp_path, monkeypatch):
    root = tmp_path / "root"
    day = TODAY - timedelta(days=100)
    _day(root, day)
    runs = root / "cells" / "runs" / f"{A.day_str(day)}.jsonl"
    archived = runs.stat().st_size
    real = A.tarfile.TarFile.gettarinfo

    def grow_after_sizing(self, name=None, arcname=None, fileobj=None):
        info = real(self, name, arcname, fileobj)
        if name == str(runs):
            with runs.open("a") as h:
                h.write('{"row": 2}\n')
        return info

    monkeypatch.setattr(A.tarfile.TarFile, "gettarinfo", grow_after_sizing)
    assert _archive(root, day, tmp_path)["verified"]
    monkeypatch.undo()
    rel = f"cells/runs/{A.day_str(day)}.jsonl"
    manifest = A.read_manifest(A.default_staging(root) / A.manifest_rel(day))
    sizes = {name: size for members in manifest["members"].values() for name, size in members}
    assert sizes[rel] == archived < runs.stat().st_size
    result = A.prune(root, now=NOW, execute=True)
    assert runs.exists() and rel in result["kept_unarchived"]


def test_verify_rejects_a_staged_manifest_for_another_day(tmp_path):
    root = tmp_path / "root"
    day, other = TODAY - timedelta(days=5), TODAY - timedelta(days=6)
    _day(root, day)
    staging = A.default_staging(root)
    A.pack_day(root, day, staging, sources=SOURCES, now=NOW)
    sums = _nas_sums(staging, tmp_path / "sums.txt")
    (staging / A.manifest_rel(other)).write_bytes((staging / A.manifest_rel(day)).read_bytes())
    result = A.verify_day(root, other, staging, sums, now=NOW)
    assert not result["verified"] and "not " + A.day_str(other) in result["problems"][0]
    assert not A.verified_marker(root, other).exists()


# --- event classification ---------------------------------------------------------


def _cell(tier="core41", flashes=0, dbz=45.0, cid="c"):
    return {"id": cid, "tier": tier, "flashes": flashes, "peak_dbz": dbz}


def test_event_rules():
    quiet = [{"valid_time": "t0", "unavailable": [], "cells": [_cell(flashes=12), _cell(flashes=3)]}]
    assert A.classify_event(quiet)["event"] is False
    busy = [{"valid_time": "t1", "unavailable": [], "cells": [_cell(flashes=10)] * 3}]
    out = A.classify_event(quiet + busy)
    assert out["event"] is True and out["peak_cells"] == 3 and out["peak_time"] == "t1"
    strong = [{"valid_time": "t2", "unavailable": [], "cells": [_cell("rain20", 60, 56.0, "big")]}]
    out = A.classify_event(strong)
    assert out["event"] is True and out["strongest"]["id"] == "big"
    weak_core = [{"valid_time": "t3", "unavailable": [], "cells": [_cell("core35", 60, 54.0)]}]
    assert A.classify_event(weak_core)["event"] is False


def test_event_unknown_without_catalogues_or_lightning():
    assert A.classify_event([])["event"] is None
    dark = [{"valid_time": f"t{i}", "unavailable": [{"what": "lightning"}], "cells": [_cell(flashes=None)]}
            for i in range(3)]
    out = A.classify_event(dark)
    assert out["event"] is None and "lightning in only 0/3" in out["reasons"][0]


# --- NAS plan ------------------------------------------------------------------------


def _nas_manifest(d: Path, day, event, schema=A.MANIFEST_SCHEMA, extra_tars=()) -> None:
    s = A.day_str(day)
    tars = [{"path": A.frames_tar_rel(src, day)} for src in SOURCES]
    tars += [{"path": A.cells_tar_rel(day)}, *extra_tars]
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{s}.json").write_text(json.dumps({"schema": schema, "day": s, "tars": tars,
                                             "event": {"event": event}}))


def test_nas_plan_keeps_events_pins_unknowns_and_recent_days(tmp_path):
    mdir = tmp_path / "manifest"
    old = TODAY - timedelta(days=400)
    days = {k: old - timedelta(days=i) for i, k in enumerate(
        ["quiet", "event", "unknown", "pinned", "corrupt", "wrongschema"])}
    recent = TODAY - timedelta(days=200)
    _nas_manifest(mdir, days["quiet"], False,
                  extra_tars=[{"path": f"cells/2025/{A.day_str(days['quiet'])}.tar"}])
    _nas_manifest(mdir, days["event"], True)
    _nas_manifest(mdir, days["unknown"], None)
    _nas_manifest(mdir, days["pinned"], False)
    (mdir / f"{A.day_str(days['corrupt'])}.json").write_text("{not json")
    _nas_manifest(mdir, days["wrongschema"], False, schema="something-else")
    _nas_manifest(mdir, recent, False)
    plan = A.nas_plan(mdir, {A.day_str(days["pinned"]): "flown"}, TODAY)
    q = A.day_str(days["quiet"])
    assert sorted(plan["delete"]) == sorted(f"{s}/{days['quiet'].year}/{q}.tar" for s in SOURCES)
    assert not any(p.startswith("cells/") for p in plan["delete"])
    assert plan["days"] == {"planned": 1, "event day": 1, "event unknown": 1, "keep-days": 1,
                            "unreadable manifest": 2, "recent": 1}


def test_nas_plan_only_lists_tars_still_present(tmp_path):
    mdir = tmp_path / "manifest"
    day = TODAY - timedelta(days=400)
    _nas_manifest(mdir, day, False)
    keep = A.frames_tar_rel(SOURCE_OPERA_DBZH, day)
    assert A.nas_plan(mdir, {}, TODAY, present={keep})["delete"] == [keep]


def test_keep_days_file_must_exist_and_parse(tmp_path):
    with pytest.raises(FileNotFoundError):
        A.read_keep_days(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text('{"2026-08-27": "x"}')
    with pytest.raises(ValueError):
        A.read_keep_days(bad)
    good = tmp_path / "keep.json"
    good.write_text('{"20260827": "flown, ground truth"}')
    assert A.read_keep_days(good) == {"20260827": "flown, ground truth"}


def test_cli_nas_plan_prints_one_path_per_line(tmp_path, capsys):
    mdir = tmp_path / "manifest"
    day = TODAY - timedelta(days=400)
    _nas_manifest(mdir, day, False)
    keep = tmp_path / "keep-days.json"
    keep.write_text("{}")
    assert main(["archive", "nas-plan", "--manifests", str(mdir), "--keep-days", str(keep),
                 "--today", A.day_str(TODAY)]) == 0
    assert sorted(capsys.readouterr().out.split()) == sorted(A.frames_tar_rel(s, day) for s in SOURCES)


# --- restore + replay ---------------------------------------------------------------


SIZE = 160
FRAMES = 6
DAY0 = datetime(2026, 10, 3, 0, 0, tzinfo=timezone.utc)


def test_restore_then_replay_reproduces_the_days_catalogues(tmp_path):
    # Starts at 00:00 with no earlier day: replay begins with the same lineage
    # break the live loop had, so every catalogue must match byte for byte.
    live = tmp_path / "live"
    store = FrameStore(live, retain_all=True)
    times = [DAY0 + timedelta(minutes=5 * i) for i in range(FRAMES)]
    for i, t in enumerate(times):
        write_dbzh(store, t, scene(SIZE, [(70, 60, 50, 7)], shift=(1.0 * i, 2.0 * i)))
    ws = Workspace(live)
    assert analyse_tick(ws, times[-1] + timedelta(minutes=1), timedelta(hours=2), DEFAULT_POLICY,
                        FrameCache(ws.frames), (SOURCE_OPERA_DBZH,)) == FRAMES

    staging = tmp_path / "staging"
    manifest = A.pack_day(live, DAY0.date(), staging, sources=(SOURCE_OPERA_DBZH,), now=NOW)
    assert manifest["catalogues"] == FRAMES
    scratch = tmp_path / "scratch"
    restored = A.restore_day(DAY0.date(), staging, scratch)
    assert restored["files"] == sum(t["files"] for t in manifest["tars"])
    out = tmp_path / "replay"
    assert replay(scratch, out, times[0], times[-1], DEFAULT_POLICY, sources=(SOURCE_OPERA_DBZH,)) == FRAMES
    for t in times:
        assert catalogue_path(out, t).read_bytes() == catalogue_path(live, t).read_bytes()
        assert catalogue_path(scratch, t).read_bytes() == catalogue_path(live, t).read_bytes()

    with pytest.raises(FileExistsError):
        A.restore_day(DAY0.date(), staging, scratch)


def test_cli_restore_refuses_the_live_root(tmp_path, monkeypatch):
    monkeypatch.setenv("WB_CELLS_ROOT", str(tmp_path))
    with pytest.raises(SystemExit):
        main(["archive", "restore", "--day", "20261003", "--from", str(tmp_path / "x"), "--to", str(tmp_path)])
