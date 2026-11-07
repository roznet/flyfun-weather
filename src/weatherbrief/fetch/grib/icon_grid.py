"""ICON native (triangular, icosahedral) grid sampling (#974).

DWD stops publishing ICON-EU and ICON-D2 on regular lat-lon grids after
2026-11-30; the ``weather/nwp/v1`` feed carries the native grid only. A native
GRIB message (grid definition template 101, ``gridType=unstructured_grid``)
holds no coordinates at all: just a 1-D array of cell values and the grid's
UUID (``uuidOfHGrid``). The cell centres come separately, as the ``CLAT`` /
``CLON`` fields of the same feed (or, for MeteoSwiss ICON-CH, the
horizontal-constants file), so nothing here is DWD-specific.

:class:`IconNativeGrid` holds one grid's cell centres and answers the two
questions every decoder asks:

* **weights** for a list of target points, computed once per grid and reused
  for every message on it (the native counterpart of
  ``decode._bilinear_grid_weights``). Two methods:

  - ``barycentric`` (default, owner call on #974): the target's position
    inside the smallest triangle of nearby cell centres that contains it, so
    the value varies linearly between centres like the bilinear lat-lon path
    did and replay diffs stay small.
  - ``nearest``: the cell the target lies in (closest centre). For
    point-sample uses: the Hewson lattice and terrain-matched airport cells
    (#924).

* **footprint**: whether a target is inside the grid at all. A target farther
  than :data:`FOOTPRINT_SPACINGS` typical cell spacings from every centre is
  outside; its weights are not produced (``inb_idx`` leaves it out), which is
  the same out-of-bounds contract as the lat-lon weights. This replaces the
  hard-coded domain bboxes, which never matched D2's non-rectangular domain.

Masked cells (D2 carries a bitmap) are NaN in the unpacked values; the gather
rule in :func:`gather_native` is the bilinear one: a NaN with a non-zero
weight blanks the result, a NaN with zero weight does not.

Grids are cached in-process by UUID (:func:`register_grid` / :func:`get_grid`)
and on disk as ``icon-grid-<uuid>.npz`` (:meth:`IconNativeGrid.save` /
:meth:`IconNativeGrid.load`), so the centres (~1.5 MB of CLAT/CLON GRIB to
download, ~12 MB as float64 on disk) are fetched once per grid, not once per
run.
"""

from __future__ import annotations

import itertools
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

import numpy as np

logger = logging.getLogger(__name__)

# A target farther than this many typical cell spacings from the nearest cell
# centre is outside the grid. 1.5 keeps every point of a cell (a triangle's
# corner is ~0.6 spacings from its centre) and adds half a cell of margin at
# the domain edge, about what the lat-lon bilinear stencil reached.
FOOTPRINT_SPACINGS = 1.5

# Nearest centres searched for an enclosing triangle. The enclosing triangle
# of centres is formed from the 3-4 nearest almost always; 8 leaves room for
# skewed cells near pentagon points and domain edges.
_BARY_K = 8

# Barycentric coordinates may dip this far below zero and still count as
# "inside": targets exactly on a triangle edge must not fall through on
# floating-point noise.
_BARY_EPS = 1e-9

_EARTH_RADIUS_KM = 6371.0


class NativeWeights(NamedTuple):
    """Gather indices + weights for target points on a native grid.

    ``idx`` and ``w`` are ``(n_inb, k)``: the cells each in-bounds target
    reads and their weights (rows sum to 1; k is 3 for barycentric, 1 for
    nearest). ``inb_idx`` indexes the original target list, like
    ``decode._GridWeights.inb_idx``.
    """

    idx: np.ndarray
    w: np.ndarray
    inb_idx: np.ndarray


def _unit_vectors(lat_deg: np.ndarray, lon_deg: np.ndarray) -> np.ndarray:
    lat = np.radians(np.asarray(lat_deg, dtype=np.float64))
    lon = np.radians(np.asarray(lon_deg, dtype=np.float64))
    cl = np.cos(lat)
    return np.stack([cl * np.cos(lon), cl * np.sin(lon), np.sin(lat)], axis=-1)


