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
    # The sampling the model's decoders use when they don't ask for one
    # (``IconVariant.native_sampling``, set when the grid is fetched, #974).
    default_method: str = "barycentric"
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
            np.savez(
                tmp, uuid=np.array(self.uuid), clat=self.clat, clon=self.clon,
                default_method=np.array(self.default_method),
            )
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: Path) -> "IconNativeGrid":
        with np.load(Path(path)) as npz:
            method = str(npz["default_method"]) if "default_method" in npz else "barycentric"
            return cls(str(npz["uuid"]), npz["clat"], npz["clon"], default_method=method)

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

    def cells_within(self, lat: float, lon: float, radius_km: float) -> np.ndarray:
        """Indices of the cells whose centre lies within ``radius_km``."""
        xyz = _unit_vectors(np.array([lat]), np.array([lon]))[0]
        chord = 2.0 * np.sin(min(np.pi, radius_km / _EARTH_RADIUS_KM) / 2.0)
        return np.asarray(self._tree.query_ball_point(xyz, chord), dtype=np.intp)

    def buffer_inside(self, lat: float, lon: float, radius_km: float, n: int = 16) -> bool:
        """True when a circle of ``radius_km`` round the point is inside the grid.

        The native counterpart of the lat-lon corridor window test (#462): a
        corridor that runs off the grid's edge is unavailable, not a smaller
        corridor. Checks the centre and ``n`` points on the circle.
        """
        ang = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
        d = radius_km / _EARTH_RADIUS_KM
        lat0, lon0 = np.radians(lat), np.radians(lon)
        plat = np.arcsin(np.sin(lat0) * np.cos(d) + np.cos(lat0) * np.sin(d) * np.cos(ang))
        plon = lon0 + np.arctan2(
            np.sin(ang) * np.sin(d) * np.cos(lat0),
            np.cos(d) - np.sin(lat0) * np.sin(plat),
        )
        lats = np.concatenate([[lat], np.degrees(plat)])
        lons = np.concatenate([[lon], np.degrees(plon)])
        return bool(self.contains(lats, lons).all())

    def weights(self, lats, lons, method: str | None = None) -> NativeWeights:
        method = method or self.default_method
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


def default_cache_root() -> Path:
    """Where decoders look for grids: ``$DATA_DIR/.cache/grib``.

    Decode-pool workers get no ``data_dir``; the fetch side writes grids under
    the same ``data_dir`` the app runs with, which is ``DATA_DIR``.
    """
    import os

    return Path(os.environ.get("DATA_DIR", "data")) / ".cache" / "grib"


def uuid_from_grib_header(head: bytes) -> str | None:
    """``uuidOfHGrid`` from the first bytes of a GRIB2 message, or None.

    Walks the sections of a possibly truncated message (a Range request for
    the first ~2 KB) to section 3 and reads template 101's UUID, so a run's
    grid can be identified without downloading the whole CLAT file.
    """
    if len(head) < 16 or head[:4] != b"GRIB" or head[7] != 2:
        return None
    off = 16
    while off + 5 <= len(head):
        length = int.from_bytes(head[off:off + 4], "big")
        number = head[off + 4]
        if length < 5:
            return None
        if number == 3:
            if off + 35 > len(head):
                return None
            template = int.from_bytes(head[off + 12:off + 14], "big")
            if template != 101:
                return None
            # Template 3.101: octet 15 earth shape, 16-18 grid number,
            # 19 grid-in-reference, 20-35 the UUID (1-based octets).
            return head[off + 19:off + 35].hex()
        if number > 3:
            return None
        off += length
    return None


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


# UUIDs already reported missing in this process: one error per grid, not
# one warning per message (a missing grid blanks every ICON field).
_MISSING_REPORTED: set[str] = set()


def find_grid(uuid: str, source=None) -> IconNativeGrid | None:
    """The grid for a decoder: in-process, next to *source*, else under
    :func:`default_cache_root`.

    The fetch side saves grids under the ``data_dir`` it was given, which can
    differ from ``$DATA_DIR`` (a CLI ``--data-dir``, tests). A cached GRIB file
    lives at ``<data_dir>/.cache/grib/<model>/<run>/<file>``, so the grid is
    looked for in the file's ancestors first. None (one error per UUID per
    process) when no root has it.
    """
    grid = get_grid(uuid)
    if grid is not None:
        return grid
    roots: list[Path] = []
    if isinstance(source, (str, Path)):
        path = Path(source)
        roots.extend(([path] if path.is_dir() else []) + list(path.parents)[:4])
    roots.append(default_cache_root())
    for root in roots:
        try:
            if not grid_cache_path(root, uuid).exists():
                continue
        except ValueError:
            return None
        grid = get_grid(uuid, cache_root=root)
        if grid is not None:
            return grid
    if uuid not in _MISSING_REPORTED:
        _MISSING_REPORTED.add(uuid)
        logger.error(
            "ICON native grid %s not found (looked in %s): every field on it "
            "is skipped until the fetch side saves it",
            uuid, ", ".join(str(r) for r in roots),
        )
    return None


def clear_registry() -> None:
    """Forget every in-process grid (tests)."""
    with _LOCK:
        _GRIDS.clear()
