"""Direct eccodes GRIB reader for the hot decode paths (#674 phase 1).

The ECMWF ``a1``/``a2`` decoders and the ICON-EU model-level decoder used to
go through ``cfgrib.open_datasets``: index the whole file, build xarray
hypercubes, then interpolate. Most of that time was spent outside the GRIB
data itself (cfgrib's dataset build was ~60 % of an ECMWF decode). This module
reads messages one at a time with eccodes instead, computes bilinear weights
once per grid, and gathers every route point from each message with one numpy
operation. Only messages a caller selects from their header keys are unpacked.

What cfgrib used to do for us, and how this reader reproduces it (each point is
pinned by ``tests/test_grib_reader.py`` against the cfgrib path):

- **Axes.** cfgrib takes a regular_ll grid's axes from eccodes'
  ``distinctLatitudes``/``distinctLongitudes``, in scan order, and reshapes the
  values to ``(Ny, Nx)``. Same here. That single choice covers the scan
  direction (``jScansPositively``), the meridian-crossing ECMWF Europe GRIB2
  grid (342.5→39.5 is presented as −17.5…39.5 by eccodes itself), and leaves
  the US GRIB2 grid in 0–360 exactly as cfgrib does; targets are aligned to
  each grid's axis by ``decode._align_lons_to_axis`` (#673).
- **Missing values.** cfgrib sets ``missingValue`` to float32-max before
  unpacking and turns exactly those cells into NaN, so only bitmap-masked
  cells become NaN (a legitimate 9999 stays 9999). Same here.
- **float32.** cfgrib stores values as float32; the decoders then gathered in
  float64. Values are rounded through float32 here too, so the gather is
  bit-identical.
- **alternativeRowScanning** is undone the way cfgrib undoes it.
- **Grid identity** is ``md5GridSection``, the key cfgrib's geometry cache and
  dataset split use, so messages on the same grid share one set of weights.

Grid types other than regular_ll / regular_gg are not handled (cfgrib gave the
old decoders no 1-D lat/lon axes for them either, so they were skipped there
too). Dataset-layout rules (which variables cfgrib dropped) are applied by the
callers in ``decode.py``, because they differ per decoder.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# cfgrib's MISSING_VAUE_INDICATOR: the value it sets ``missingValue`` to, so
# that only bitmap-masked cells come back as this and nothing real collides.
_MISSING_VALUE = float(np.finfo(np.float32).max)

_DIMENSION_GRID_TYPES = {"regular_ll", "regular_gg"}

# Rollback switch for the whole of phase 1. ``cfgrib`` restores the previous
# decoders on the three hot paths; anything else (or unset) uses this reader.
DECODER_ENV = "WB_GRIB_DECODER"


def use_cfgrib_decoder() -> bool:
    """True when ``WB_GRIB_DECODER=cfgrib`` asks for the pre-#674 decoders.

    Read at call time so a redeploy (or a test) can flip it without a reimport.
    """
    return os.environ.get(DECODER_ENV, "").strip().lower() == "cfgrib"


@dataclass(frozen=True)
class GribGrid:
    """A regular lat/lon grid's axes, exactly as cfgrib would present them."""

    key: str
    lats: np.ndarray  # float64, scan order
    lons: np.ndarray  # float64, scan order
    shape: tuple[int, int]  # (Ny, Nx)
    alternative_row_scanning: bool


