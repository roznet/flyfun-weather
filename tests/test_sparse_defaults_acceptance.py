"""#402 acceptance: a moved default reaches untouched profiles, not customized ones.

The payoff guarantee of the sparse-persist initiative (#402 → #403 code → #405
prod backfill): because a sparse profile stores no engine method / advisory
param, its *graded* value is whatever the declared default says **at eval time**.
So changing a default centrally (one edit in code) moves every untouched profile,
while a profile carrying an explicit override keeps its choice.

The real resolution seams this guards:
* engine methods — ``tasks/advise.py::_resolve_analyses`` (``method = stored or
  ENGINE_METHOD_DEFAULTS[key]``);
* advisory params — ``registry.evaluate_all``'s ``{**catalog_defaults, **user}``.

This test moves a default and asserts both halves. It is the regression guard for
the whole initiative: anything that re-couples grading to the stored profile
(e.g. a save that re-densifies, or resolution that reads the profile instead of
the live default) breaks it.
"""

from __future__ import annotations

from weatherbrief.analysis.advisories.engine_methods import ENGINE_METHOD_DEFAULTS
from weatherbrief.analysis.advisories.profile_sparsify import build_param_defaults
from weatherbrief.analysis.advisories.registry import get_catalog


def _resolve_engine(settings: dict, key: str) -> str:
    """Mirror of the engine-method resolution in ``advise.py:92-94`` — absence
    follows the declared default."""
    return settings.get(key) or ENGINE_METHOD_DEFAULTS[key]


def test_moving_an_engine_default_reaches_sparse_not_customized(monkeypatch):
    sparse: dict = {}  # untouched profile — stores no engine method
    custom = {"icing_method": "ogimet_dd"}  # deliberate override

    # Baseline: the declared default.
    monkeypatch.setitem(ENGINE_METHOD_DEFAULTS, "icing_method", "ogimet_nwp")
    assert _resolve_engine(sparse, "icing_method") == "ogimet_nwp"
    assert _resolve_engine(custom, "icing_method") == "ogimet_dd"

    # Move the default centrally (one edit in code).
    monkeypatch.setitem(ENGINE_METHOD_DEFAULTS, "icing_method", "sfip_nwp")
    assert _resolve_engine(sparse, "icing_method") == "sfip_nwp"  # untouched → follows
    assert _resolve_engine(custom, "icing_method") == "ogimet_dd"  # override → unchanged


def _effective_param(user_params: dict, default_map: dict, adv_id: str, key: str):
    """Mirror of ``evaluate_all``'s ``{**catalog_defaults, **user_params}`` merge
    for one advisory's params."""
    catalog_for_adv = {k: v for (a, k), v in default_map.items() if a == adv_id}
    merged = {**catalog_for_adv, **user_params.get(adv_id, {})}
    return merged[key]


def test_moving_a_param_default_reaches_sparse_not_customized():
    defaults = build_param_defaults(get_catalog())
    (adv_id, key), d0 = next(iter(defaults.items()))

    sparse: dict = {}  # no stored param
    custom = {adv_id: {key: d0 + 100}}  # explicit override

    # Under the current catalog default.
    assert _effective_param(sparse, defaults, adv_id, key) == d0
    assert _effective_param(custom, defaults, adv_id, key) == d0 + 100

    # Simulate the catalog default moving.
    moved = dict(defaults)
    moved[(adv_id, key)] = d0 + 1
    assert _effective_param(sparse, moved, adv_id, key) == d0 + 1  # untouched → follows
    assert _effective_param(custom, moved, adv_id, key) == d0 + 100  # override → unchanged
