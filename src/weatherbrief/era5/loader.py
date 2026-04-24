"""Load ERA5 pressure-level GRIB into our internal field dict.

ERA5 pressure-level data comes as (time, level, lat, lon) arrays of:
  - t: temperature (K)
  - q: specific humidity (kg/kg)
  - u, v: wind components (m/s)

This module converts those raw ERA5 quantities into the field dict
that the rest of the frontal / Hewson code expects:
  - T850:   °C
  - Td850:  °C   (derived from q + T + pressure)
  - theta_e: K
  - u850, v850: km/h

All naming uses the `_850` suffix for backwards compatibility with the
existing code paths; callers wanting 925 or 700 hPa pass a different
`level_hPa` and receive the same-shaped dict (field names unchanged).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np

# MetPy used for q → Td conversion and θe computation
import metpy.calc as mpcalc
from metpy.units import units

# Import cfgrib lazily via xarray — the xarray stack is already a dep
import xarray as xr


def load_era5_fields(
    grib_path: str | Path,
    timestamp: datetime | np.datetime64 | str,
    level_hPa: int = 850,
) -> dict:
    """Extract one timestamp × one pressure level from an ERA5 GRIB.

    Parameters
    ----------
    grib_path : path to the ERA5 GRIB file.
    timestamp : requested valid time — accepts datetime, numpy datetime64,
        or ISO string ("2023-11-02T12:00").
    level_hPa : pressure level to extract. Must be present in the GRIB.

    Returns
    -------
    dict with keys matching `reshape_to_fields()` output:
        T850   : (n_lat, n_lon) float — temperature, °C
        Td850  : (n_lat, n_lon) float — dewpoint, °C (derived from q)
        theta_e: (n_lat, n_lon) float — equivalent potential temperature, K
        u850   : (n_lat, n_lon) float — U wind, km/h
        v850   : (n_lat, n_lon) float — V wind, km/h
    Plus metadata:
        lat    : (n_lat,) descending north→south
        lon    : (n_lon,) ascending west→east
        level  : the pressure level used (hPa)
        time   : the timestamp actually selected

    Notes
    -----
    The variable names keep the `_850` suffix for drop-in compatibility
    with existing frontal code even when called with `level_hPa=925` or
    `700`. The `level` key records the actual level.
    """
    # Normalize timestamp
    if isinstance(timestamp, str):
        timestamp = np.datetime64(timestamp)
    elif isinstance(timestamp, datetime):
        timestamp = np.datetime64(timestamp)

    # Context manager ensures the GRIB file handle is released. When
    # build_case_from_era5 calls this in a loop the leaks add up.
    with xr.open_dataset(str(grib_path), engine="cfgrib") as ds:
        try:
            slice_ = ds.sel(time=timestamp, isobaricInhPa=level_hPa)
        except KeyError as e:
            available_times = ds.time.values
            available_levels = ds.isobaricInhPa.values
            raise ValueError(
                f"Timestamp {timestamp} or level {level_hPa} not in GRIB. "
                f"Available times: {available_times[:3]}...{available_times[-1:]}, "
                f"levels: {list(available_levels)}"
            ) from e

        # Extract .values inside the with-block so arrays materialise
        # before the dataset is closed.
        T_K = slice_["t"].values            # K
        q = slice_["q"].values              # kg/kg
        u_ms = slice_["u"].values           # m/s
        v_ms = slice_["v"].values           # m/s
        slice_time = slice_.time.values
        slice_lat = slice_.latitude.values
        slice_lon = slice_.longitude.values

    # Convert to our pipeline's conventions
    T_C = T_K - 273.15                  # °C
    # MetPy wants pressure as a Quantity; scalar broadcasts against T + q
    Td_K = mpcalc.dewpoint_from_specific_humidity(
        pressure=level_hPa * units.hPa,
        temperature=T_K * units.kelvin,
        specific_humidity=q * units("kg/kg"),
    )
    Td_C = Td_K.to("degC").magnitude

    # Wind: m/s → km/h (matching what wind_to_uv produces elsewhere in the pipeline)
    u_kmh = u_ms * 3.6
    v_kmh = v_ms * 3.6

    # Equivalent potential temperature (K)
    theta_e = mpcalc.equivalent_potential_temperature(
        pressure=level_hPa * units.hPa,
        temperature=T_C * units.degC,
        dewpoint=Td_C * units.degC,
    ).to("kelvin").magnitude

    # Coordinates (from the closed dataset's copies)
    lat = slice_lat
    lon = slice_lon

    # ERA5 lat is typically descending. Flip if so, to match our convention
    # (frontal/grid.py uses ascending lat 35→60).
    if lat[0] > lat[-1]:
        lat = lat[::-1]
        T_C = T_C[::-1, :]
        Td_C = Td_C[::-1, :]
        theta_e = theta_e[::-1, :]
        u_kmh = u_kmh[::-1, :]
        v_kmh = v_kmh[::-1, :]

    return {
        "T850": T_C.astype(np.float64),
        "Td850": Td_C.astype(np.float64),
        "theta_e": theta_e.astype(np.float64),
        "u850": u_kmh.astype(np.float64),
        "v850": v_kmh.astype(np.float64),
        "lat": lat.astype(np.float64),
        "lon": lon.astype(np.float64),
        "level": level_hPa,
        "time": slice_time,
    }
