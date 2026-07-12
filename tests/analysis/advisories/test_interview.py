"""Tests for the setup-interview presets (#387, slice 3)."""

from __future__ import annotations

import itertools

from weatherbrief.analysis.advisories import get_catalog, get_interview


def _catalog_by_id():
    return {e.id: e for e in get_catalog()}


def test_every_patch_references_valid_ids_and_keys():
    """Every option patches only real advisory ids / param keys."""
    catalog = _catalog_by_id()
    for q in get_interview().questions:
        for opt in q.options:
            for adv_id in opt.enabled:
                assert adv_id in catalog, f"{q.id}/{opt.id}: unknown advisory {adv_id}"
            for adv_id, params in opt.params.items():
                assert adv_id in catalog, f"{q.id}/{opt.id}: unknown advisory {adv_id}"
                valid_keys = {p.key for p in catalog[adv_id].parameters}
                for key in params:
                    assert key in valid_keys, (
                        f"{q.id}/{opt.id}: {adv_id} has no param {key}"
                    )


def test_every_patch_value_within_range():
    """Every param value an option sets lies within the catalog min/max."""
    catalog = _catalog_by_id()
    for q in get_interview().questions:
        for opt in q.options:
            for adv_id, params in opt.params.items():
                defs = {p.key: p for p in catalog[adv_id].parameters}
                for key, val in params.items():
                    pdef = defs[key]
                    if pdef.min is not None:
                        assert val >= pdef.min, f"{adv_id}.{key}={val} < min {pdef.min}"
                    if pdef.max is not None:
                        assert val <= pdef.max, f"{adv_id}.{key}={val} > max {pdef.max}"


def test_options_of_a_question_share_key_set():
    """Every option of a question declares the same enabled/param key set.

    Guarantees re-answering a question overwrites exactly the keys the prior
    answer wrote (reversible / idempotent).
    """
    for q in get_interview().questions:
        def keyset(opt):
            enabled_keys = set(opt.enabled.keys())
            param_keys = {(a, k) for a, ps in opt.params.items() for k in ps}
            return enabled_keys, param_keys

        keysets = [keyset(o) for o in q.options]
        first = keysets[0]
        for ks in keysets[1:]:
            assert ks == first, f"question {q.id} options declare mismatched keys"


def test_sibling_questions_declare_disjoint_keys():
    """No two questions own the same enabled id or (advisory, param) key."""
    questions = get_interview().questions

    def owned(q):
        enabled = set()
        params = set()
        for opt in q.options:
            enabled |= set(opt.enabled.keys())
            params |= {(a, k) for a, ps in opt.params.items() for k in ps}
        return enabled, params

    owned_by = {q.id: owned(q) for q in questions}
    for a, b in itertools.combinations(questions, 2):
        ea, pa = owned_by[a.id]
        eb, pb = owned_by[b.id]
        assert not (ea & eb), f"{a.id} & {b.id} share enable keys {ea & eb}"
        assert not (pa & pb), f"{a.id} & {b.id} share param keys {pa & pb}"


def test_apply_any_answer_combination_is_valid():
    """Applying any combination of one option per question yields valid settings.

    Simulates the client merge (each question owns disjoint keys) and re-checks
    every resulting param value against its catalog range.
    """
    catalog = _catalog_by_id()
    questions = get_interview().questions
    for combo in itertools.product(*[q.options for q in questions]):
        enabled: dict[str, bool] = {}
        params: dict[str, dict[str, float]] = {}
        for opt in combo:
            enabled.update(opt.enabled)
            for adv_id, ps in opt.params.items():
                params.setdefault(adv_id, {}).update(ps)
        # Every enabled id is real.
        for adv_id in enabled:
            assert adv_id in catalog
        # Every merged param value is within range.
        for adv_id, ps in params.items():
            defs = {p.key: p for p in catalog[adv_id].parameters}
            for key, val in ps.items():
                pdef = defs[key]
                if pdef.min is not None:
                    assert val >= pdef.min
                if pdef.max is not None:
                    assert val <= pdef.max


def test_interview_has_expected_questions():
    """v1 ships the three designed questions in order."""
    ids = [q.id for q in get_interview().questions]
    assert ids == ["flight_rules", "icing_equipage", "minimums"]
