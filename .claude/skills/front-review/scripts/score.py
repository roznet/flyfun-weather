#!/usr/bin/env python3
"""front-review scorer: our Hewson front lines against the drawn analyses.

    score CASES T [--plot]           score one analysis time (e.g. 2026-11-22T00Z)
                                     -> CASES/scores/T.json, CASES/plots/T.png
    score-all CASES [--rescore]      every unscored analysis time in the library
    show CASES T                     the compact table for one scored time
    record RECORD CASES T NOTES.json append the time's score rows + your notes
                                     (--replace to redo a time already recorded)
    tally RECORD [--since DAY] [--ref dwd|mo]
                                     the running scoreboard

Run with the repo venv (it imports weatherbrief). Definitions, all per
(reference, model, level, variant), on ~0.2° cells along the lines:

* boundary  share of the reference front length with one of our lines that runs
            roughly parallel (<= 45°), within 150 km along the front, and within
            [-150, 150 + slope shift] km across it, positive toward the cold air.
            The shift (SLOPE_SHIFT_KM) is the textbook displacement of a sloping
            front at that level; the measured offsets refine it over time.
* false     share of our line length that matches no reference front that way.
* type      on matched cold/warm cells away from junctions (a reference cell
            within 150 km of a reference front of another type), how often our
            label is the drawn one. Junction cells are counted apart. Occluded
            fronts score for boundary only.
* offset    signed across-front distance of each match (km, + = cold side).
* missed    why each unmatched reference cell has no line: no_signal (no front
            axis with real gradient nearby), fragmented (axis points passed every
            gate but sat in pieces < 200 km), gated:<gate> (the gate blocking most
            nearby axis points), and how many were matched at another level.

Variants: the gate presets default / sensitive / strict; gap75 (default gates,
gaps <= 75 km bridged before the length check); motion (default lines, each point
labelled by which way the axis moves over the next 3 h instead of by the advection
sign); multilevel (default lines kept only where a parallel default line exists
at another level within 300 km).

METAR front passages (tentative thresholds, see detect_passages) are scored
against both analyses and every model/level as an independent surface check.
Each time also stores a station baseline: the share of the in-domain stations that
reported in the window lying within MATCH_KM of each set of lines. The tally's lift
is the share of passages within MATCH_KM divided by that baseline (1 = no better
than any station), so a set that covers more of the map earns no credit for it.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import warnings

import numpy as np
from scipy.spatial import cKDTree

warnings.filterwarnings("ignore", message="Mean of empty slice")

MATCH_KM = 150.0
CELL_DEG = 0.2
DOMAIN = (36.0, 58.5, -16.5, 27.0)        # Hewson grid minus a 1° edge margin
PARALLEL_COS = float(np.cos(np.radians(45)))
JUNCTION_KM = 150.0
MULTILEVEL_KM = 300.0
LEVELS = (925, 850, 700)
MODELS = ("ecmwf", "gfs", "icon")
PRESETS = ("default", "sensitive", "strict")
VARIANTS = PRESETS + ("gap75", "motion", "multilevel")
# Expected displacement of a sloping front toward the cold air at each level, km
# (warm ~1:150, cold ~1:80; occluded treated as cold). Refined from `offset`.
SLOPE_SHIFT_KM = {
    925: {"warm": 100, "cold": 50, "occluded": 50},
    850: {"warm": 220, "cold": 110, "occluded": 110},
    700: {"warm": 450, "cold": 240, "occluded": 240},
}
PRESSURE_KINK_HPA_H = 0.3                  # METAR passage: tendency after − before, hPa/h
MOTION_STILL_KM = 10.0                     # < 10 km in 3 h (~2 kt): quasi-stationary
KINDS = ("cold", "warm", "occluded")


# --------------------------------------------------------------------- geometry

def xyz(ll: np.ndarray) -> np.ndarray:
    ll = np.asarray(ll, dtype=float).reshape(-1, 2)
    la, lo = np.radians(ll[:, 0]), np.radians(ll[:, 1])
    return np.c_[np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)] * 6371.0


def enu(ll: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """(lat, lon) points -> local east/north km around ``ref``."""
    ll = np.asarray(ll, dtype=float).reshape(-1, 2)
    return np.c_[(ll[:, 1] - ref[1]) * 111.0 * np.cos(np.radians(ref[0])), (ll[:, 0] - ref[0]) * 111.0]


def in_domain(lat, lon):
    return (lat >= DOMAIN[0]) & (lat <= DOMAIN[1]) & (lon >= DOMAIN[2]) & (lon <= DOMAIN[3])


def bin_cells(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Unique ~CELL_DEG cells, one representative point each (≈ uniform along a line)."""
    ky = np.floor(lat / CELL_DEG).astype(int)
    kx = np.floor(lon * np.cos(np.radians(lat)) / CELL_DEG).astype(int)
    _, first = np.unique(np.c_[ky, kx], axis=0, return_index=True)
    first.sort()
    return np.c_[lat[first], lon[first]]


def tangents(pts: np.ndarray, groups: np.ndarray, radius: float = 80.0) -> np.ndarray:
    """Unit tangent (east, north) per point: principal axis of same-group points nearby."""
    tan = np.tile([1.0, 0.0], (len(pts), 1))
    if not len(pts):
        return tan
    tree = cKDTree(xyz(pts))
    for j, nb in enumerate(tree.query_ball_point(xyz(pts), radius)):
        nb = [k for k in nb if groups[k] == groups[j]]
        if len(nb) > 2:
            q = enu(pts[nb], pts[j])
            tan[j] = np.linalg.svd(q - q.mean(0), full_matrices=False)[2][0]
    return tan


# -------------------------------------------------------------------- references

def _ref_specs():
    from weatherbrief.fetch.dwd_charts import _CHART_CALIBRATIONS as DWD
    from weatherbrief.fetch.metoffice_charts import _CHART_CALIBRATIONS as MO

    def dwd_masks(im):
        r, g, b = im[..., 0], im[..., 1], im[..., 2]
        return {"warm": (r > 190) & (g < 70) & (b < 70),
                "cold": (b > 190) & (r < 70) & (g < 70),
                "occluded": (r > 190) & (b > 190) & (g > 100) & (g < 160)}

    def mo_masks(im):
        def near(c, tol=40):
            return (np.abs(im - np.array(c)).sum(axis=2) <= tol)
        m = {"warm": near((254, 50, 0)), "cold": near((0, 50, 254)), "occluded": near((229, 50, 229))}
        for k in m:                       # the title box (logo, "Analysis chart")
            m[k][:60, :300] = False
        return m

    return {"dwd": ("ana.png", DWD["analysis"], dwd_masks),
            "mo": ("ana.gif", MO["colour"], mo_masks)}


