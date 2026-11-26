"""`_get_pack_dir_released` hands the pooled DB connection back before a
read-only pack endpoint does its (possibly slow) file work.

The 15-connection pool ran out on 2026-11-26 06:17Z when a cross-section scrub
fired ~150 sounding-profile requests, each holding its connection while parsing
pack JSON. The helper closes the session right after the lookup; the guard below
keeps it from being used in a handler that still needs ``db`` afterwards.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock

from weatherbrief.api import packs


def test_released_closes_session_after_lookup(monkeypatch, tmp_path):
    db = MagicMock()
    order: list[str] = []
    monkeypatch.setattr(packs, "_get_pack_dir", lambda *a, **k: order.append("lookup") or tmp_path)
    db.close.side_effect = lambda: order.append("close")

    assert packs._get_pack_dir_released(db, "f", "ts", viewer_id="u") == tmp_path
    assert order == ["lookup", "close"]


def test_released_handlers_never_touch_db_afterwards():
    """A handler that calls `_get_pack_dir_released` must not use `db` again."""
    tree = ast.parse(Path(inspect.getsourcefile(packs)).read_text())
    users = []
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name == "_get_pack_dir_released":
            continue
        calls = [n.lineno for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_get_pack_dir_released"]
        if not calls:
            continue
        users.append(fn.name)
        later = sorted({n.lineno for n in ast.walk(fn)
                        if isinstance(n, ast.Name) and n.id == "db" and n.lineno > min(calls)})
        assert not later, f"{fn.name} uses db after releasing it (lines {later})"
    assert "get_sounding_profile" in users
