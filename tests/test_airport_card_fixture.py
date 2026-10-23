"""The web airport-card fixture (#881) is what the server's models produce.

``web/tests/fixtures/airport_cards.json`` feeds the card's vitest and
Playwright runs (and the review screenshots). It is written by
``scripts/export_airport_card_fixtures.py`` from ``AirportObservation``'s
computed card fields and the runway picture builder, so a server-side change
to a reading or a field name fails here rather than leaving the web drawing a
shape the server no longer sends. Rerun the script when it does.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_airport_card_fixture_matches_the_server():
    sys.path.insert(0, str(ROOT / "scripts"))
    from export_airport_card_fixtures import OUT, build

    assert OUT.read_text() == build(), (
        "web/tests/fixtures/airport_cards.json drifted from the server: "
        "run python scripts/export_airport_card_fixtures.py"
    )
