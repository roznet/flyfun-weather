"""Observed cells (#650): detect, track and score radar cells across Europe.

Runs on a home compute node, never on the droplet: see
``designs/observed-cells.md``.  The analysis functions are pure over a
:class:`~weatherbrief.observed.frames.GridFrame` window; ``runner`` wires them
to the archive store and the loop.
"""

from .catalogue import SCHEMA, catalogue_path, read_catalogue
from .detect import Cell, TierDetection, detect
from .motion import FlowField, estimate_flow, masked_ncc
from .policy import DEFAULT_POLICY, CellPolicy, TierPolicy

__all__ = [
    "SCHEMA",
    "Cell",
    "CellPolicy",
    "DEFAULT_POLICY",
    "FlowField",
    "TierDetection",
    "TierPolicy",
    "catalogue_path",
    "detect",
    "estimate_flow",
    "masked_ncc",
    "read_catalogue",
]
