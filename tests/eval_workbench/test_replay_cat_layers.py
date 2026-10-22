"""Smoke test for ``scripts/replay_cat_layers.py`` (#861).

The script is the pre-landing check for CAT layer-grouping changes, run by
hand against prod packs. Pinned here so it can't rot between uses: a dump of a
real (fixture) pack carries layers per point/model, and ``--compare`` flags a
cruise altitude that a regrouped layer now covers.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

# Reuse the written-pack fixture (and the fixtures it depends on).
from test_rerun_deep import cross_sections, hourly, pack_dir, route  # noqa: F401

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "replay_cat_layers.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("replay_cat_layers", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dump_pack_has_layers_per_point_and_model(pack_dir):
    dump = _load_script().dump_pack(pack_dir)
    assert dump["cruise_ft"] == 8000
    assert set(dump["points"]) == {f"{i}/gfs" for i in range(5)}
    for pt in dump["points"].values():
        assert set(pt) == {"ri", "e_shear"}
    json.dumps(dump)  # the --out file must be serialisable


def test_compare_flags_cruise_newly_inside_a_merged_layer(tmp_path, capsys):
    split = [[4500, 5500, "moderate", 850, 850], [9500, 10500, "moderate", 700, 700]]
    merged = [[5000, 10000, "moderate", 850, 700]]
    for name, layers in (("a", split), ("b", merged)):
        (tmp_path / f"{name}.json").write_text(json.dumps({
            "pack": {"cruise_ft": 8000,
                     "points": {"0/ecmwf": {"ri": [], "e_shear": layers}}},
        }))
    _load_script().compare(tmp_path / "a.json", tmp_path / "b.json")
    out = capsys.readouterr().out
    assert "cruise 8000 ft now inside" in out
    assert '"cruise_newly_in_layer": 1' in out


def test_compare_reports_missing_points_distinctly(tmp_path, capsys):
    layer = [[5000, 10000, "moderate", 850, 700]]
    for name, key in (("a", "0/ecmwf"), ("b", "1/ecmwf")):
        (tmp_path / f"{name}.json").write_text(json.dumps({
            "pack": {"cruise_ft": 8000,
                     "points": {key: {"ri": [], "e_shear": layer}}},
        }))
    _load_script().compare(tmp_path / "a.json", tmp_path / "b.json")
    out = capsys.readouterr().out
    assert "0/ecmwf: missing in B" in out
    assert "1/ecmwf: missing in A" in out
    assert '"soundings_changed": 0' in out