def chart_to_lonlat(px: np.ndarray, py: np.ndarray, cal: dict):
    import pyproj
    a, b, c, d, e, f, g, h = cal["homography"]
    hi = np.linalg.inv(np.array([[a, b, c], [d, e, f], [g, h, 1.0]]))
    v = hi @ np.vstack([px, py, np.ones_like(px)])
    lon, lat = pyproj.Proj(**cal["proj"])(v[0] / v[2], v[1] / v[2], inverse=True)
    return np.asarray(lon), np.asarray(lat)


def extract_reference(path: Path, cal: dict, masks_fn) -> dict:
    """Drawn fronts -> cells with kind and tangent, inside the scoring domain."""
    from PIL import Image
    im = np.array(Image.open(path).convert("RGB")).astype(int)
    pts, kinds, pixels = [], [], {}
    for kind, m in masks_fn(im).items():
        py, px = np.nonzero(m)
        pixels[kind] = int(m.sum())
        if not len(px):
            continue
        lon, lat = chart_to_lonlat(px.astype(float), py.astype(float), cal)
        sel = in_domain(lat, lon)
        if sel.any():
            c = bin_cells(lat[sel], lon[sel])
            pts.append(c); kinds += [kind] * len(c)
    pts = np.vstack(pts) if pts else np.zeros((0, 2))
    kinds = np.array(kinds)
    ref = {"pts": pts, "kind": kinds, "tan": tangents(pts, kinds), "pixels": pixels}
    # junction: within JUNCTION_KM of a reference front of another kind
    junction = np.zeros(len(pts), bool)
    if len(pts):
        tree = cKDTree(xyz(pts))
        for j, nb in enumerate(tree.query_ball_point(xyz(pts), JUNCTION_KM)):
            junction[j] = any(kinds[k] != kinds[j] for k in nb)
    ref["junction"] = junction
    return ref


# ------------------------------------------------------------------ our lines

class ModelCase:
    """One model's trimmed grids for an analysis time: grids at hour and hour+3."""

    def __init__(self, path: Path, model: str, hour: int, elevation: np.ndarray):
        from weatherbrief.frontal.grid import terrain_mask_for_level
        from weatherbrief.frontal.sources import SnapshotFieldSource
        self.model, self.hour = model, hour
        self.src = SnapshotFieldSource(path, model_name=model)
        self.lat, self.lon = self.src.lat, self.src.lon
        self.elevation = elevation
        with np.load(path) as z:
            self.background = {L: z[f"background_{L}"].astype(np.float64) for L in LEVELS
                               if f"background_{L}" in z.files}
        self.tmask = {L: terrain_mask_for_level(elevation, L) for L in LEVELS}
        self._grids: dict = {}
        self._heavy: dict = {}

    def grids(self, level: int, hour: int):
        key = (level, hour)
        if key not in self._grids:
            self.src.terrain_mask = self.tmask[level]
            self._grids[key] = self.src.grids_at_hour(self.model, hour, level)
        return self._grids[key]

    def heavy_gradient(self, level: int):
        """θe gradient on the TFP smoothing scale (cold→warm direction, K/100 km)."""
        if level not in self._heavy:
            from weatherbrief.frontal.detect import theta_e_gradient_components
            g = self.grids(level, self.hour)
            hx, hy = theta_e_gradient_components(g.theta_e, self.lat, self.lon, terrain_mask=None,
                                                 smooth_sigma=4.0)
            self._heavy[level] = (hx * 100, hy * 100)
        return self._heavy[level]

    def sample(self, arr, lat, lon):
        from weatherbrief.frontal.route_sampling import bilinear_sample
        return bilinear_sample(arr, self.lat, self.lon, lat, lon)

    def cold_dir(self, level: int, lat: float, lon: float) -> np.ndarray:
        hx, hy = self.heavy_gradient(level)
        v = -np.array([self.sample(hx, lat, lon), self.sample(hy, lat, lon)])
        n = np.hypot(*v)
        return v / n if np.isfinite(n) and n > 0 else np.array([np.nan, np.nan])

    # -- lines --------------------------------------------------------------
    def lines(self, level: int, variant: str) -> list[dict]:
        """[{kind, verts: [(lat, lon, advection)]}] for one variant (not multilevel)."""
        from weatherbrief.frontal.contour_fronts import extract_front_lines
        from weatherbrief.frontal.gates import get_preset
        preset = variant if variant in PRESETS else "default"
        cfg = get_preset(preset, level_hPa=level)
        g = self.grids(level, self.hour)
        bg = self.background.get(level)
        if variant == "gap75":
            return self._gap_lines(g, cfg, bg, level)
        out = [{"kind": pl.kind, "verts": [(v.lat, v.lon, v.advection) for v in pl.vertices]}
               for pl in extract_front_lines(g, self.lat, self.lon, cfg, background=bg,
                                             terrain_mask=self.tmask[level])]
        if variant == "motion":
            for line in out:
                pk = [self.motion_kind(level, la, lo) for la, lo, _ in line["verts"]]
                line["point_kinds"] = pk
                votes = collections.Counter(k for k in pk if k in ("warm", "cold"))
                if votes:   # majority of the motion labels, like _classify_run for advection
                    line["kind"] = "warm" if votes["warm"] >= votes["cold"] else "cold"
                elif any(k == "quasi-stationary" for k in pk):
                    line["kind"] = "quasi-stationary"
        return out

    def _gap_lines(self, g, cfg, bg, level, max_gap_km=75.0, min_len=200.0):
        from contourpy import contour_generator
        from weatherbrief.frontal.contour_fronts import (_classify_run, _gate_vertex,
                                                         _index_to_coord, _polyline_length_km)
        from weatherbrief.frontal.route_sampling import haversine_km
        ny, nx = g.tfp.shape
        runs = []
        for line in contour_generator(x=np.arange(nx), y=np.arange(ny), z=g.tfp, corner_mask=True).lines(0.0):
            run, gap, prev = [], 0.0, None
            for c, r in line:
                la, lo = _index_to_coord(r, self.lat), _index_to_coord(c, self.lon)
                step = haversine_km(prev[0], prev[1], la, lo) if prev else 0.0
                prev = (la, lo)
                v = _gate_vertex(la, lo, g, self.lat, self.lon, cfg, bg, self.tmask[level])
                if v is not None:
                    run.append(v); gap = 0.0
                elif run:
                    gap += step
                    if gap > max_gap_km:
                        runs.append(run); run, gap = [], 0.0
            runs.append(run)
        return [{"kind": _classify_run(rn, cfg.advection_min),
                 "verts": [(v.lat, v.lon, v.advection) for v in rn]}
                for rn in runs if len(rn) >= 2 and _polyline_length_km(rn) >= min_len]

    def motion_kind(self, level: int, lat: float, lon: float) -> str:
        """Which way the TFP=0 axis moves over the next 3 h, along the θe gradient."""
        g0 = self.grids(level, self.hour)
        g1 = self.grids(level, self.hour + 3)
        if g1 is None:
            return "unknown"
        dx, dy = self.sample(g0.dT_dx, lat, lon), self.sample(g0.dT_dy, lat, lon)
        n = np.hypot(dx, dy)
        if not np.isfinite(n) or n == 0:
            return "unknown"
        ux, uy = dx / n, dy / n                    # cold -> warm
        s = np.arange(-150.0, 150.1, 5.0)
        la = lat + uy * s / 111.0
        lo = lon + ux * s / (111.0 * np.cos(np.radians(lat)))
        tfp = np.array([self.sample(g1.tfp, a, b) for a, b in zip(la, lo)])
        grad = np.array([self.sample(g1.gradient, a, b) for a, b in zip(la, lo)])
        best = None
        for i in range(len(s) - 1):
            # the front axis: TFP goes - to + along the cold->warm direction (|∇θe| peaks);
            # + to - is the gradient minimum between fronts
            if np.isfinite(tfp[i]) and np.isfinite(tfp[i + 1]) and tfp[i] < 0 < tfp[i + 1] \
                    and max(grad[i], grad[i + 1]) >= 3.0:
                z = s[i] + (s[i + 1] - s[i]) * tfp[i] / (tfp[i] - tfp[i + 1])
                if best is None or abs(z) < abs(best):
                    best = z
        if best is None:
            return "unknown"
        if best > MOTION_STILL_KM:
            return "cold"          # axis moved into the warm air: cold air advancing
        if best < -MOTION_STILL_KM:
            return "warm"
        return "quasi-stationary"


