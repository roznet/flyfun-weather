"""Dev-only golden-labeling workbench for the LLM digest eval (#254).

This package powers a **dev/admin-only** workbench that renders the real
briefing view for a curated *corpus* of weather packs pulled from production,
with an in-view panel for an SME to record golden GREEN/AMBER/RED labels (per
guidance preset) plus a rationale. The golden labels are the durable, committed
artifact the eval scores against (see ``scripts/run_digest_eval.py``).

Isolation: the workbench is **never** reachable in production. Its API router
is only mounted when ``WEATHERBRIEF_EVAL_WORKBENCH`` is set (see
``api/app.py``) and every endpoint also requires an admin caller. The
"virtual-flight" resolver below short-circuits a handful of core read paths
**only** when the flag is on *and* the flight id is in the reserved ``eval-``
namespace; otherwise behaviour is byte-for-byte unchanged.

Layout::

    config.py      runtime gate + the ``eval-`` flight-id namespace
    situations.py  shared pack-loading / advisory-summary / situation tagging
    corpus.py      on-disk corpus of pulled packs + golden ``label.json``
    resolver.py    synthesize a Flight + BriefingPackMeta from a corpus pack

See ``designs/digest-eval-workbench.md`` for the full design and the
prod->dev pull workflow.
"""

from weatherbrief.eval_workbench.config import (
    EVAL_FLIGHT_PREFIX,
    corpus_id_from_flight_id,
    eval_corpus_dir,
    eval_flight_id,
    eval_workbench_enabled,
    is_eval_flight_id,
)

__all__ = [
    "EVAL_FLIGHT_PREFIX",
    "corpus_id_from_flight_id",
    "eval_corpus_dir",
    "eval_flight_id",
    "eval_workbench_enabled",
    "is_eval_flight_id",
]
