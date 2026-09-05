import Foundation

// =============================================================================
// SYNC — port of `buildObserved` / `mergeObserved` /  `pickAnnulus` /
// `observedSource` in web/ts/visualization/data-extract.ts (#574).
//
// The API payload carries every station × every radius so that changing the
// corridor width is a client-side re-resolve with no request. This is the
// resolver that collapses it to one sample per route point at one width.
// =============================================================================

enum ObservedResolver {

    /// Largest gap (nm) between an analysis point and an observed station that
    /// still counts as the same place. The observed sampler walks the same route
    /// at the same spacing, so matches are normally exact; this tolerates a
    /// rounding difference, not a genuinely different point.
    static let matchToleranceNm: Double = 6

    // MARK: - Resolve

    /// Collapse the payload to one sample per station at one corridor width.
    ///
    /// `radiusOverrideNm` picks a sampled radius when it is one the server
    /// actually measured; anything else falls back to the widest, matching the
    /// web. Returns nil when there is nothing to draw, so every layer can guard
    /// on a single optional.
    static func resolve(
        _ observed: ObservedConditions?,
        radiusOverrideNm: Double? = nil
    ) -> VizObserved? {
        guard let observed else { return nil }
        let radii = observed.radiiNm ?? []
        guard !radii.isEmpty else { return nil }
        let radiusNm: Double = {
            if let override = radiusOverrideNm, radii.contains(override) { return override }
            return radii.max() ?? 0
        }()

        // Station id → along-route distance. A station with no enroute distance
        // has no position on this chart, so it is dropped rather than drawn at 0.
        var distanceById: [String: Double] = [:]
        for station in observed.stations ?? [] {
            if let d = station.enrouteDistanceNm { distanceById[station.id] = d }
        }

        var byId: [String: VizObservedPoint] = [:]
        func point(_ stationId: String) -> VizObservedPoint? {
            guard let distanceNm = distanceById[stationId] else { return nil }
            if let existing = byId[stationId] { return existing }
            let fresh = VizObservedPoint(distanceNm: distanceNm)
            byId[stationId] = fresh
            return fresh
        }

        for station in observed.reflectivity?.stations ?? [] {
            guard var p = point(station.stationId),
                  let a = pick(station.annuli, radiusNm, \.radiusNm) else { continue }
            p.radarNoCoverage = a.isInsufficient
            // A detection remains real even where the surrounding disc is unknown.
            // Coverage limits absence claims, not the measured positive signal.
            p.dbz = a.maxValue
            byId[station.stationId] = p
        }

        for station in observed.rainRate?.stations ?? [] {
            guard var p = point(station.stationId),
                  let a = pick(station.annuli, radiusNm, \.radiusNm) else { continue }
            p.rateNoCoverage = a.isInsufficient
            p.rateMmH = a.maxValue
            byId[station.stationId] = p
        }

        for station in observed.lightning?.stations ?? [] {
            guard var p = point(station.stationId),
                  let a = pick(station.annuli, radiusNm, \.radiusNm) else { continue }
            p.flashCount = a.flashCount
            p.flashRate = a.flashesPer1000Km2PerMin
            byId[station.stationId] = p
        }

        for station in observed.cloudTops?.stations ?? [] {
            guard var p = point(station.stationId),
                  let a = pick(station.annuli, radiusNm, \.radiusNm) else { continue }
            p.topsNoCoverage = a.isInsufficient
            p.topsHighestFt = a.highestFl.map { $0 * 100 }
            p.topsBins = topBins(a)
            // Retrieval method is not cloud layering or a confidence score.
            // In particular method 9 means opaque RTM + inversion (guide table 10).
            // Kelvin on the wire (the granule's own unit); °C for anything a
            // pilot reads.
            p.topsColdestC = a.coldestTopK.map { $0 - 273.15 }
            p.topsHighestCloudiness = a.highestCloudiness
            p.topsMedianCloudiness = a.medianCloudiness
            p.topsHighestAviationFl = a.highestAviationFl
            byId[station.stationId] = p
        }

        let points = byId.values.sorted { $0.distanceNm < $1.distanceNm }
        guard !points.isEmpty else { return nil }

        return VizObserved(
            radiiNm: radii.sorted(),
            radiusNm: radiusNm,
            points: points,
            reflectivity: source(observed.reflectivity, "Radar reflectivity"),
            rainRate: source(observed.rainRate, "Radar rain rate"),
            cloudTops: source(observed.cloudTops, "Satellite cloud tops"),
            lightning: source(observed.lightning, "Lightning"),
            summaryLines: observed.summaryLines ?? []
        )
    }