def line_cells(lines: list[dict], advection_min: float = 0.5) -> dict:
    """Densify our polylines to <=10 km steps, bin, keep kind + tangent per cell."""
    from weatherbrief.frontal.route_sampling import _classify_kind, haversine_km
    pts, kinds, pkinds, adv, gid = [], [], [], [], []
    for li, line in enumerate(lines):
        v = np.array([(a, b) for a, b, _ in line["verts"]])
        ad = np.array([c for _, _, c in line["verts"]])
        pk = line.get("point_kinds") or [_classify_kind(c, advection_min) for c in ad]
        dense, dk, da = [v[0]], [pk[0]], [ad[0]]
        for i in range(len(v) - 1):
            n = max(1, int(haversine_km(v[i, 0], v[i, 1], v[i + 1, 0], v[i + 1, 1]) / 10))
            for t in np.linspace(1 / n, 1, n):
                dense.append(v[i] + (v[i + 1] - v[i]) * t)
                j = i if t < 0.5 else i + 1
                dk.append(pk[j]); da.append(ad[j])
        dense = np.array(dense)
        sel = in_domain(dense[:, 0], dense[:, 1])
        if not sel.any():
            continue
        dense, dk, da = dense[sel], np.array(dk)[sel], np.array(da)[sel]
        ky = np.floor(dense[:, 0] / CELL_DEG).astype(int)
        kx = np.floor(dense[:, 1] * np.cos(np.radians(dense[:, 0])) / CELL_DEG).astype(int)
        _, first = np.unique(np.c_[ky, kx], axis=0, return_index=True)
        first.sort()
        pts.append(dense[first]); pkinds += list(dk[first]); adv += list(da[first])
        kinds += [line["kind"]] * len(first); gid += [li] * len(first)
    pts = np.vstack(pts) if pts else np.zeros((0, 2))
    gid = np.array(gid)
    return {"pts": pts, "kind": np.array(kinds), "point_kind": np.array(pkinds),
            "adv": np.array(adv), "tan": tangents(pts, gid), "gid": gid}


def subset(cells: dict, keep: np.ndarray) -> dict:
    return {k: (v[keep] if isinstance(v, np.ndarray) and len(v) == len(keep) else v)
            for k, v in cells.items()}


# -------------------------------------------------------------------- matching

def match(ref: dict, ours: dict, level: int, mc: ModelCase) -> dict:
    """Slope-aware, parallel matching of reference cells to our cells (and back)."""
    nr, no = len(ref["pts"]), len(ours["pts"])
    ref_match = np.full(nr, -1)
    ref_off = np.full(nr, np.nan)
    our_ok = np.zeros(no, bool)
    if not nr or not no:
        return {"ref_match": ref_match, "ref_off": ref_off, "our_ok": our_ok}
    otree = cKDTree(xyz(ours["pts"]))
    rmax = MATCH_KM + max(SLOPE_SHIFT_KM[level].values())
    for j in range(nr):
        kind = ref["kind"][j]
        shift = SLOPE_SHIFT_KM[level][kind]
        cand = otree.query_ball_point(xyz(ref["pts"][j])[0], np.hypot(MATCH_KM, MATCH_KM + shift))
        if not cand:
            continue
        t = ref["tan"][j]
        cold = mc.cold_dir(level, *ref["pts"][j])
        if not np.all(np.isfinite(cold)):
            cold = np.array([-t[1], t[0]])
        normal = cold - (cold @ t) * t
        normal = normal / (np.hypot(*normal) or 1.0)
        d = enu(ours["pts"][cand], ref["pts"][j])
        along = np.abs(d @ t)
        perp = d @ normal
        par = np.abs(ours["tan"][cand] @ t) >= PARALLEL_COS
        okm = par & (along <= MATCH_KM) & (perp >= -MATCH_KM) & (perp <= MATCH_KM + shift)
        if okm.any():
            idx = np.nonzero(okm)[0]
            best = idx[np.argmin(np.hypot(along[idx], perp[idx]))]
            ref_match[j] = cand[best]
            ref_off[j] = perp[best]
            our_ok[np.array(cand)[idx]] = True
    # our cells never chosen may still lie in a valid window of some reference cell
    rtree = cKDTree(xyz(ref["pts"]))
    for i in np.nonzero(~our_ok)[0]:
        for j in rtree.query_ball_point(xyz(ours["pts"][i])[0], rmax):
            t = ref["tan"][j]
            if abs(ours["tan"][i] @ t) < PARALLEL_COS:
                continue
            cold = mc.cold_dir(level, *ref["pts"][j])
            if not np.all(np.isfinite(cold)):
                cold = np.array([-t[1], t[0]])
            normal = cold - (cold @ t) * t
            normal = normal / (np.hypot(*normal) or 1.0)
            d = enu(ours["pts"][i], ref["pts"][j])[0]
            if abs(d @ t) <= MATCH_KM and -MATCH_KM <= d @ normal <= MATCH_KM + SLOPE_SHIFT_KM[level][ref["kind"][j]]:
                our_ok[i] = True
                break
    return {"ref_match": ref_match, "ref_off": ref_off, "our_ok": our_ok}


