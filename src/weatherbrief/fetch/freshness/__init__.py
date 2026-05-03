"""Marker-based freshness decision system (issue #108).

Replaces polling-based per-call meta.json fan-out with an in-memory marker
store gated by a hardcoded schedule registry, populated by a 5-min
background loop.  Most freshness checks become pure compute:

    stale = (now >= pack.next_expected_update)

Modules:
- :mod:`registry` — per-(model, source) cycle/delivery/horizon config
- :mod:`markers` — in-memory ``MarkerStore`` with asyncio lock
- :mod:`sources` — unified dynamic-check dispatch wrapping existing
  ``find_latest_*`` helpers
"""
