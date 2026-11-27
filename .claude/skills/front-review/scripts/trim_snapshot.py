#!/usr/bin/env python3
"""Runs INSIDE the weatherbrief container (copied in by fetch.py): trim one Hewson
snapshot to the first hours a front review needs.

    python trim_snapshot.py IN.npz OUT.npz [--max-hour 9]

The output keeps the snapshot schema (lat, lon, levels, stride_hours,
init_time_unix, valid_times, <metric>_<level> stacks), so the stock
``SnapshotFieldSource`` reads it unchanged, with two differences:

* only time indices 0..max_hour/stride are kept, and only the metrics the front
  extractor reads (theta_e, gradient, tfp, advection);
* ``background_<level>`` holds the time-mean |∇θe| of the FULL snapshot, computed
  exactly as ``HewsonFieldSource.background_gradient`` does. The trimmed file has
  too few hours to recompute it, and the anomaly gate needs it.

~3 MB instead of ~45 MB.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

KEEP_METRICS = ("theta_e", "gradient", "tfp", "advection")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--max-hour", type=int, default=9)
    a = ap.parse_args()

    from weatherbrief.frontal.sources import SnapshotFieldSource

    model = Path(a.src).parent.name
    source = SnapshotFieldSource(a.src, model_name=model)
    with np.load(a.src) as z:
        stride = int(z["stride_hours"]) if "stride_hours" in z.files else source.stride_hours
        n = a.max_hour // stride + 1
        out = {
            "lat": z["lat"], "lon": z["lon"], "levels": z["levels"],
            "stride_hours": np.int32(stride), "init_time_unix": z["init_time_unix"],
            "valid_times": z["valid_times"][:n],
        }
        for level in (int(L) for L in z["levels"]):
            for metric in KEEP_METRICS:
                key = f"{metric}_{level}"
                if key in z.files:
                    out[key] = z[key][:n]
    for level in (int(L) for L in out["levels"]):
        out[f"background_{level}"] = source.background_gradient(model, level).astype(np.float32)
    tmp = a.out + ".part.npz"
    np.savez_compressed(tmp, **out)
    Path(tmp).replace(a.out)
    print(f"trimmed {a.src} -> {a.out} ({n} steps)")


if __name__ == "__main__":
    main()
