"""Pipeline task modules — independently runnable stages.

Re-exports the main entry points so callers can do::

    from weatherbrief.tasks import run_fetch, run_analysis, run_advisories
"""

from weatherbrief.tasks.advise import (  # noqa: F401
    AdvisoryResult,
    run_advisories,
    run_advisories_from_pack,
)
from weatherbrief.tasks.analyze import (  # noqa: F401
    AnalysisResult,
    analyze_all_route_points,
    analyze_waypoint,
    compute_interpolated_time,
    compute_route_tracks,
    run_analysis,
    run_analysis_from_pack,
)
from weatherbrief.tasks.artifacts import (  # noqa: F401
    load_cross_sections,
    load_elevation_profile,
    load_fetch_meta,
    load_route_analyses,
    load_route_points,
    save_advisory_artifacts,
    save_analysis_artifacts,
    save_fetch_artifacts,
    write_pack_meta,
)
from weatherbrief.tasks.fetch import (  # noqa: F401
    FetchResult,
    run_fetch,
)
from weatherbrief.tasks.route_weather import (  # noqa: F401
    run_observation_comparison,
    run_route_weather,
)
from weatherbrief.tasks.outputs import (  # noqa: F401
    DigestResult,
    GrametResult,
    SkewtResult,
    run_gramet,
    run_llm_digest,
    run_skewt,
)
