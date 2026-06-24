import Foundation

/// Raw sounding profile from the API for client-side Skew-T rendering.
struct SoundingProfileResponse: Codable, Sendable {
    let pointIndex: Int
    let lat: Double
    let lon: Double
    let distanceFromOriginNm: Double
    let waypointIcao: String?
    let model: String
    let time: String
    let levels: [SoundingProfileLevel]
    let cruiseAltitudeFt: Int?
    let indices: ThermodynamicIndices?
    let cloudLayers: [EnhancedCloudLayer]?
    let icingZones: [IcingZone]?
    let inversionLayers: [InversionLayer]?
}

struct SoundingProfileLevel: Codable, Sendable {
    let pressureHpa: Int
    let altitudeFt: Double?
    let temperatureC: Double
    let dewpointC: Double?
    let windSpeedKt: Double?
    let windDirectionDeg: Double?

    // MARK: Extended fields (Phase 0 — server already sends these; previously dropped)
    // Property names rely on the shared decoder's `.convertFromSnakeCase` strategy,
    // so they map automatically from the snake_case JSON keys shown in comments.

    /// Relative humidity, 0–100 (`relative_humidity_pct`).
    let relativeHumidityPct: Double?
    /// Dewpoint depression T−Td in °C (`dewpoint_depression_c`).
    let dewpointDepressionC: Double?
    /// Wet-bulb temperature in °C (`wet_bulb_c`).
    let wetBulbC: Double?
    /// Equivalent potential temperature θe in Kelvin (`theta_e_k`).
    let thetaEK: Double?
    /// Environmental lapse rate in °C/km (`lapse_rate_c_per_km`).
    let lapseRateCPerKm: Double?
    /// Ogimet-DD icing index, 0–100 (`icing_index`).
    let icingIndex: Double?
    /// Ogimet-NWP icing index, 0–100 (`icing_index_nwp`).
    let icingIndexNwp: Double?
    /// SFIP icing severity, 0–100 (`sfip_100`).
    let sfip100: Double?
    /// Cloud liquid water content in g/m³ (`cloud_liquid_water_g_m3`).
    let cloudLiquidWaterGM3: Double?
    /// Ice mixing ratio in g/kg (`ice_mixing_ratio_g_kg`).
    let iceMixingRatioGKg: Double?
    /// NWP cloud area fraction at this level, 0–100 (`cloud_area_fraction_pct`).
    let cloudAreaFractionPct: Double?
    /// Richardson number for CAT/turbulence (`richardson_number`).
    let richardsonNumber: Double?
    /// Vertical velocity ω in Pa/s (`omega_pa_s`).
    let omegaPaS: Double?
    /// Vertical velocity in ft/min (`w_fpm`).
    let wFpm: Double?
}
