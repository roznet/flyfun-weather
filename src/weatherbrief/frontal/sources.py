"""``HewsonFieldSource`` — one detection algorithm, swappable data source.

Front detection needs, per ``(model, level, hour)``, the Hewson diagnostic
grids (θe, |∇θe|, TFP, −∇²θe, advection, tendency) plus the gradient direction
(``dT_dx``/``dT_dy``) and a time-mean background gradient for the anomaly
filter. Two places can supply those:

* :class:`SnapshotFieldSource` — reads a precomputed NPZ snapshot
  (``weatherbrief.hewson.precompute`` output). **Production default.** Zero
  fetch; the derivative fields were computed once on the full European grid
  (cleaner 2nd derivatives than a route-corridor recompute); the 3 h temporal
  stride is interpolated between frames by the route sampler. ``dT_dx``/
  ``dT_dy`` are re-derived from the stored θe (the NPZ doesn't persist them).

* :class:`CaseFieldSource` — recomputes diagnostics from a calibration
  :class:`~weatherbrief.frontal.case.Case` via
  :func:`~weatherbrief.frontal.detect.compute_hewson_diagnostics` (today's
  path). For calibration, ERA5 historical studies, arbitrary-level/hourly work,
  and as a fallback when no snapshot covers the route.

The choice is made in code, **not** exposed as a user toggle — see
``designs/future/hewson-fields-aviation-advisories.md`` §6.2/§6.3 (source-
agnostic principle). A future native-GRIB stencil source slots in as a third
implementation with no change to detection or gates.

Hour reference frame: each source defines its own. ``CaseFieldSource`` hours are
offsets from the case's first valid time (unchanged from the historical Case
path); ``SnapshotFieldSource`` hours are offsets from the snapshot's model init.
Callers pass hours in the source's frame and stay internally consistent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from weatherbrief.frontal.detect import (
    compute_hewson_diagnostics,
    theta_e_gradient_components,
)


@dataclass(frozen=True)
class HewsonGrids:
    """The diagnostic grids at one ``(model, level, integer hour)``.

    All arrays share the source grid shape ``(n_lat, n_lon)``. ``dT_dx``/
    ``dT_dy`` are in K/km (the unit gradient direction the front extractor
    needs); every other field is in the unit the precompute NPZ stores
    (gradient K/100 km, tfp & neg_laplacian K/(100 km)², advection & tendency
    K/h). ``tendency`` may be all-NaN when neighbours aren't available.
    """

    theta_e: np.ndarray
    gradient: np.ndarray
    tfp: np.ndarray
    neg_laplacian: np.ndarray
    advection: np.ndarray
    tendency: np.ndarray
    dT_dx: np.ndarray
    dT_dy: np.ndarray


class HewsonFieldSource(ABC):
    """Abstract "give me Hewson grids at (model, level, hour)" provider."""

    # Subclasses set these in __init__.
    lat: np.ndarray
    lon: np.ndarray
    terrain_mask: np.ndarray | None

    @property
    @abstractmethod
    def models(self) -> list[str]:
        """Model keys this source can answer for."""

    @abstractmethod
    def available_hours(self, model: str) -> list[int]:
        """Sorted integer hour offsets available for ``model`` (source frame)."""

    @abstractmethod
    def available_levels(self, model: str) -> list[int]:
        """Pressure levels (hPa) present for ``model``."""

    @abstractmethod
    def grids_at_hour(
        self, model: str, hour: int, level_hPa: int | None = None,
    ) -> HewsonGrids | None:
        """Diagnostic grids at one integer ``hour``, or ``None`` if unavailable.

        ``hour`` must be a member of :meth:`available_hours`.
        """

    def gradient_at_hour(
        self, model: str, hour: int, level_hPa: int | None = None,
    ) -> np.ndarray | None:
        """Just the |∇θe| grid at ``hour`` — cheaper than the full grids.

        Default pulls it out of :meth:`grids_at_hour`; sources override to avoid
        computing the other diagnostics (the background-gradient mean only needs
        this field, and computing tendency there would fetch neighbour hours).
        """
        grids = self.grids_at_hour(model, hour, level_hPa)
        return None if grids is None else grids.gradient

    # ------------------------------------------------------------------
    # Shared helpers (default implementations)

    def resolve_level(self, model: str, level_hPa: int | None) -> int:
        """Default level is 850 hPa if present, else the lowest available."""
        avail = self.available_levels(model)
        if level_hPa is None:
            return 850 if 850 in avail else avail[0]
        if level_hPa not in avail:
            raise ValueError(
                f"level {level_hPa} hPa not in {avail} for model {model!r}"
            )
        return level_hPa

    def background_gradient(
        self,
        model: str,
        level_hPa: int | None = None,
        *,
        hour_stride: int = 6,
    ) -> np.ndarray:
        """Time-mean |∇θe| — the persistent (orographic / sea-land) background.

        Used to anomaly-filter off-track fronts: a transient front passing
        through for a few hours barely moves a multi-day mean, so subtracting
        this leaves the synoptic signal. Sampled every ``hour_stride`` *hours*
        (not array indices) so it behaves the same for hourly and 3-hourly
        sources. Falls back to all available hours when the stride filters
        everything out (short cases).
        """
        hours = self.available_hours(model)
        sampled = [h for h in hours if h % hour_stride == 0] or hours
        acc: np.ndarray | None = None
        n = 0
        for h in sampled:
            grad = self.gradient_at_hour(model, h, level_hPa)
            if grad is None:
                continue
            acc = grad if acc is None else acc + grad
            n += 1
        if acc is None or n == 0:
            return np.zeros((len(self.lat), len(self.lon)))
        return acc / n


# ---------------------------------------------------------------------------
# Case-backed source (recompute path — calibration / ERA5 / fallback)
# ---------------------------------------------------------------------------


class CaseFieldSource(HewsonFieldSource):
    """Recompute diagnostics from a calibration :class:`Case` on demand.

    This is the historical ``analyze_route_fronts`` path, now behind the source
    interface. Diagnostics evaluate in well under a second on the 0.25° European
    grid, so per-hour recompute is fine for a route that hits a handful of
    forecast hours.
    """

    def __init__(self, case, *, terrain_mask: np.ndarray | None = None):
        self._case = case
        self.lat = case.lat
        self.lon = case.lon
        self.terrain_mask = terrain_mask

    @property
    def models(self) -> list[str]:
        return list(self._case.models)

    def available_hours(self, model: str) -> list[int]:
        return sorted(self._case.available_hours(model))

    def available_levels(self, model: str) -> list[int]:
        return self._case.available_levels(model)

    def resolve_level(self, model: str, level_hPa: int | None) -> int:
        # Defer to the Case so its legacy single-level (850-only) handling and
        # error messages stay authoritative.
        return self._case._resolve_level(model, level_hPa)

    def grids_at_hour(
        self, model: str, hour: int, level_hPa: int | None = None,
    ) -> HewsonGrids | None:
        fields = self._case.fields(model, hour, level_hPa)
        if fields is None:
            return None
        diag = compute_hewson_diagnostics(
            fields["theta_e"], self.lat, self.lon,
            u=fields["u850"], v=fields["v850"],
            terrain_mask=self.terrain_mask,
        )
        return HewsonGrids(
            theta_e=fields["theta_e"],
            gradient=diag["gradient"],
            tfp=diag["tfp"],
            neg_laplacian=diag["neg_laplacian"],
            advection=diag["advection"],
            tendency=self._tendency(model, hour, level_hPa),
            dT_dx=diag["dT_dx"],
            dT_dy=diag["dT_dy"],
        )

    def gradient_at_hour(
        self, model: str, hour: int, level_hPa: int | None = None,
    ) -> np.ndarray | None:
        # Compute only |∇θe| (via the zone detector's gradient, σ=0.5) — one
        # field read, no neighbour fetch. Matches the historical
        # ``compute_background_gradient`` numerics exactly so the validated
        # anomaly-filter behaviour is preserved.
        from weatherbrief.frontal.detect import compute_frontal_zones

        fields = self._case.fields(model, hour, level_hPa)
        if fields is None:
            return None
        return compute_frontal_zones(
            fields["theta_e"], self.lat, self.lon, terrain_mask=self.terrain_mask,
        )["gradient"]

    def _tendency(self, model: str, hour: int, level_hPa: int | None) -> np.ndarray:
        """∂θe/∂t via centred / one-sided difference across neighbouring hours."""
        avail = self.available_hours(model)
        prev_h = max((h for h in avail if h < hour), default=None)
        next_h = min((h for h in avail if h > hour), default=None)

        def _the(h: int) -> np.ndarray:
            return self._case.fields(model, h, level_hPa)["theta_e"]

        if prev_h is not None and next_h is not None:
            return (_the(next_h) - _the(prev_h)) / (next_h - prev_h)
        if next_h is not None:
            return (_the(next_h) - _the(hour)) / (next_h - hour)
        if prev_h is not None:
            return (_the(hour) - _the(prev_h)) / (hour - prev_h)
        return np.full((len(self.lat), len(self.lon)), np.nan, dtype=np.float64)


# ---------------------------------------------------------------------------
# Snapshot-backed source (production — reads the precompute NPZ)
# ---------------------------------------------------------------------------


class SnapshotFieldSource(HewsonFieldSource):
    """Read Hewson grids straight from a precomputed NPZ snapshot.

    One NPZ file holds one model's snapshot, so this source answers for a
    single ``model_name`` (passed at construction). Metric stacks are pulled
    lazily per level and cached, so a route that samples a few hours touches
    only the levels it needs. ``dT_dx``/``dT_dy`` are re-derived from the stored
    θe via :func:`theta_e_gradient_components` (the NPZ omits them by design).
    """

    def __init__(
        self,
        path: Path | str,
        *,
        model_name: str,
        terrain_mask: np.ndarray | None = None,
    ):
        from weatherbrief.hewson.precompute import DEFAULT_STRIDE_HOURS

        self._path = Path(path)
        self._model_name = model_name
        self.terrain_mask = terrain_mask
        with np.load(self._path) as npz:
            self.lat = npz["lat"].astype(np.float64)
            self.lon = npz["lon"].astype(np.float64)
            self._levels = sorted(int(L) for L in npz["levels"])
            self._stride = (
                int(npz["stride_hours"])
                if "stride_hours" in npz.files
                else DEFAULT_STRIDE_HOURS
            )
            self._init_unix = int(npz["init_time_unix"])
            self._n_time = int(npz["valid_times"].shape[0])
        # (level, metric) -> (n_time, n_lat, n_lon) stack, materialised on first use.
        self._stack_cache: dict[tuple[int, str], np.ndarray] = {}

    @property
    def init_time_unix(self) -> int:
        """Model init the snapshot's hour offsets are measured from."""
        return self._init_unix

    @property
    def stride_hours(self) -> int:
        return self._stride

    @property
    def models(self) -> list[str]:
        return [self._model_name]

    def available_levels(self, model: str) -> list[int]:
        self._check_model(model)
        return list(self._levels)

    def available_hours(self, model: str) -> list[int]:
        self._check_model(model)
        return [i * self._stride for i in range(self._n_time)]

    def grids_at_hour(
        self, model: str, hour: int, level_hPa: int | None = None,
    ) -> HewsonGrids | None:
        self._check_model(model)
        level = self.resolve_level(model, level_hPa)
        if hour % self._stride != 0:
            return None
        idx = hour // self._stride
        if idx < 0 or idx >= self._n_time:
            return None
        theta_e = self._slice(level, "theta_e", idx)
        if theta_e is None:
            return None
        dT_dx, dT_dy = theta_e_gradient_components(
            theta_e, self.lat, self.lon, terrain_mask=self.terrain_mask,
        )
        return HewsonGrids(
            theta_e=theta_e,
            gradient=self._slice(level, "gradient", idx),
            tfp=self._slice(level, "tfp", idx),
            neg_laplacian=self._slice(level, "neg_laplacian", idx),
            advection=self._slice(level, "advection", idx),
            tendency=self._slice(level, "tendency", idx),
            dT_dx=dT_dx,
            dT_dy=dT_dy,
        )

    def gradient_at_hour(
        self, model: str, hour: int, level_hPa: int | None = None,
    ) -> np.ndarray | None:
        self._check_model(model)
        level = self.resolve_level(model, level_hPa)
        if hour % self._stride != 0:
            return None
        idx = hour // self._stride
        if idx < 0 or idx >= self._n_time:
            return None
        return self._slice(level, "gradient", idx)

    # ------------------------------------------------------------------
    # Internals

    def _check_model(self, model: str) -> None:
        if model != self._model_name:
            raise ValueError(
                f"snapshot source holds {self._model_name!r}, asked for {model!r}"
            )

    def _slice(self, level: int, metric: str, idx: int) -> np.ndarray | None:
        """One ``(n_lat, n_lon)`` grid for ``metric`` at ``level`` / time ``idx``.

        Loads (and caches) the whole metric stack for the level the first time
        it's touched — cheaper than reopening the NPZ per hour when several
        hours of the same metric are read (background gradient, approach scan).
        """
        key = (level, metric)
        stack = self._stack_cache.get(key)
        if stack is None:
            npz_key = f"{metric}_{level}"
            with np.load(self._path) as npz:
                if npz_key not in npz.files:
                    return None
                stack = npz[npz_key].astype(np.float64)
            self._stack_cache[key] = stack
        if idx >= stack.shape[0]:
            return None
        return stack[idx]
