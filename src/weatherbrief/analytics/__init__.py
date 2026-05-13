"""Usage analytics: privacy-first feature tracking.

See ``designs/usage-analytics.md`` for the full design. Quick map:

* :mod:`weatherbrief.analytics.events` — registry of allowed event names.
* :mod:`weatherbrief.analytics.models` — SQLAlchemy ORM rows.
* :mod:`weatherbrief.analytics.api` — ``POST /api/events`` ingest endpoint.
* :mod:`weatherbrief.analytics.enrich` — server-side dimension derivation.
* :mod:`weatherbrief.analytics.rollup` — nightly aggregation + retention.
* :mod:`weatherbrief.analytics.digest` — weekly summary (logs only for v1).
"""
