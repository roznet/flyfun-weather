import Foundation

// MARK: - Atmospheric data point

/// A point in temperature-pressure space on a Skew-T diagram.
public struct AtmosphericPoint: Sendable, Equatable, Hashable, Codable {
    public let tempC: Double
    public let pressureHPa: Double

    public init(tempC: Double, pressureHPa: Double) {
        self.tempC = tempC
        self.pressureHPa = pressureHPa
    }
}

// MARK: - Computation result types

/// Result of a Lifting Condensation Level computation.
public struct LCLResult: Sendable, Equatable, Hashable {
    public let pressureHPa: Double
    public let tempC: Double
}

/// Result of CAPE and CIN computation (J/kg).
public struct CAPECINResult: Sendable, Equatable, Hashable {
    public let cape: Double
    public let cin: Double
}

/// A constant-mixing-ratio line with its value (g/kg) and curve points.
public struct MixingRatioLine: Sendable, Equatable, Hashable {
    public let mixingRatioGkg: Double
    public let points: [AtmosphericPoint]
}

// MARK: - Sounding data

/// A single pressure level in a sounding profile.
public struct SoundingLevel: Sendable, Equatable, Hashable, Codable {
    public let pressureHPa: Double
    public let altitudeFt: Double?
    public let temperatureC: Double
    public let dewpointC: Double?
    public let windSpeedKt: Double?
    public let windDirectionDeg: Double?

    public init(
        pressureHPa: Double,
        altitudeFt: Double? = nil,
        temperatureC: Double,
        dewpointC: Double? = nil,
        windSpeedKt: Double? = nil,
        windDirectionDeg: Double? = nil
    ) {
        self.pressureHPa = pressureHPa
        self.altitudeFt = altitudeFt
        self.temperatureC = temperatureC
        self.dewpointC = dewpointC
        self.windSpeedKt = windSpeedKt
        self.windDirectionDeg = windDirectionDeg
    }
}

/// Complete sounding data for a Skew-T plot.
public struct SoundingProfile: Sendable, Equatable, Hashable, Codable {
    public let levels: [SoundingLevel]
    public let indices: SkewTIndices?
    public let overlays: SkewTOverlays

    public init(levels: [SoundingLevel], indices: SkewTIndices? = nil, overlays: SkewTOverlays = .empty) {
        self.levels = levels
        self.indices = indices
        self.overlays = overlays
    }
}

/// Thermodynamic indices for annotation.
public struct SkewTIndices: Sendable, Equatable, Hashable, Codable {
    public let lclPressureHPa: Double?
    public let lfcPressureHPa: Double?
    public let elPressureHPa: Double?
    public let capeSurfaceJkg: Double?
    public let cinSurfaceJkg: Double?
    public let freezingLevelFt: Double?
    public let liftedIndex: Double?

    public init(
        lclPressureHPa: Double? = nil,
        lfcPressureHPa: Double? = nil,
        elPressureHPa: Double? = nil,
        capeSurfaceJkg: Double? = nil,
        cinSurfaceJkg: Double? = nil,
        freezingLevelFt: Double? = nil,
        liftedIndex: Double? = nil
    ) {
        self.lclPressureHPa = lclPressureHPa
        self.lfcPressureHPa = lfcPressureHPa
        self.elPressureHPa = elPressureHPa
        self.capeSurfaceJkg = capeSurfaceJkg
        self.cinSurfaceJkg = cinSurfaceJkg
        self.freezingLevelFt = freezingLevelFt
        self.liftedIndex = liftedIndex
    }
}

/// Overlay data to draw on the Skew-T (cloud/icing/inversion bands).
public struct SkewTOverlays: Sendable, Equatable, Hashable, Codable {
    public let cloudLayers: [OverlayBand]
    public let icingZones: [OverlayBand]
    public let inversions: [InversionBand]
    public let cruiseAltitudeFt: Double?

    public static let empty = SkewTOverlays(cloudLayers: [], icingZones: [], inversions: [], cruiseAltitudeFt: nil)

    public init(cloudLayers: [OverlayBand], icingZones: [OverlayBand], inversions: [InversionBand], cruiseAltitudeFt: Double?) {
        self.cloudLayers = cloudLayers
        self.icingZones = icingZones
        self.inversions = inversions
        self.cruiseAltitudeFt = cruiseAltitudeFt
    }
}

/// A vertical band (e.g. cloud layer or icing zone) defined by altitude.
public struct OverlayBand: Sendable, Equatable, Hashable, Codable {
    public let baseFt: Double
    public let topFt: Double
    public let label: String

    public init(baseFt: Double, topFt: Double, label: String) {
        self.baseFt = baseFt
        self.topFt = topFt
        self.label = label
    }
}

/// A temperature inversion band defined by altitude and strength.
public struct InversionBand: Sendable, Equatable, Hashable, Codable {
    public let baseFt: Double
    public let topFt: Double
    public let strengthC: Double

    public init(baseFt: Double, topFt: Double, strengthC: Double) {
        self.baseFt = baseFt
        self.topFt = topFt
        self.strengthC = strengthC
    }
}
