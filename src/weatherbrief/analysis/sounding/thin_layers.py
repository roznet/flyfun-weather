"""The minimum vertical extent of a layer the sounding sees at a single level.

An icing zone, an SLD zone or a cloud deck seen at one level only has no
measured thickness: ``base_ft == top_ft``. A zero-height band is invisible in
the cross-section, and a zero-height cloud deck used to be dropped outright
(meteorology-decisions §86). The product gives every such layer the same
convention: it reaches at least ``MIN_LAYER_HALF_THICKNESS_FT`` above and below
its middle, so 1,000 ft in all.

This is the one place that convention lives. Every user calls
``widen_thin_layer``, so ``grep -rn widen_thin_layer src`` lists them, and a
change here (a different half-thickness, or capping it at the neighbouring
levels) changes all of them together. ``tests/test_thin_layers.py`` pins it.
"""

from __future__ import annotations

#: Half of the minimum thickness of a layer seen at one level only.
MIN_LAYER_HALF_THICKNESS_FT = 500.0


def widen_thin_layer(base_ft: float, top_ft: float) -> tuple[float, float]:
    """``(base_ft, top_ft)`` widened to the minimum thickness around its middle.

    A layer already at least ``2 × MIN_LAYER_HALF_THICKNESS_FT`` thick is
    returned unchanged. Callers round as they did before.
    """
    if top_ft - base_ft >= 2 * MIN_LAYER_HALF_THICKNESS_FT:
        return base_ft, top_ft
    mid_ft = (base_ft + top_ft) / 2
    return mid_ft - MIN_LAYER_HALF_THICKNESS_FT, mid_ft + MIN_LAYER_HALF_THICKNESS_FT