    /// Fold the resolved samples onto the route points, matched by along-route
    /// distance. Mutates in place, mirroring the web's `mergeObserved`.
    static func merge(into points: inout [VizPoint], observed: VizObserved?) {
        guard let observed, !observed.points.isEmpty else { return }
        for i in points.indices {
            var best: VizObservedPoint?
            var bestGap = Double.infinity
            for candidate in observed.points {
                let gap = abs(candidate.distanceNm - points[i].distanceNm)
                if gap < bestGap { bestGap = gap; best = candidate }
            }
            guard let best, bestGap <= matchToleranceNm else { continue }
            points[i].observed = best
        }
    }

    // MARK: - Helpers

    /// The sampled disc closest to the requested radius.
    ///
    /// Nearest rather than exact: the payload's radii are a server constant, and
    /// falling back to the closest measured disc beats rendering nothing. Never
    /// interpolated — a blended value between a 5 NM and a 20 NM disc describes
    /// no area that was actually measured.
    private static func pick<T>(
        _ annuli: [T], _ radiusNm: Double, _ radius: KeyPath<T, Double>
    ) -> T? {
        guard var best = annuli.first else { return nil }
        for a in annuli {
            if a[keyPath: radius] == radiusNm { return a }
            if abs(a[keyPath: radius] - radiusNm) < abs(best[keyPath: radius] - radiusNm) { best = a }
        }
        return best
    }

    private static func source(_ field: (any ObservedFieldMeta)?, _ label: String) -> VizObservedSource? {
        guard let field else { return nil }
        return VizObservedSource(
            source: field.source,
            label: label,
            validTime: field.validTime,
            ageMinutes: field.ageMinutes,
            windowMinutes: field.windowMinutes ?? 0,
            attribution: field.attribution?.text ?? ""
        )
    }

    /// Prefer the sparse fine histogram; fall back to the coarse bands for a pack
    /// built before it existed. Fine bins avoid implying the full coarse
    /// interval contains detected tops; missing bins do not prove clear air.
    private static func topBins(_ a: ObservedTopsAnnulus) -> [VizObservedTopBin] {
        // Valid retrieval samples, cloudy and clear. Parallax overlap and
        // unequal footprints prevent interpreting this as a sky-area share.
        let lookedAt = Double(a.validPx)
        let fine = a.flFine ?? [:]
        if !fine.isEmpty {
            return fine.keys
                .compactMap { Int($0) }
                .sorted()
                .map { fl in
                    let count = fine[String(fl)] ?? 0
                    return VizObservedTopBin(
                        label: "\(fl * 100)–\((fl + observedFineFlStep) * 100) ft MSL",
                        loFt: Double(fl) * 100,
                        hiFt: Double(fl + observedFineFlStep) * 100,
                        fraction: lookedAt > 0 ? Double(count) / lookedAt : 0,
                        count: count
                    )
                }
        }
        return observedCoarseTopBands.map { band in
            let count = a.flBins?[band.label] ?? 0
            return VizObservedTopBin(
                label: band.loFt == 0 ? "<\(Int(band.hiFt)) ft MSL"
                    : band.hiFt >= 60_000 ? "≥\(Int(band.loFt)) ft MSL"
                    : "\(Int(band.loFt))–\(Int(band.hiFt)) ft MSL",
                loFt: band.loFt,
                hiFt: band.hiFt,
                fraction: lookedAt > 0 ? Double(count) / lookedAt : 0,
                count: count
            )
        }
    }

}
