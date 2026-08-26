"""The extent invariants, over *real* packs instead of a synthetic fixture (#578).

`test_published_extent_consistency.py` runs the same predicates over contexts
this repo builds by hand. Every finding in the #576 verification, by contrast,
came from replaying real briefings — and nothing in CI does that, so the class
of defect that only appears on real geometry (partial model coverage, dense
arrival points, a lone dissenting model) had no automated reader at all.

This re-grades a handful of corpus packs with the current code and asserts the
invariants over the result. It checks the **re-run**, not the pack's saved
`route_advisories.json`: the saved manifest is whatever code wrote it, possibly
years old and predating fields like `representative_model`, so asserting today's
invariants over it would flag history rather than the code under test.

The corpus lives in a separate repo (see `designs/eval-digest-workbench.md`), so
this skips wherever it isn't checked out — CI included. That is deliberate: the
affordable full sweep is `scripts/rerun_advisories_diff.py --check-invariants`
(~70s for 200 packs), and this is the "handful of packs" version a developer
gets for free when the corpus is present.
"""

from __future__ import annotations

import os

import pytest

from weatherbrief.analysis.advisories import invariants
from weatherbrief.eval_workbench import config, corpus, rerun

# A "handful" by default — the full sweep is the script's job. Raise with
# ``EXTENT_INVARIANT_PACK_LIMIT=0`` (no limit) when hunting.
_DEFAULT_LIMIT = 8


def _packs():
    """Corpus packs that can actually be re-run, oldest-id first, capped."""
    if not config.eval_corpus_dir().exists():
        return []
    packs = [
        p for p in corpus.list_corpus("corpus")
        # A T1-stripped pack has no cross_section and cannot be re-graded;
        # skipping it here keeps the skip reason honest ("no corpus") rather
        # than turning a stale checkout into a wall of errors.
        if (corpus.pack_path(p.corpus_id, "corpus") / "route_analyses.json").exists()
    ]
    limit = int(os.getenv("EXTENT_INVARIANT_PACK_LIMIT", _DEFAULT_LIMIT))
    return packs[:limit] if limit > 0 else packs


_PACKS = _packs()
_SKIP = pytest.mark.skipif(
    not _PACKS,
    reason=(
        "no re-runnable eval corpus — set EVAL_CORPUS_DIR to a checkout of "
        "flyfun-weather-evalset (see designs/eval-digest-workbench.md)"
    ),
)


@_SKIP
@pytest.mark.parametrize("corpus_id", [p.corpus_id for p in _PACKS])
def test_every_invariant_holds_on_a_real_pack(corpus_id):
    pack_dir = corpus.pack_path(corpus_id, "corpus")
    baseline = rerun.load_saved_manifest(pack_dir)
    manifest = rerun.rerun_manifest(pack_dir, baseline)
    assert manifest.advisories, f"{corpus_id}: re-run produced no advisories"
    violations = invariants.check_manifest(manifest)
    assert not violations, [str(v) for v in violations]


@_SKIP
def test_the_sweep_sees_flagged_advisories():
    """Guards the guard: all-GREEN packs would make the sweep vacuous.

    Every invariant above is conditional on something being graded — an
    all-UNAVAILABLE corpus would pass them without asserting anything.
    """
    flagged = 0
    for pack in _PACKS:
        pack_dir = corpus.pack_path(pack.corpus_id, "corpus")
        manifest = rerun.rerun_manifest(pack_dir, rerun.load_saved_manifest(pack_dir))
        flagged += sum(
            1
            for a in manifest.advisories
            for m in a.per_model
            if m.status in invariants.FLAGGED
        )
    assert flagged, "no corpus pack flagged anything; the sweep proves nothing"
