#if DEBUG
import Foundation

/// Canned, internally-consistent briefing fixtures for `fixture-1`
/// (LFMD → LFML, D-1, AMBER overall with one RED convective advisory).
///
/// Serves the whole briefing surface for the XCUITest journeys (#318): the
/// advisory dashboard + drill-down, the cross-section, the digest, and the
/// airport-conditions strip. The blobs are the shared `app/fixtures/api/*.json`
/// files (#895, one per endpoint response, the same files the Android app
/// serves), decoded through the production `JSONDecoder.weatherBrief`
/// (snake_case → camelCase, the same path the real network responses take), so
/// a model change that breaks the contract fails loudly here — see `FixtureFiles`.
///
/// Coherence rules the blobs keep so nothing renders as a contradiction:
///  - Models are exactly `["ecmwf", "gfs"]` everywhere (pack init-times, route
///    analyses, advisory per-model splits, airport conditions).
///  - The route is 5 points over 78 nm; the convective RED peaks near LFMD on
///    GFS while ECMWF stays AMBER — the "RED under blue sky" cross-check story.
///  - `assessment` is AMBER on the pack + digest, matching the worst *non-red*
///    aggregate the dashboard collapses to after the single RED convective card.
enum FixtureBriefingData {

    static let flightId = "fixture-1"
    static let timestamp = "2099-06-30T06:00:00Z"

    // MARK: - Pack metadata

    static let pack: PackMetaResponse = FixtureFiles.decode(PackMetaResponse.self, "pack")

    static let packs: [PackMetaResponse] = [pack]

    // MARK: - Advisories

    static let advisories: AdvisoriesResponse = FixtureFiles.decode(AdvisoriesResponse.self, "advisories")

    // MARK: - Advisory drill-down (convective "why it's RED")

    static let convectiveDetail: AdvisoryDetailResponse = FixtureFiles.decode(AdvisoryDetailResponse.self, "advisory-detail-convective")

    // MARK: - Digest

    static let digest: DigestResponse = FixtureFiles.decode(DigestResponse.self, "digest")

    // MARK: - Snapshot (route + waypoint analyses + observations)

    static let snapshot: SnapshotResponse = FixtureFiles.decode(SnapshotResponse.self, "snapshot")

    // MARK: - Live observation layer (#637)

    /// What `/live` returns before any live data exists: every block null.
    static let nullLiveLayer: LiveLayerResponse = FixtureFiles.decode(LiveLayerResponse.self, "live-null")

    /// A populated layer (served under `FLYFUN_MOCK_LIVE=1`). Display blocks are
    /// null so the pack's own observations stay on screen; only the
    /// "since this briefing" changes are added. `pack_timestamp` deliberately
    /// uses "+00:00" where the pack meta uses "Z" — they denote the same instant.
    /// The SIGMET change keys the pack's `LFMM SIGMET T04` bulletin.
    static let liveLayer: LiveLayerResponse = FixtureFiles.decode(LiveLayerResponse.self, "live")

    // MARK: - Route analyses (5 points × ecmwf/gfs — drives the cross-section)

    /// Each point carries a full ECMWF + GFS sounding so the cross-section renderer
    /// has real layers to draw (clouds, icing, convective towers, CAT, inversions)
    /// and `extractVizData` has non-trivial data to map. GFS peaks the convective
    /// tower near LFMD (point 0/1) while ECMWF stays quiet — the same disagreement
    /// the convective advisory + drill-down narrate.
    static let routeAnalyses: RouteAnalysesResponse = FixtureFiles.decode(RouteAnalysesResponse.self, "route-analyses")

    // MARK: - Elevation (terrain profile)

    static let elevation: ElevationResponse = FixtureFiles.decode(ElevationResponse.self, "elevation")
}
#endif