class GribMessage:
    """One message's header, with the values unpacked only on request.

    Valid only inside the iteration step that yielded it: the eccodes handle
    is released as soon as the iterator moves on.
    """

    __slots__ = (
        "_gid", "var", "type_of_level", "level", "param_id", "header",
        "grid_key", "grid_type",
    )

    def __init__(self, gid) -> None:
        import eccodes

        self._gid = gid
        cf_var = _get_str(gid, "cfVarName")
        # cfgrib names a variable by cfVarName unless eccodes has none.
        self.var = (
            cf_var if cf_var not in ("", "undef", "unknown")
            else _get_str(gid, "shortName")
        )
        self.type_of_level = _get_str(gid, "typeOfLevel")
        try:
            self.level = float(eccodes.codes_get(gid, "level", float))
        except Exception:
            self.level = 0.0  # not NaN: NaN keys never group in a dict
        try:
            self.param_id = int(eccodes.codes_get(gid, "paramId"))
        except Exception:
            self.param_id = 0
        # Anything that would make cfgrib add a dimension beyond the level
        # (a second step, date or ensemble member for the same variable/level).
        self.header = (
            _get_str(gid, "dataDate"),
            _get_str(gid, "dataTime"),
            _get_str(gid, "stepRange"),
            _get_str(gid, "number"),
        )
        self.grid_type = _get_str(gid, "gridType")
        # An empty key would put every grid on one set of weights, so fall
        # back to the geometry itself if eccodes cannot hash the section.
        self.grid_key = _get_str(gid, "md5GridSection") or "|".join(
            _get_str(gid, k) for k in (
                "gridType", "Ni", "Nj",
                "latitudeOfFirstGridPointInDegrees", "latitudeOfLastGridPointInDegrees",
                "longitudeOfFirstGridPointInDegrees", "longitudeOfLastGridPointInDegrees",
            )
        )

    def grid(self) -> GribGrid | None:
        """The message's grid axes, or None when it has no 1-D lat/lon axes."""
        import eccodes

        if self.grid_type not in _DIMENSION_GRID_TYPES:
            return None
        gid = self._gid
        try:
            nx = int(eccodes.codes_get(gid, "Nx"))
            ny = int(eccodes.codes_get(gid, "Ny"))
            lats = np.asarray(eccodes.codes_get_array(gid, "distinctLatitudes"), dtype=np.float64)
            lons = np.asarray(eccodes.codes_get_array(gid, "distinctLongitudes"), dtype=np.float64)
        except Exception:
            logger.debug("grid read failed for %s", self.var, exc_info=True)
            return None
        if lats.size != ny or lons.size != nx:
            logger.debug(
                "skip %s: axis sizes (%d, %d) != (Ny, Nx) (%d, %d)",
                self.var, lats.size, lons.size, ny, nx,
            )
            return None
        try:
            alt = bool(eccodes.codes_get(gid, "alternativeRowScanning"))
        except Exception:
            alt = False
        return GribGrid(self.grid_key, lats, lons, (ny, nx), alt)

    def values(self, grid: GribGrid) -> np.ndarray | None:
        """Unpack the field as float32 ``(Ny, Nx)``, bitmap-masked cells NaN."""
        import eccodes

        gid = self._gid
        eccodes.codes_set(gid, "missingValue", _MISSING_VALUE)
        raw = eccodes.codes_get_values(gid)
        if raw.size != grid.shape[0] * grid.shape[1]:
            return None
        arr = raw.astype(np.float32).reshape(grid.shape)
        if grid.alternative_row_scanning:
            arr[1::2, :] = arr[1::2, ::-1]
        arr[arr == _MISSING_VALUE] = np.nan
        return arr


def _get_str(gid, key: str) -> str:
    import eccodes

    try:
        return str(eccodes.codes_get(gid, key, str))
    except Exception:
        return ""


@contextmanager
def _as_path(source: Path | str | bytes) -> Iterator[Path]:
    """eccodes reads from a real file; spill a byte blob to a temp file."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        with tempfile.NamedTemporaryFile(suffix=".grib", delete=False) as tmp:
            tmp.write(source)
            tmp_path = Path(tmp.name)
        try:
            yield tmp_path
        finally:
            tmp_path.unlink(missing_ok=True)
    else:
        yield Path(source)


def iter_messages(source: Path | str | bytes) -> Iterator[GribMessage]:
    """Yield every message in a GRIB file (or byte blob), in file order.

    Each handle is released when the iterator advances, so at most one
    message's values are ever held by this function.
    """
    import eccodes

    with _as_path(source) as path, open(path, "rb") as f:
        while True:
            gid = eccodes.codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                yield GribMessage(gid)
            finally:
                eccodes.codes_release(gid)


def gather_bilinear(values: np.ndarray, bw) -> np.ndarray:
    """Bilinear gather of ``values[..., lat, lon]`` at the corners in ``bw``.

    ``bw`` is a ``decode._GridWeights``. Returns float64, shape
    ``values.shape[:-2] + (len(bw.inb_idx),)``.

    Edge rule (#674): a corner with zero weight never blanks the result. A
    target exactly on a grid row (or column) next to a masked cell takes the
    value of its own row; it is not lost because the cell on the far side of
    the zero-weight edge is NaN. (Seen at EGVN, 51.75°N, where xarray's
    ``.interp`` picked the cell on the masked side and returned nothing.) A
    NaN corner with any non-zero weight still blanks the value.

    Arithmetic order matches the previous gather (w00, w01, w10, w11, summed
    left to right in float64), so unmasked results are bit-identical to it.
    """
    out = None
    for w, i, j in (
        (bw.w00, bw.i0, bw.j0),
        (bw.w01, bw.i0, bw.j1),
        (bw.w10, bw.i1, bw.j0),
        (bw.w11, bw.i1, bw.j1),
    ):
        term = w * values[..., i, j]
        term = np.where(w == 0.0, 0.0, term)
        out = term if out is None else out + term
    return out