def _to_degrees(clat: np.ndarray, clon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Cell centres in degrees, whatever unit the source used.

    DWD's CLAT/CLON GRIB fields are degrees; ICON grid files (and some
    MeteoSwiss constants) are radians. Radians never exceed pi in magnitude,
    while any European grid has latitudes above ~30 degrees.
    """
    if np.nanmax(np.abs(clat)) <= np.pi / 2 + 1e-6 and np.nanmax(np.abs(clon)) <= np.pi + 1e-6:
        return np.degrees(clat), np.degrees(clon)
    return clat, clon


@dataclass
class IconNativeGrid:
    """Cell centres of one ICON grid, with a spatial index."""

    uuid: str
    clat: np.ndarray  # degrees, float64, one per cell
    clon: np.ndarray  # degrees, float64, one per cell
    _tree: object = field(init=False, repr=False)
    _xyz: np.ndarray = field(init=False, repr=False)
    spacing_rad: float = field(init=False)

    def __post_init__(self) -> None:
        from scipy.spatial import cKDTree

        clat = np.asarray(self.clat, dtype=np.float64).ravel()
        clon = np.asarray(self.clon, dtype=np.float64).ravel()
        if clat.shape != clon.shape or clat.size < 3:
            raise ValueError(
                f"ICON grid {self.uuid}: need matching CLAT/CLON with >= 3 cells, "
                f"got {clat.size} and {clon.size}"
            )
        if not (np.isfinite(clat).all() and np.isfinite(clon).all()):
            # NaN centres would silently poison the KD-tree (and the unit guess).
            raise ValueError(f"ICON grid {self.uuid}: non-finite CLAT/CLON")
        clat, clon = _to_degrees(clat, clon)
        self.clat, self.clon = clat, clon
        self._xyz = _unit_vectors(clat, clon)
        self._tree = cKDTree(self._xyz)
        # Typical spacing: median distance from a cell centre to its nearest
        # neighbour, on a deterministic sample (the full query on 770k cells
        # costs ~1 s for no gain).
        step = max(1, clat.size // 20000)
        sample = self._xyz[::step]
        d, _ = self._tree.query(sample, k=2)
        chord = float(np.median(d[:, 1]))
        self.spacing_rad = 2.0 * float(np.arcsin(min(1.0, chord / 2.0)))

    @property
    def n_cells(self) -> int:
        return int(self.clat.size)

    @property
    def spacing_km(self) -> float:
        return self.spacing_rad * _EARTH_RADIUS_KM

    # -- persistence ------------------------------------------------------

    def save(self, path: Path) -> None:
        """Write the centres as ``npz`` (atomically: temp file, then rename).

        The temp name is unique per writer, so two workers building the same
        grid at once never write into one file; the last rename wins and both
        wrote identical content.
        """
        import os
        import uuid as _uuid

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{_uuid.uuid4().hex[:8]}.tmp.npz")
        try:
            np.savez(tmp, uuid=np.array(self.uuid), clat=self.clat, clon=self.clon)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: Path) -> "IconNativeGrid":
        with np.load(Path(path)) as npz:
            return cls(str(npz["uuid"]), npz["clat"], npz["clon"])

    @classmethod
    def from_grib(cls, clat_source, clon_source) -> "IconNativeGrid":
        """Build from the feed's CLAT and CLON GRIB files (path or bytes).

        Both must be native-grid messages on the same UUID; anything else is a
        feed change we want to hear about, so it raises.
        """
        from weatherbrief.fetch.grib.grib_reader import iter_messages

        def _read(source, what: str) -> tuple[str, np.ndarray]:
            for msg in iter_messages(source):
                if not msg.is_native:
                    raise ValueError(f"{what}: not a native-grid message ({msg.grid_type})")
                if not msg.uuid:
                    raise ValueError(f"{what}: native-grid message without uuidOfHGrid")
                values = msg.native_values()
                if values is None:
                    raise ValueError(f"{what}: could not unpack values")
                return msg.uuid, values.astype(np.float64)
            raise ValueError(f"{what}: no GRIB message")

        uuid_lat, clat = _read(clat_source, "CLAT")
        uuid_lon, clon = _read(clon_source, "CLON")
        if uuid_lat != uuid_lon:
            raise ValueError(f"CLAT grid {uuid_lat} != CLON grid {uuid_lon}")
        return cls(uuid_lat, clat, clon)

    # -- queries ----------------------------------------------------------

    def _query(self, lats, lons, k: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        xyz = _unit_vectors(np.atleast_1d(lats), np.atleast_1d(lons))
        dist, idx = self._tree.query(xyz, k=k)
        if k == 1:
            dist, idx = dist[:, None], idx[:, None]
        return xyz, dist, idx

    def _inside(self, nearest_chord: np.ndarray) -> np.ndarray:
        limit = 2.0 * np.sin(min(np.pi / 2, FOOTPRINT_SPACINGS * self.spacing_rad) / 2.0)
        return nearest_chord <= limit

    def contains(self, lats, lons) -> np.ndarray:
        """Boolean per target: inside the grid's footprint."""
        _, dist, _ = self._query(lats, lons, 1)
        return self._inside(dist[:, 0])

    def nearest_weights(self, lats, lons) -> NativeWeights:
        """One cell per in-bounds target: the closest centre."""
        _, dist, idx = self._query(lats, lons, 1)
        inb = self._inside(dist[:, 0])
        inb_idx = np.flatnonzero(inb)
        sel = idx[inb].astype(np.intp)
        return NativeWeights(sel, np.ones(sel.shape, dtype=np.float64), inb_idx)

    def barycentric_weights(self, lats, lons) -> NativeWeights:
        """Three cells per in-bounds target, linear across their triangle.

        For each target, the k nearest centres are projected onto the plane
        tangent to the sphere at the target (gnomonic, so great-circle
        segments stay straight), and the first triangle of them that contains
        the target is used, trying triangles in order of how near their
        corners are. A target that no triangle contains (it sits between the
        outermost centres at the domain edge, still inside the footprint)
        falls back to inverse-distance weights over its three nearest centres.
        """
        n_cells = self.n_cells
        k = min(_BARY_K, n_cells)
        xyz, dist, idx = self._query(lats, lons, k)
        inb = self._inside(dist[:, 0])
        inb_idx = np.flatnonzero(inb)
        xyz, dist, idx = xyz[inb], dist[inb], idx[inb]
        m = xyz.shape[0]
        out_idx = np.zeros((m, 3), dtype=np.intp)
        out_w = np.zeros((m, 3), dtype=np.float64)
        if m == 0:
            return NativeWeights(out_idx, out_w, inb_idx)

        # Local tangent basis at each target: east, north.
        z = np.array([0.0, 0.0, 1.0])
        east = np.cross(z, xyz)
        norm = np.linalg.norm(east, axis=1, keepdims=True)
        # A target at a pole has no east; any perpendicular will do.
        polar = norm[:, 0] < 1e-12
        if np.any(polar):
            east[polar] = np.array([1.0, 0.0, 0.0])
            norm[polar] = 1.0
        east /= norm
        north = np.cross(xyz, east)

        nb = self._xyz[idx]  # (m, k, 3)
        dot = np.einsum("mkc,mc->mk", nb, xyz)
        dot = np.where(dot <= 1e-12, 1e-12, dot)
        px = np.einsum("mkc,mc->mk", nb, east) / dot
        py = np.einsum("mkc,mc->mk", nb, north) / dot

        done = np.zeros(m, dtype=bool)
        combos = sorted(itertools.combinations(range(k), 3), key=lambda c: (max(c), sum(c)))
        for a, b, c in combos:
            todo = ~done
            if not np.any(todo):
                break
            xa, ya = px[todo, a], py[todo, a]
            xb, yb = px[todo, b], py[todo, b]
            xc, yc = px[todo, c], py[todo, c]
            det = (yb - yc) * (xa - xc) + (xc - xb) * (ya - yc)
            ok_det = np.abs(det) > 1e-18
            safe = np.where(ok_det, det, 1.0)
            # Target is the origin of the tangent plane.
            l1 = ((yb - yc) * (0.0 - xc) + (xc - xb) * (0.0 - yc)) / safe
            l2 = ((yc - ya) * (0.0 - xc) + (xa - xc) * (0.0 - yc)) / safe
            l3 = 1.0 - l1 - l2
            hit = ok_det & (l1 >= -_BARY_EPS) & (l2 >= -_BARY_EPS) & (l3 >= -_BARY_EPS)
            if not np.any(hit):
                continue
            rows = np.flatnonzero(todo)[hit]
            out_idx[rows] = idx[rows][:, [a, b, c]]
            w = np.stack([l1[hit], l2[hit], l3[hit]], axis=1)
            w = np.clip(w, 0.0, None)
            out_w[rows] = w / w.sum(axis=1, keepdims=True)
            done[rows] = True

        if not np.all(done):
            rows = np.flatnonzero(~done)
            d3 = np.maximum(dist[rows, :3], 1e-15)
            inv = 1.0 / d3
            out_idx[rows] = idx[rows, :3]
            out_w[rows] = inv / inv.sum(axis=1, keepdims=True)
            logger.debug(
                "ICON grid %s: %d/%d targets outside every centre triangle, "
                "inverse-distance fallback", self.uuid, rows.size, m,
            )
        return NativeWeights(out_idx, out_w, inb_idx)

    def weights(self, lats, lons, method: str = "barycentric") -> NativeWeights:
        if method == "barycentric":
            return self.barycentric_weights(lats, lons)
        if method == "nearest":
            return self.nearest_weights(lats, lons)
        raise ValueError(f"unknown ICON sampling method {method!r}")


