"""Every route commits its session before the response is sent (#784).

A bare ``Depends(get_db)`` commits only after the response has gone out: the
MCP ``create_flight`` -> refresh call arrived 14 ms after the 201 and 404'd.
It also gives the request a second session next to flyfun-common's
function-scoped ``current_user_id``. Declare ``Depends(get_db, scope="function")``.
The commit timing itself is tested in flyfun-common (``tests/test_db_scope.py``).
"""

from flyfun_common.db import find_request_scoped_db
from flyfun_common.db.deps import _iter_route_dependants

from weatherbrief.api.app import create_app


def test_no_route_resolves_get_db_at_request_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    app = create_app()
    # Guards against the walker going blind on a FastAPI routing change.
    assert len(list(_iter_route_dependants(app.routes))) > 200
    assert find_request_scoped_db(app) == []