def axis_census(mc: ModelCase, level: int, preset: str) -> tuple[np.ndarray, list]:
    """Every TFP=0 axis point with heavy |∇θe| >= 2 and the gates it fails."""
    from contourpy import contour_generator
    from weatherbrief.frontal.contour_fronts import _gate_vertex, _index_to_coord
    from weatherbrief.frontal.gates import get_preset
    cfg = get_preset(preset, level_hPa=level)
    g = mc.grids(level, mc.hour)
    bg = mc.background.get(level)
    tm = mc.tmask[level]
    hx, hy = mc.heavy_gradient(level)
    hgrad = np.hypot(hx, hy)
    jump_only = cfg.with_overrides(gradient_min=-1e9, anomaly_min=-1e9)
    ny, nx = g.tfp.shape
    pts, fails = [], []
    for line in contour_generator(x=np.arange(nx), y=np.arange(ny), z=g.tfp, corner_mask=True).lines(0.0):
        for c, r in line[::2]:
            la, lo = _index_to_coord(r, mc.lat), _index_to_coord(c, mc.lon)
            if not in_domain(np.array(la), np.array(lo)) or mc.sample(hgrad, la, lo) < 2.0:
                continue
            lg = mc.sample(g.gradient, la, lo)
            f = []
            if not lg >= cfg.gradient_min:
                f.append("gradient")
            if bg is not None:
                b = mc.sample(bg, la, lo)
                if np.isfinite(b) and lg - b < cfg.anomaly_min:
                    f.append("anomaly")
            if mc.sample(tm.astype(float), la, lo) < 0.5:
                f.append("terrain")
            if _gate_vertex(la, lo, g, mc.lat, mc.lon, jump_only, None, None) is None:
                f.append("theta_e_jump")
            pts.append((la, lo)); fails.append(tuple(f))
    return np.array(pts).reshape(-1, 2), fails


def in_window(ref: dict, j: int, pts: np.ndarray, level: int, mc: ModelCase) -> np.ndarray:
    """Points inside reference cell j's slope-aware match window (parallelism not tested)."""
    t = ref["tan"][j]
    cold = mc.cold_dir(level, *ref["pts"][j])
    if not np.all(np.isfinite(cold)):
        cold = np.array([-t[1], t[0]])
    normal = cold - (cold @ t) * t
    normal = normal / (np.hypot(*normal) or 1.0)
    d = enu(pts, ref["pts"][j])
    perp = d @ normal
    return (np.abs(d @ t) <= MATCH_KM) & (perp >= -MATCH_KM) & \
        (perp <= MATCH_KM + SLOPE_SHIFT_KM[level][ref["kind"][j]])


def why_missed(ref: dict, missed: np.ndarray, level: int, census, ours: dict, mc: ModelCase) -> dict:
    """Cause per missed reference cell, from the axis points inside its match window.

    no_signal         no front axis with real gradient in the window
    fragmented        axis points passed every gate but are not on a drawn line
                      (the run was cut and the pieces were under 200 km)
    kept_not_parallel the passing points are on a drawn line, but it crosses the
                      reference front (> 45°) rather than running along it
    gated:<gate>      no point passed; the gate failing most of them
    """
    cpts, cfails = census
    why = collections.Counter()
    if not len(cpts):
        return {"no_signal": int(missed.sum())} if missed.any() else {}
    ctree = cKDTree(xyz(cpts))
    on_line = np.zeros(len(cpts), bool)
    if len(ours["pts"]):
        d, _ = cKDTree(xyz(ours["pts"])).query(xyz(cpts))
        on_line = d <= 20.0
    rmax = np.hypot(MATCH_KM, MATCH_KM + max(SLOPE_SHIFT_KM[level].values()))
    for j in np.nonzero(missed)[0]:
        near = np.array(ctree.query_ball_point(xyz(ref["pts"][j])[0], rmax), dtype=int)
        if len(near):
            near = near[in_window(ref, j, cpts[near], level, mc)]
        if not len(near):
            why["no_signal"] += 1
            continue
        passing = [k for k in near if not cfails[k]]
        if passing:
            why["fragmented" if any(not on_line[k] for k in passing) else "kept_not_parallel"] += 1
        else:
            c = collections.Counter(f for k in near for f in cfails[k])
            why["gated:" + c.most_common(1)[0][0]] += 1
    return dict(why)


def score_block(ref: dict, ours: dict, m: dict, label_key: str) -> dict:
    """Metrics for one (reference, model, level, variant)."""
    nr = len(ref["pts"])
    hit = m["ref_match"] >= 0
    out = {"ref_cells": int(nr), "our_cells": int(len(ours["pts"])),
           "boundary": float(hit.mean()) if nr else None,
           "boundary_by_kind": {k: [int(hit[ref["kind"] == k].sum()), int((ref["kind"] == k).sum())]
                                for k in KINDS if (ref["kind"] == k).any()},
           "false": float((~m["our_ok"]).mean()) if len(ours["pts"]) else None}
    conf, jconf = collections.Counter(), collections.Counter()
    for j in np.nonzero(hit & np.isin(ref["kind"], ["cold", "warm"]))[0]:
        ours_k = str(ours[label_key][m["ref_match"][j]])
        (jconf if ref["junction"][j] else conf)[f"{ref['kind'][j]}>{ours_k}"] += 1
    n = sum(conf.values())
    out["type"] = (sum(v for k, v in conf.items() if k.split(">")[0] == k.split(">")[1]) / n) if n else None
    out["type_n"] = n
    out["confusion"] = dict(conf)
    out["junction_confusion"] = dict(jconf)
    out["offset_km"] = {k: float(np.median(m["ref_off"][hit & (ref["kind"] == k)]))
                        for k in KINDS if (hit & (ref["kind"] == k)).any()}
    return out


# --------------------------------------------------------------- METAR passages

def _circ_mean(deg, w):
    a = np.radians(deg)
    return float(np.degrees(np.arctan2((w * np.sin(a)).sum(), (w * np.cos(a)).sum())) % 360)