def gather_native(values: np.ndarray, nw: NativeWeights) -> np.ndarray:
    """Weighted gather of ``values[..., cell]`` at the cells in ``nw``.

    Returns float64 of shape ``values.shape[:-1] + (len(nw.inb_idx),)``.
    Same edge rule as ``grib_reader.gather_bilinear``: a zero-weight cell
    never blanks the result, a NaN cell with non-zero weight does.
    """
    out = None
    for j in range(nw.idx.shape[1]):
        w = nw.w[:, j]
        term = w * values[..., nw.idx[:, j]].astype(np.float64)
        term = np.where(w == 0.0, 0.0, term)
        out = term if out is None else out + term
    if out is None:
        return np.zeros(values.shape[:-1] + (0,), dtype=np.float64)
    return out


# -- process-wide registry --------------------------------------------------

_UUID_RE = re.compile(r"[0-9a-fA-F-]{8,64}")

_GRIDS: dict[str, IconNativeGrid] = {}
_LOCK = threading.Lock()


def grid_cache_path(cache_root: Path, uuid: str) -> Path:
    """Where a grid's centres are kept on disk, beside the GRIB cache.

    Kept forever, never pruned: one ~12 MB file per grid (ICON-EU, ICON-D2),
    and a grid only changes when DWD changes the model's resolution or domain.
    The UUID comes from a GRIB header, so it is checked to be hex (dashes
    allowed) before it becomes part of a path.
    """
    if not uuid or not _UUID_RE.fullmatch(uuid):
        raise ValueError(f"not an ICON grid UUID: {uuid!r}")
    return Path(cache_root) / f"icon-grid-{uuid}.npz"


def register_grid(grid: IconNativeGrid) -> IconNativeGrid:
    with _LOCK:
        return _GRIDS.setdefault(grid.uuid, grid)


def get_grid(uuid: str, cache_root: Path | None = None) -> IconNativeGrid | None:
    """The grid for *uuid*: in-process, else from ``cache_root`` on disk."""
    with _LOCK:
        grid = _GRIDS.get(uuid)
    if grid is not None or cache_root is None:
        return grid
    try:
        path = grid_cache_path(cache_root, uuid)
    except ValueError:
        logger.warning("Ignoring malformed ICON grid UUID %r", uuid)
        return None
    if not path.exists():
        return None
    try:
        grid = IconNativeGrid.load(path)
    except Exception:
        logger.warning("Unreadable ICON grid cache %s", path, exc_info=True)
        return None
    if grid.uuid != uuid:
        logger.warning("ICON grid cache %s holds %s, not %s", path, grid.uuid, uuid)
        return None
    return register_grid(grid)


def clear_registry() -> None:
    """Forget every in-process grid (tests)."""
    with _LOCK:
        _GRIDS.clear()
