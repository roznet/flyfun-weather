"""The clutter levels, and the one predicate every consumer reads them with (#696).

A leaf module on purpose. The levels are a **wire contract**: the node writes
them onto each cell of the display file, and the droplet decides whether to
speak about that cell. They belong to neither side, so neither owns them, and
a one-line question about a string does not belong in the 500-line analysis
module that happens to write it (#696 review).

**This does not keep scipy out of the droplet**, and the review's rationale
for moving it was wrong about that: ``cells/__init__.py`` eagerly imports
``.detect`` and ``.motion``, so *any* import from this package pulls scipy —
including this module. That predates #696 (``cells_display`` already imported
``cells.display`` for ``DISPLAY_SCHEMA``), and it costs nothing in practice
because the web app loads scipy for the briefing pipeline regardless. Making
the package's ``__init__`` lazy is the only thing that would change it.

``clear`` means "no evidence of clutter", **not** "this is weather": most cells
are clear because nothing was measured against them.
"""

from __future__ import annotations

CLEAR = "clear"
SUSPECT = "suspect"
CONFIRMED = "confirmed"

#: The levels that count as "we have no confident storm to report here".
FLAGGED = (SUSPECT, CONFIRMED)


def suspect(cell: dict) -> bool:
    """Whether a catalogue or display cell carries clutter suspicion.

    One reader for every consumer — the droplet's storm and band filters, the
    review map and the evaluation harness — so "what counts as suspect" cannot
    drift between them. A cell from before #696 has no ``clutter`` block and is
    never suspect.
    """
    return ((cell.get("clutter") or {}).get("level")) in FLAGGED