def detect_passages(rows: list[dict], t0: datetime, coords: dict) -> list[dict]:
    """Front passages at stations within ±1.5 h of the analysis time t0.

    Tentative signature (Northern Hemisphere): the wind VEERS (clockwise) by >= 40°
    between the 1-3 h before and the 1-3 h after, both sides >= 5 kt, and
      cold: T and Td both fall >= 1.5 °C;   warm: Td rises >= 2 °C and T does not fall;
    and the pressure turns: the QNH tendency after minus the one before is
    >= PRESSURE_KINK_HPA_H (a trough passing). Without it, evening cooling with a
    drainage wind veer reads as a cold front (seen 20 Nov 18Z across France).
    The time of the largest change wins. Thresholds are a first guess -- check events
    against the charts before trusting a tally.
    """
    by = collections.defaultdict(list)
    for r in rows:
        by[r["icao"]].append(r)
    events = []
    for icao, rs in by.items():
        if icao not in coords:
            continue
        ts = np.array([datetime.strptime(r["t"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp()
                       for r in rs])
        order = np.argsort(ts)
        ts = ts[order]
        rs = [rs[i] for i in order]

        def arr(k):
            return np.array([np.nan if r[k] is None else float(r[k]) for r in rs])
        wd, ws, tc, td, qnh = arr("wd"), arr("ws"), arr("tc"), arr("td"), arr("qnh")
        best = None
        for dt in np.arange(-1.5, 1.51, 0.5):
            t = t0.timestamp() + dt * 3600
            before = (ts >= t - 3 * 3600) & (ts <= t - 3600)
            after = (ts >= t + 3600) & (ts <= t + 3 * 3600)
            if before.sum() < 2 or after.sum() < 2:
                continue
            okb = before & np.isfinite(wd) & (ws >= 5)
            oka = after & np.isfinite(wd) & (ws >= 5)
            if okb.sum() < 1 or oka.sum() < 1:
                continue
            veer = (_circ_mean(wd[oka], ws[oka]) - _circ_mean(wd[okb], ws[okb]) + 360) % 360
            dT = np.nanmean(tc[after]) - np.nanmean(tc[before])
            dTd = np.nanmean(td[after]) - np.nanmean(td[before])
            if not (40 <= veer <= 180) or not np.isfinite(dT) or not np.isfinite(dTd):
                continue
            # the pressure trough: the 2-h tendency after the passage minus the one before
            pb = (ts >= t - 3 * 3600) & (ts <= t - 1800)
            pa = (ts >= t + 1800) & (ts <= t + 3 * 3600)
            if pb.sum() < 2 or pa.sum() < 2 or not np.isfinite(qnh[pb | pa]).all():
                continue
            kink = (np.polyfit(ts[pa] / 3600, qnh[pa], 1)[0] - np.polyfit(ts[pb] / 3600, qnh[pb], 1)[0])
            kind = None
            if kink < PRESSURE_KINK_HPA_H:
                continue
            if dT <= -1.5 and dTd <= -1.5:
                kind = "cold"
            elif dTd >= 2.0 and dT >= 0:
                kind = "warm"
            if kind:
                strength = abs(dT) + abs(dTd)
                if best is None or strength > best["strength"]:
                    best = {"icao": icao, "lat": coords[icao][0], "lon": coords[icao][1], "kind": kind,
                            "dt_h": float(dt), "veer": round(veer), "dT": round(float(dT), 1),
                            "dTd": round(float(dTd), 1), "kink_hpa_h": round(float(kink), 2),
                            "strength": float(strength)}
        if best and in_domain(np.array(best["lat"]), np.array(best["lon"])):
            events.append(best)
    return events


def _load_metars(cases: Path, t0: datetime) -> list[dict]:
    rows = []
    lo, hi = t0 - timedelta(hours=6), t0 + timedelta(hours=6)
    for day in {(t0 + timedelta(hours=h)).strftime("%Y-%m-%d") for h in (-6, 0, 6)}:
        p = cases / "metars" / f"{day}.jsonl"
        if not p.exists():
            continue
        for line in p.open():
            r = json.loads(line)
            t = datetime.strptime(r["t"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
            if lo <= t <= hi:
                rows.append(r)
    seen, out = set(), []
    for r in rows:                         # the day files overlap
        k = (r["icao"], r["t"])
        if k not in seen:
            seen.add(k); out.append(r)
    return out


def _station_coords() -> dict:
    import os
    from weatherbrief.tasks.airport_watchlist import get_configs_dir, load_watchlist_with_coords
    db = os.environ.get("AIRPORTS_DB") or str(Path(__file__).resolve().parents[4] / "data" / "nav.db")
    return {a.icao: (a.lat, a.lon) for a in load_watchlist_with_coords(get_configs_dir(), db)}


# ------------------------------------------------------------------------ score

def parse_t(name: str) -> datetime:
    return datetime.strptime(name, "%Y-%m-%dT%HZ").replace(tzinfo=timezone.utc)


def hewson_init(t: datetime) -> datetime:
    return t.replace(hour=0 if t.hour < 12 else 12)


def score_time(cases: Path, tname: str, plot: bool = False) -> dict:
    t = parse_t(tname)
    init = hewson_init(t)
    hour = int((t - init).total_seconds() // 3600)
    elevation = np.load(cases / "terrain_mask.npz")["elevation"]
    refs = {}
    for src, (fname, cal, masks_fn) in _ref_specs().items():
        p = cases / "charts" / src / tname / fname
        if p.exists():
            refs[src] = extract_reference(p, cal, masks_fn)
    result = {"analysis_time": tname, "init": init.strftime("%Y-%m-%dT%HZ"), "hour": hour,
              "references": {s: {"cells": {k: int((r["kind"] == k).sum()) for k in KINDS},
                                 "pixels": r["pixels"]} for s, r in refs.items()},
              "rows": [], "flips": [], "passages": [], "missing_models": []}
    cells_by = {}                          # (model, level, variant) -> our cells
    mcs = {}
    for model in MODELS:
        p = cases / "grids" / model / (init.strftime("%Y-%m-%dT%H:%M:%SZ") + ".npz")
        if not p.exists():
            result["missing_models"].append(model)
            continue
        mc = mcs[model] = ModelCase(p, model, hour, elevation)
        for level in LEVELS:
            for v in VARIANTS[:-1]:
                cells_by[(model, level, v)] = line_cells(mc.lines(level, v))
        # multilevel: default cells with a parallel default line at another level nearby
        for level in LEVELS:
            base = cells_by[(model, level, "default")]
            keep = np.zeros(len(base["pts"]), bool)
            for other in LEVELS:
                if other == level or not len(base["pts"]):
                    continue
                o = cells_by[(model, other, "default")]
                if not len(o["pts"]):
                    continue
                ot = cKDTree(xyz(o["pts"]))
                for i, nb in enumerate(ot.query_ball_point(xyz(base["pts"]), MULTILEVEL_KM)):
                    if not keep[i] and any(abs(o["tan"][k] @ base["tan"][i]) >= PARALLEL_COS for k in nb):
                        keep[i] = True
            cells_by[(model, level, "multilevel")] = subset(base, keep)

    for src, ref in refs.items():
        for model, mc in mcs.items():
            census = {(L, p): axis_census(mc, L, p) for L in LEVELS for p in PRESETS}
            default_hits = {}
            for level in LEVELS:
                m = match(ref, cells_by[(model, level, "default")], level, mc)
                default_hits[level] = (m, m["ref_match"] >= 0)
            for level in LEVELS:
                for v in VARIANTS:
                    ours = cells_by[(model, level, v)]
                    m = default_hits[level][0] if v == "default" else match(ref, ours, level, mc)
                    label = "kind"   # per-line label, as the map draws it (motion: majority of motion)
                    row = score_block(ref, ours, m, label)
                    missed = m["ref_match"] < 0
                    preset = v if v in PRESETS else "default"
                    row["missed"] = why_missed(ref, missed, level, census[(level, preset)], ours, mc)
                    if v == "default":
                        other = np.zeros(len(ref["pts"]), bool)
                        for L2, (_, h) in default_hits.items():
                            if L2 != level:
                                other |= h
                        row["missed_found_other_level"] = int((missed & other).sum())
                    row.update(reference=src, model=model, level=level, variant=v)
                    result["rows"].append(row)
                    if v in ("default", "motion"):
                        for j in np.nonzero((m["ref_match"] >= 0) & np.isin(ref["kind"], ["cold", "warm"]))[0]:
                            i = m["ref_match"][j]
                            ok = str(ours[label][i])
                            if ok == ref["kind"][j]:
                                continue
                            la, lo = ref["pts"][j]
                            e = mc.sample(elevation, la, lo)
                            result["flips"].append({
                                "reference": src, "model": model, "level": level, "variant": v,
                                "lat": round(float(la), 2), "lon": round(float(lo), 2),
                                "drawn": str(ref["kind"][j]), "ours": ok,
                                "junction": bool(ref["junction"][j]),
                                "abs_advection": round(abs(float(ours["adv"][i])), 2),
                                "elevation_m": None if not np.isfinite(e) else round(float(e)),
                            })

    # METAR passages: a surface check of both analyses and every model/level
    rows = _load_metars(cases, t)
    if rows:
        coords = _station_coords()
        for ev in detect_passages(rows, t, coords):
            p = np.array([ev["lat"], ev["lon"]])
            for src, ref in refs.items():
                if len(ref["pts"]):
                    d, j = cKDTree(xyz(ref["pts"])).query(xyz(p)[0])
                    ev[f"{src}_km"] = round(float(d))
                    ev[f"{src}_kind"] = str(ref["kind"][j])
            for model, mc in mcs.items():
                for level in LEVELS:
                    ours = cells_by[(model, level, "default")]
                    if not len(ours["pts"]):
                        continue
                    d, i = cKDTree(xyz(ours["pts"])).query(xyz(p)[0])
                    cold = mc.cold_dir(level, *p)
                    off = float(enu(ours["pts"][i], p)[0] @ cold) if np.all(np.isfinite(cold)) else None
                    ev[f"{model}_{level}"] = {"km": round(float(d)), "toward_cold_km":
                                              None if off is None else round(off), "kind": str(ours["kind"][i])}
            result["passages"].append(ev)
        result["metar_rows"] = len(rows)
        # station baseline: how often any reporting station sits near each set of lines
        st = np.array(sorted({coords[r["icao"]] for r in rows if r["icao"] in coords}), float).reshape(-1, 2)
        st = st[in_domain(st[:, 0], st[:, 1])]
        sets = {src: ref["pts"] for src, ref in refs.items()}
        sets.update({f"{model}_{level}": cells_by[(model, level, "default")]["pts"]
                     for model in mcs for level in LEVELS})
        near = {}
        for k, pts in sets.items():
            if len(st):
                near[k] = 0.0 if not len(pts) else round(float(
                    (cKDTree(xyz(pts)).query(xyz(st))[0] <= MATCH_KM).mean()), 4)
        result["passage_baseline"] = {"stations": int(len(st)), "radius_km": MATCH_KM, "near": near}

    out = cases / "scores" / f"{tname}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, default=float))
    if plot and "dwd" in refs:
        _plot(cases, tname, refs, cells_by, mcs, result["passages"])
    return result


def _plot(cases, tname, refs, cells_by, mcs, passages):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ref = refs["dwd"]
    fig, axes = plt.subplots(3, 3, figsize=(21, 15))
    for i, level in enumerate(LEVELS):
        for j, model in enumerate(MODELS):
            ax = axes[i, j]
            if model not in mcs:
                ax.set_title(f"{model} missing"); continue
            ours = cells_by[(model, level, "default")]
            m = match(ref, ours, level, mcs[model])
            hit = m["ref_match"] >= 0
            g = mcs[model].grids(level, mcs[model].hour)
            ax.pcolormesh(mcs[model].lon, mcs[model].lat, np.where(mcs[model].tmask[level], g.gradient, np.nan),
                          vmin=0, vmax=15, cmap="Greys", shading="auto", alpha=0.5)
            if len(ours["pts"]):
                col = np.where(ours["kind"] == "warm", "red", np.where(ours["kind"] == "cold", "blue", "purple"))
                ax.scatter(ours["pts"][m["our_ok"], 1], ours["pts"][m["our_ok"], 0], s=30, c=col[m["our_ok"]], alpha=0.55)
                ax.scatter(ours["pts"][~m["our_ok"], 1], ours["pts"][~m["our_ok"], 0], s=30,
                           c=col[~m["our_ok"]], alpha=0.15)
            for k, mk in (("cold", "s"), ("warm", "o"), ("occluded", "^")):
                s = ref["kind"] == k
                ax.scatter(ref["pts"][s & hit, 1], ref["pts"][s & hit, 0], s=6, c="tab:green", marker=mk)
                ax.scatter(ref["pts"][s & ~hit, 1], ref["pts"][s & ~hit, 0], s=6, c="k", marker=mk)
            if "mo" in refs:
                ax.scatter(refs["mo"]["pts"][:, 1], refs["mo"]["pts"][:, 0], s=3, c="tab:cyan")
            for ev in passages:
                ax.annotate("C" if ev["kind"] == "cold" else "W", (ev["lon"], ev["lat"]), color="darkorange",
                            fontsize=9, fontweight="bold", ha="center", va="center")
            ax.set_xlim(DOMAIN[2] - 1, DOMAIN[3] + 1); ax.set_ylim(DOMAIN[0] - 1, DOMAIN[1] + 1)
            ax.set_aspect(1 / np.cos(np.radians(47))); ax.grid(alpha=0.3)
            fr = (~m["our_ok"]).mean() if len(ours["pts"]) else float("nan")
            ax.set_title(f"{model} {level} hPa default  boundary {hit.mean():.0%}  false {fr:.0%}")
    fig.suptitle(f"{tname}: DWD fronts green=caught black=missed (square cold, circle warm, triangle occl.); "
                 f"Met Office cyan; ours red/blue/purple (faint = no DWD match); METAR passages C/W")
    out = cases / "plots" / f"{tname}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=55, bbox_inches="tight")
    plt.close(fig)


# ------------------------------------------------------------------------- show

def _pct(x):
    return "  -  " if x is None else f"{x:5.0%}"


def show(res: dict, ref: str = "dwd") -> None:
    print(f"{res['analysis_time']}  Hewson {res['init']} +{res['hour']}h  references: " +
          ", ".join(f"{s} {v['cells']}" for s, v in res["references"].items()) +
          (f"  missing models: {res['missing_models']}" if res["missing_models"] else ""))
    print(f"\n[{ref}] {'model':6} {'lvl':4} {'variant':11} {'bound':>6} {'false':>6} {'type':>6} {'n':>4}  "
          f"offset km (+cold)        missed")
    for r in res["rows"]:
        if r["reference"] != ref:
            continue
        off = " ".join(f"{k[0]}{v:+.0f}" for k, v in r["offset_km"].items())
        miss = dict(r["missed"])
        if "missed_found_other_level" in r:
            miss["other_level"] = r["missed_found_other_level"]
        print(f"      {r['model']:6} {r['level']:<4} {r['variant']:11} {_pct(r['boundary']):>6} {_pct(r['false']):>6} "
              f"{_pct(r['type']):>6} {r['type_n']:4d}  {off:24} {miss}")
    flips = [f for f in res["flips"] if f["reference"] == ref and f["variant"] == "default"]
    if flips:
        c = collections.Counter((f["model"], f["level"], f["drawn"], f["ours"], f["junction"]) for f in flips)
        print(f"\n[{ref}] default-gate type flips (model, level, drawn>ours, at junction): count")
        for k, v in sorted(c.items()):
            ll = [f for f in flips if (f["model"], f["level"], f["drawn"], f["ours"], f["junction"]) == k]
            lat = [f["lat"] for f in ll]; lon = [f["lon"] for f in ll]
            print(f"      {k[0]} {k[1]} {k[2]}>{k[3]} {'junction' if k[4] else 'clear   '} {v:3d}  "
                  f"lat {min(lat):.1f}–{max(lat):.1f} lon {min(lon):.1f}–{max(lon):.1f}  "
                  f"|adv| med {np.median([f['abs_advection'] for f in ll]):.1f} K/h")
    if res.get("passages") is not None:
        print(f"\nMETAR passages within ±1.5 h: {len(res['passages'])} (from {res.get('metar_rows', 0)} reports)")
        for ev in res["passages"]:
            near = "  ".join(f"{m}:" + "/".join(
                f"{ev[f'{m}_{L}']['km']}" if f"{m}_{L}" in ev else "-" for L in LEVELS) for m in MODELS)
            print(f"      {ev['icao']} {ev['kind']:4} {ev['dt_h']:+.1f}h veer {ev['veer']}° dT {ev['dT']:+} "
                  f"dTd {ev['dTd']:+}  DWD {ev.get('dwd_km', '-')} km ({ev.get('dwd_kind', '-')}), "
                  f"MO {ev.get('mo_km', '-')} km  ours km 925/850/700 {near}")


# ------------------------------------------------------------------ record/tally

def cmd_record(a) -> None:
    res = json.loads((Path(a.cases) / "scores" / f"{a.t}.json").read_text())
    rec = Path(a.record)
    rec.parent.mkdir(parents=True, exist_ok=True)
    old = [json.loads(line) for line in rec.open()] if rec.exists() else []
    if a.notes:
        notes = json.loads(Path(a.notes).read_text())
    else:                                  # --replace without notes: keep the recorded ones
        prev = [r for r in old if r["analysis_time"] == a.t and r["rec"] == "notes"]
        if not (a.replace and prev):
            raise SystemExit("notes file required (only --replace of a recorded time can reuse its notes)")
        notes = {k: v for k, v in prev[-1].items() if k not in ("rec", "analysis_time", "recorded_at")}
    for k in ("regime", "dwd_mo_agree", "extraction_ok"):
        if k not in notes:
            raise SystemExit(f"notes need {k!r} (see SKILL.md step 4)")
    if any(r["analysis_time"] == a.t for r in old):
        if not a.replace:
            raise SystemExit(f"{a.t} already recorded (--replace to redo it)")
        old = [r for r in old if r["analysis_time"] != a.t]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    new = [{**notes, "rec": "notes", "analysis_time": a.t, "recorded_at": stamp}]
    for r in res["rows"]:
        new.append({**r, "rec": "score", "analysis_time": a.t})
    for f in res["flips"]:
        new.append({**f, "rec": "flip", "analysis_time": a.t})
    for ev in res["passages"]:
        new.append({**ev, "rec": "passage", "analysis_time": a.t})
    if "passage_baseline" in res:
        new.append({**res["passage_baseline"], "rec": "passage_baseline", "analysis_time": a.t})
    with rec.open("w") as fh:
        for r in old + new:
            fh.write(json.dumps(r, default=float) + "\n")
    print(f"recorded {a.t}: {len(new)} rows -> {rec}")


def cmd_tally(a) -> None:
    rows = [json.loads(line) for line in Path(a.record).open()]
    if a.since:
        rows = [r for r in rows if r["analysis_time"] >= a.since]
    notes = {r["analysis_time"]: r for r in rows if r["rec"] == "notes"}
    excluded = {t for t, n in notes.items() if n.get("exclude") or not n.get("extraction_ok", True)}
    scores = [r for r in rows if r["rec"] == "score" and r["reference"] == a.ref
              and r["analysis_time"] not in excluded]
    times = sorted({r["analysis_time"] for r in scores})
    print(f"{len(times)} analysis times vs {a.ref} ({len(excluded)} excluded: {sorted(excluded)})")
    if a.ref == "dwd":
        disagree = [t for t in times if notes.get(t, {}).get("dwd_mo_agree") == "no"]
        if disagree:
            print(f"  DWD and Met Office disagree on {len(disagree)}: {disagree} -- read those with care")

    agg = collections.defaultdict(lambda: collections.Counter())
    for r in scores:
        k = (r["model"], r["level"], r["variant"])
        c = agg[k]
        c["ref"] += r["ref_cells"]; c["hit"] += round((r["boundary"] or 0) * r["ref_cells"])
        c["our"] += r["our_cells"]; c["false"] += round((r["false"] or 0) * r["our_cells"])
        c["type_n"] += r["type_n"]; c["type_ok"] += round((r["type"] or 0) * r["type_n"])
        for why, n in r["missed"].items():
            c["m:" + why] += n
        c["m:other_level"] += r.get("missed_found_other_level", 0)
    print(f"\n{'model':6} {'lvl':4} {'variant':11} {'bound':>6} {'false':>6} {'type':>6}  missed: share by cause")
    for k in sorted(agg):
        c = agg[k]
        missed = sum(v for kk, v in c.items() if kk.startswith("m:") and kk != "m:other_level")
        causes = " ".join(f"{kk[2:]} {v / missed:.0%}" for kk, v in sorted(c.items(), key=lambda x: -x[1])
                          if kk.startswith("m:") and kk != "m:other_level" and v) if missed else ""
        other = f" (other level {c['m:other_level'] / missed:.0%})" if k[2] == "default" and missed else ""
        print(f"{k[0]:6} {k[1]:<4} {k[2]:11} {c['hit'] / max(1, c['ref']):6.0%} {c['false'] / max(1, c['our']):6.0%} "
              f"{(c['type_ok'] / c['type_n']) if c['type_n'] else float('nan'):6.0%}  {causes}{other}")

    # offsets by level and kind (default), the measured slope
    offs = collections.defaultdict(list)
    for r in scores:
        if r["variant"] == "default":
            for kind, v in r["offset_km"].items():
                offs[(r["level"], kind)].append(v)
    print("\nmedian signed offset, default gate, km toward the cold air (expected in brackets):")
    for (level, kind), v in sorted(offs.items()):
        print(f"  {level} {kind:9} {np.median(v):+5.0f}  [{SLOPE_SHIFT_KM[level][kind]:+d}]  n={len(v)} times")

    # best level per time and model (default), with the regime note
    print("\nbest level per analysis time (default gate, boundary):")
    for t in times:
        bits = []
        for m in MODELS:
            rr = [r for r in scores if r["analysis_time"] == t and r["model"] == m and r["variant"] == "default"]
            if rr:
                b = max(rr, key=lambda r: r["boundary"] or 0)
                bits.append(f"{m} {b['level']} ({(b['boundary'] or 0):.0%})")
        print(f"  {t}  {'  '.join(bits)}  — {notes.get(t, {}).get('regime', '')}")

    flips = [r for r in rows if r["rec"] == "flip" and r["reference"] == a.ref and r["variant"] == "default"
             and r["analysis_time"] not in excluded]
    if flips:
        c = collections.Counter((f["drawn"], f["ours"], "junction" if f["junction"] else "clear") for f in flips)
        print("\ndefault-gate type flips by kind: " + ", ".join(f"{a_}>{b_} {j} {n}" for (a_, b_, j), n in c.most_common()))
        hot = collections.Counter((round(f["lat"]), round(f["lon"])) for f in flips if not f["junction"])
        print("  places (1°, away from junctions): " + ", ".join(f"{la}N {lo}E ×{n}" for (la, lo), n in hot.most_common(8)))

    ps = [r for r in rows if r["rec"] == "passage" and r["analysis_time"] not in excluded]
    if ps:
        print(f"\nMETAR passages: {len(ps)}; median distance to the drawn front: DWD "
              f"{np.median([p['dwd_km'] for p in ps if 'dwd_km' in p]):.0f} km, MO "
              f"{np.median([p['mo_km'] for p in ps if 'mo_km' in p] or [np.nan]):.0f} km")
        for m in MODELS:
            line = []
            for L in LEVELS:
                v = [p[f"{m}_{L}"]["km"] for p in ps if f"{m}_{L}" in p]
                line.append(f"{L} {np.median(v):.0f} km ({np.mean(np.array(v) <= MATCH_KM):.0%} ≤150)" if v else f"{L} -")
            print(f"  {m:6} nearest line: " + "  ".join(line))
        _tally_lift(ps, {r["analysis_time"]: r for r in rows if r["rec"] == "passage_baseline"})


def _passage_km(p: dict, k: str):
    if k in ("dwd", "mo"):
        return p.get(f"{k}_km"), p.get(f"{k}_kind")
    v = p.get(k)
    return (v["km"], v["kind"]) if v else (None, None)


def _tally_lift(ps: list[dict], base: dict) -> None:
    """Passages near each set of lines against the station baseline of their time.

    hit = share of passages within MATCH_KM; base = expected share if passages fell
    on stations at random (mean of the per-time station shares); lift = hit / base;
    z = (hits - expected) / sqrt(sum p(1-p)), overstated because stations crossed by
    one front are not independent; type = our label matches the passage, of the hits
    labelled cold or warm.
    """
    have = [p for p in ps if p["analysis_time"] in base]
    if not have:
        print("  (no station baseline recorded yet: score-all --rescore, then record --replace)")
        return
    missing = sorted({p["analysis_time"] for p in ps} - set(base))
    keys = ["dwd", "mo"] + [f"{m}_{L}" for m in MODELS for L in LEVELS]

    def block(title, sel, ks):
        print(f"\n  {title}")
        print(f"  {'set':10} {'n':>4} {'hit':>5} {'base':>5} {'lift':>5} {'z':>5}  type")
        for k in ks:
            n = h = 0
            e = v = 0.0
            tok = tn = 0
            for p in sel:
                b = base[p["analysis_time"]]["near"].get(k)
                km, kind = _passage_km(p, k)
                if b is None or km is None:
                    continue
                n += 1; e += b; v += b * (1 - b)
                if km <= MATCH_KM:
                    h += 1
                    if kind in ("cold", "warm"):
                        tn += 1; tok += kind == p["kind"]
            if n:
                z = (h - e) / np.sqrt(v) if v else float("nan")
                lift = h / e if e else float("nan")
                print(f"  {k:10} {n:4} {h / n:5.0%} {e / n:5.0%} {lift:5.2f} {z:5.1f}  {tok}/{tn}")

    stations = np.median([b["stations"] for b in base.values()])
    print(f"\nMETAR passage lift vs a random reporting station (≤{MATCH_KM:.0f} km; ~{stations:.0f} stations a time"
          + (f"; no baseline for {missing}" if missing else "") + "):")
    block("all passages", have, keys)
    for kind in ("cold", "warm"):
        block(f"{kind} passages", [p for p in have if p["kind"] == kind], keys)
    block("passages away from every DWD front (does anyone else explain them?)",
          [p for p in have if (p.get("dwd_km") or 0) > MATCH_KM], keys[1:])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("score"); p.add_argument("cases"); p.add_argument("t"); p.add_argument("--plot", action="store_true")
    p = sub.add_parser("score-all"); p.add_argument("cases"); p.add_argument("--rescore", action="store_true")
    p.add_argument("--plot", action="store_true")
    p = sub.add_parser("show"); p.add_argument("cases"); p.add_argument("t"); p.add_argument("--ref", default="dwd")
    p = sub.add_parser("record"); p.add_argument("record"); p.add_argument("cases"); p.add_argument("t")
    p.add_argument("notes", nargs="?", help="notes JSON; omit with --replace to keep the recorded notes")
    p.add_argument("--replace", action="store_true")
    p = sub.add_parser("tally"); p.add_argument("record"); p.add_argument("--since"); p.add_argument("--ref", default="dwd")
    a = ap.parse_args(argv)
    if a.cmd == "score":
        show(score_time(Path(a.cases), a.t, plot=a.plot))
    elif a.cmd == "score-all":
        cases = Path(a.cases)
        names = sorted({d.name for s in ("dwd", "mo") for d in (cases / "charts" / s).glob("*")})
        for n in names:
            if a.rescore or not (cases / "scores" / f"{n}.json").exists():
                r = score_time(cases, n, plot=a.plot)
                print(f"scored {n}: {len(r['rows'])} rows, {len(r['passages'])} passages", file=sys.stderr)
    elif a.cmd == "show":
        show(json.loads((Path(a.cases) / "scores" / f"{a.t}.json").read_text()), a.ref)
    elif a.cmd == "record":
        cmd_record(a)
    elif a.cmd == "tally":
        cmd_tally(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
