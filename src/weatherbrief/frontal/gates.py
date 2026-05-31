"""Front-detection gate configuration — a complete, serializable recipe.

Historically the ~10 acceptance/classification/geometry thresholds that decide
"is this TFP zero-crossing a front?" lived as loose module constants
(``_DEFAULT_GRADIENT_MIN`` &c. in ``route_sampling.py``) and were duplicated as
keyword arguments across four function signatures
(``detect_front_crossings`` / ``find_route_fronts`` / ``find_nearby_fronts`` /
``analyze_route_fronts``). Adding a gate meant touching every signature and the
CLI plumbing, and there was no way to vary a single gate without editing code.

``FrontGateConfig`` collapses all of those into one frozen, serializable
dataclass — a self-describing *detection recipe*. It deliberately carries the
**pressure level** because gates are level-specific: θe gradients are naturally
larger at 925 hPa (moist boundary layer) than at 700 hPa, so a single
``gradient_min`` across all levels is wrong (the zone detector already scales
its θe threshold 2× the T threshold for the same reason — ``detect.py``).

Two payoffs:

* **Calibration / sweeps** — a list of configs can be applied to the *same*
  candidate set with zero re-sampling (see ``route_sampling.apply_gate_config``).
* **Reproducibility** — the active config is stamped into ``route_fronts.json``
  so every briefing records which recipe produced it.

See ``designs/future/hewson-fields-aviation-advisories.md`` §6.2/§6.3 and
``designs/frontal-detection.md``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Mapping


@dataclass(frozen=True)
class FrontGateConfig:
    """A complete front-detection recipe: which level, which gates, what geometry.

    Field groups:

    * **identity** — ``name`` (preset label) + ``level_hPa`` (which precomputed
      level to detect at).
    * **acceptance gates** — decide IS-a-front. ``gradient_min`` (the air-mass
      boundary must be a real |∇θe| ridge) and ``delta_theta_e_min`` (the θe jump
      across the boundary must be a genuine change of air mass, not a col).
      ``anomaly_min`` is the off-track-only gradient-above-background gate that
      rejects persistent orographic / sea-land gradients.
    * **classification** — ``advection_min`` labels cold vs warm vs
      quasi-stationary; it *rejects nothing*.
    * **geometry / sampling** — route step, merge distance, the ±window the θe
      jump is measured across, the off-track proximity radius, the approach
      look-ahead, and whether the off-track anomaly filter runs.

    All thresholds default to the values validated on the 2026-05-31 Channel
    cold front (the historical module constants). Construct presets with
    :func:`get_preset` or derive variants with :meth:`with_overrides`.
    """

    name: str = "default"
    level_hPa: int = 850  # which precomputed level to detect at (925/850/700)

    # --- acceptance gates (decide IS-a-front) ---
    gradient_min: float = 6.0          # K/100km — significant (>4) .. classical (>8)
    delta_theta_e_min: float = 5.0     # K — |θe jump| across the ±window (air-mass contrast)
    anomaly_min: float = 2.0           # K/100km — off-track gradient above background

    # --- classification (labels only, rejects nothing) ---
    advection_min: float = 0.5         # K/h — below this: quasi-stationary

    # --- geometry / sampling ---
    step_km: float = 15.0              # densification step (~2 samples / 0.25° cell)
    merge_km: float = 60.0             # collapse multiple zero-crossings on one front
    airmass_window_km: float = 75.0    # ± window to measure the θe jump across a front
    proximity_km: float = 120.0        # off-track lateral/ahead search radius
    approach_dh: float | None = 2.0    # h look-ahead for closing/receding verdict
    use_anomaly_filter: bool = True

    # ------------------------------------------------------------------
    # Serialization

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable mapping of every field (stamped into artifacts)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FrontGateConfig":
        """Rebuild a config from :meth:`to_dict`, ignoring unknown keys.

        Tolerant of schema drift: extra keys (e.g. a future gate this code
        doesn't know) are dropped rather than raising, so an artifact written
        by a newer build still loads. Missing keys fall back to defaults.
        """
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def with_overrides(self, **overrides: Any) -> "FrontGateConfig":
        """Return a copy with the given fields replaced (frozen-safe)."""
        return replace(self, **overrides)


# ---------------------------------------------------------------------------
# Preset registry
#
# Mirrors the advisories param-override pattern (AdvisoryParameterDef): named,
# self-describing recipes a user / calibrator can swap without editing code.
# Eventually these become per-level (925/850/700) presets; for now the level is
# a separate axis the caller sets (the route stage picks the level nearest
# cruise, the map/calibration path sets it explicitly).
# ---------------------------------------------------------------------------


_PRESETS: dict[str, FrontGateConfig] = {
    # The validated baseline (2026-05-31 Channel front).
    "default": FrontGateConfig(name="default"),
    # Fewer false alarms: stronger gradient + air-mass-jump requirement.
    "strict": FrontGateConfig(
        name="strict", gradient_min=8.0, delta_theta_e_min=7.0, anomaly_min=3.0,
    ),
    # Catch weaker / pre-frontal boundaries (more POD, more FAR).
    "sensitive": FrontGateConfig(
        name="sensitive", gradient_min=4.0, delta_theta_e_min=3.0, anomaly_min=1.5,
    ),
    # Gradient-only: drop the air-mass-jump gate to isolate the |∇θe| ridge
    # (diagnostic — shows what the magnitude gate alone accepts).
    "gradient-only": FrontGateConfig(
        name="gradient-only", delta_theta_e_min=0.0,
    ),
}


def get_preset(name: str, *, level_hPa: int | None = None) -> FrontGateConfig:
    """Return a named preset, optionally overriding its detection level.

    Raises ``KeyError`` (with the available names) for an unknown preset so a
    typo in a CLI ``--gate`` arg fails loudly rather than silently detecting
    with the default recipe.
    """
    try:
        cfg = _PRESETS[name]
    except KeyError:
        raise KeyError(
            f"unknown gate preset {name!r}; available: {sorted(_PRESETS)}"
        ) from None
    if level_hPa is not None and level_hPa != cfg.level_hPa:
        cfg = cfg.with_overrides(level_hPa=level_hPa)
    return cfg


def preset_names() -> list[str]:
    """Sorted list of registered preset names (for CLI help / pickers)."""
    return sorted(_PRESETS)
