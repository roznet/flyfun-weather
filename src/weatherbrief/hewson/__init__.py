"""Hewson diagnostic field precompute.

Produces NPZ snapshots of θe-derived diagnostic fields (gradient, neg-Laplacian,
TFP, advection, tendency) on the 0.25° European grid at 925/850/700 hPa, one
snapshot per (model, init cycle). Consumed by the interactive forecast map,
cross-section overlay, and per-leg advisory evaluators.

Entry points:
    run_once(models, ...)           — library call used by the scheduler loop
                                       and CLI alike (one function, two callers)
    python -m weatherbrief.hewson precompute [flags]
                                      — ad-hoc / debug invocation

See designs/future/hewson-fields-aviation-advisories.md §6.1 for the snapshot
schema and §4 for the resolution / level / cadence decisions.
"""

from weatherbrief.hewson.precompute import (
    DEFAULT_LEVELS,
    DEFAULT_MODELS,
    SnapshotResult,
    load_snapshot,
    purge_old_snapshots,
    resolve_output_dir,
    run_once,
    snapshot_path,
)

__all__ = [
    "DEFAULT_LEVELS",
    "DEFAULT_MODELS",
    "SnapshotResult",
    "load_snapshot",
    "purge_old_snapshots",
    "resolve_output_dir",
    "run_once",
    "snapshot_path",
]
