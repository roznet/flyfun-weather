import Foundation

/// Atmospheric thermodynamic computations for Skew-T reference lines and parcel analysis.
public enum Thermodynamics {

    // MARK: - Physical constants

    /// Specific gas constant for dry air (J/(kg·K))
    public static let Rd: Double = 287.05
    /// Specific gas constant for water vapor (J/(kg·K))
    public static let Rv: Double = 461.5
    /// Specific heat at constant pressure for dry air (J/(kg·K))
    public static let cp: Double = 1005.7
    /// Latent heat of vaporization (J/kg)
    public static let Lv: Double = 2.501e6
    /// Ratio Rd/cp (Poisson constant)
    public static let kappa: Double = Rd / cp // ~0.286
    /// Molecular weight ratio (Rd/Rv = epsilon)
    public static let epsilon: Double = Rd / Rv // ~0.622
    /// Standard gravitational acceleration (m/s²)
    public static let g: Double = 9.80665

    // Magnus formula constants (Bolton 1980)
    private static let magnusA: Double = 17.67
    private static let magnusB: Double = 243.5
    /// Reference saturation vapor pressure at 0°C (hPa)
    private static let es0: Double = 6.112

    // MARK: - Saturation vapor pressure

    /// Saturation vapor pressure (hPa) using Magnus formula.
    public static func saturationVaporPressure(tempC: Double) -> Double {
        es0 * exp(magnusA * tempC / (magnusB + tempC))
    }

    /// Saturation mixing ratio (kg/kg) at given temperature and pressure.
    public static func saturationMixingRatio(tempC: Double, pressureHPa: Double) -> Double {
        let es = saturationVaporPressure(tempC: tempC)
        return epsilon * es / (pressureHPa - es)
    }

    /// Dewpoint (°C) from mixing ratio (kg/kg) and pressure (hPa).
    public static func dewpointFromMixingRatio(w: Double, pressureHPa: Double) -> Double {
        let e = w * pressureHPa / (w + epsilon)
        let logRatio = log(e / es0)
        return magnusB * logRatio / (magnusA - logRatio)
    }

    // MARK: - Potential temperature

    /// Potential temperature (K) — temperature a parcel would have if brought to 1000 hPa dry-adiabatically.
    public static func potentialTemperature(tempC: Double, pressureHPa: Double) -> Double {
        (tempC + 273.15) * pow(1000.0 / pressureHPa, kappa)
    }

    /// Temperature (°C) on a dry adiabat of given potential temperature (K) at pressure (hPa).
    public static func dryAdiabatTemperature(theta: Double, pressureHPa: Double) -> Double {
        theta * pow(pressureHPa / 1000.0, kappa) - 273.15
    }

    // MARK: - Standard atmosphere

    /// Approximate altitude (ft) from pressure using the barometric formula.
    public static func pressureToAltitude(_ pressureHPa: Double) -> Double {
        let h_m = 44330.0 * (1.0 - pow(pressureHPa / 1013.25, 0.19026))
        return h_m * 3.28084
    }

    /// Approximate pressure (hPa) from altitude (ft) using the barometric formula.
    public static func altitudeToPressure(_ altitudeFt: Double) -> Double {
        let h_m = altitudeFt / 3.28084
        return 1013.25 * pow(1.0 - h_m / 44330.0, 5.255)
    }

    // MARK: - Dry adiabat curves

    /// Generate dry adiabat curves for background lines.
    /// Returns array of curves, each curve is an array of `AtmosphericPoint`.
    public static func dryAdiabats(
        thetaRange: ClosedRange<Double> = 250...450,
        thetaStep: Double = 10,
        pRange: ClosedRange<Double> = 200...1050,
        pStep: Double = 25
    ) -> [[AtmosphericPoint]] {
        var curves: [[AtmosphericPoint]] = []
        var theta = thetaRange.lowerBound
        while theta <= thetaRange.upperBound {
            var curve: [AtmosphericPoint] = []
            var p = pRange.upperBound
            while p >= pRange.lowerBound {
                let t = dryAdiabatTemperature(theta: theta, pressureHPa: p)
                curve.append(AtmosphericPoint(tempC: t, pressureHPa: p))
                p -= pStep
            }
            curves.append(curve)
            theta += thetaStep
        }
        return curves
    }

    // MARK: - Moist adiabat curves

    /// Moist adiabatic lapse rate dT/dp (K/hPa) for saturated ascent.
    /// Derived from the first law of thermodynamics for a saturated parcel.
    private static func moistLapseRate(tempC: Double, pressureHPa: Double) -> Double {
        let T = tempC + 273.15
        let ws = saturationMixingRatio(tempC: tempC, pressureHPa: pressureHPa)
        let numerator = Rd * T + Lv * ws
        let denominator = cp + Lv * Lv * ws * epsilon / (Rd * T * T)
        return numerator / (denominator * pressureHPa)
    }

    /// Generate moist (saturated) adiabat curves by integrating the moist lapse rate.
    /// `startTemps` are temperatures at the upper bound of `pRange` where each curve begins.
    public static func moistAdiabats(
        startTemps: [Double]? = nil,
        pRange: ClosedRange<Double> = 200...1050,
        dpStep: Double = 10
    ) -> [[AtmosphericPoint]] {
        let temps = startTemps ?? Array(stride(from: -40.0, through: 40.0, by: 4.0))
        var curves: [[AtmosphericPoint]] = []

        for startT in temps {
            var curve: [AtmosphericPoint] = []
            var t = startT
            var p = pRange.upperBound

            // Integrate upward (decreasing pressure)
            while p >= pRange.lowerBound {
                curve.append(AtmosphericPoint(tempC: t, pressureHPa: p))

                // RK2 (midpoint method) for better accuracy
                let dt1 = moistLapseRate(tempC: t, pressureHPa: p) * (-dpStep)
                let tMid = t + dt1 / 2
                let pMid = p - dpStep / 2
                let dt2 = moistLapseRate(tempC: tMid, pressureHPa: pMid) * (-dpStep)
                t += dt2
                p -= dpStep

                // Stop if temperature gets unreasonably cold
                if t < -100 { break }
            }
            if curve.count >= 2 {
                curves.append(curve)
            }
        }
        return curves
    }

    // MARK: - Mixing ratio lines

    /// Generate mixing ratio lines (constant w, nearly vertical).
    /// Each line traces the dewpoint for a given mixing ratio across pressures.
    public static func mixingRatioLines(
        values: [Double]? = nil,
        pRange: ClosedRange<Double> = 200...1050,
        pStep: Double = 50
    ) -> [MixingRatioLine] {
        let wValues = values ?? [0.4, 1, 2, 4, 7, 10, 16, 24] // g/kg
        var lines: [MixingRatioLine] = []

        for wGkg in wValues {
            let w = wGkg / 1000.0 // convert to kg/kg
            var points: [AtmosphericPoint] = []
            var p = pRange.upperBound
            while p >= pRange.lowerBound {
                let td = dewpointFromMixingRatio(w: w, pressureHPa: p)
                points.append(AtmosphericPoint(tempC: td, pressureHPa: p))
                p -= pStep
            }
            lines.append(MixingRatioLine(mixingRatioGkg: wGkg, points: points))
        }
        return lines
    }

    // MARK: - Parcel analysis (CAPE/CIN)

    /// LCL pressure and temperature by iterative lifting with linear interpolation at the crossing.
    public static func liftingCondensationLevel(tempC: Double, dewpointC: Double, pressureHPa: Double) -> LCLResult? {
        let theta = potentialTemperature(tempC: tempC, pressureHPa: pressureHPa)
        let w = saturationMixingRatio(tempC: dewpointC, pressureHPa: pressureHPa)
        let dpStep: Double = 5

        // Lift dry-adiabatically until saturation, interpolate the crossing
        var p = pressureHPa
        var prevDiff: Double? = nil
        var prevP = p
        while p >= 100 {
            let tParcel = dryAdiabatTemperature(theta: theta, pressureHPa: p)
            let tdParcel = dewpointFromMixingRatio(w: w, pressureHPa: p)
            let diff = tParcel - tdParcel // positive = unsaturated

            if diff <= 0 {
                // Crossed saturation — interpolate between previous and current level
                if let pd = prevDiff, pd > 0 {
                    let frac = pd / (pd - diff) // linear interpolation
                    let lclP = prevP - frac * dpStep
                    let lclT = dryAdiabatTemperature(theta: theta, pressureHPa: lclP)
                    return LCLResult(pressureHPa: lclP, tempC: lclT)
                }
                return LCLResult(pressureHPa: p, tempC: tParcel)
            }
            prevDiff = diff
            prevP = p
            p -= dpStep
        }
        return nil
    }

    /// Compute the parcel path from the surface through LCL and up to the top.
    /// Uses `liftingCondensationLevel` for consistent LCL detection.
    /// Returns array of `AtmosphericPoint` for the lifted parcel.
    public static func parcelPath(
        surfaceTempC: Double,
        surfaceDewpointC: Double,
        surfacePressureHPa: Double,
        topPressureHPa: Double = 200
    ) -> [AtmosphericPoint] {
        let theta = potentialTemperature(tempC: surfaceTempC, pressureHPa: surfacePressureHPa)
        let dpStep: Double = 5

        // Find LCL using the shared function for consistency
        let lcl = liftingCondensationLevel(tempC: surfaceTempC, dewpointC: surfaceDewpointC, pressureHPa: surfacePressureHPa)
        let lclPressure = lcl?.pressureHPa ?? 100 // if no LCL, stay dry all the way

        var path: [AtmosphericPoint] = []
        var p = surfacePressureHPa
        var tParcel = surfaceTempC

        while p >= topPressureHPa {
            if p > lclPressure {
                // Dry adiabatic ascent (above LCL pressure = below LCL altitude)
                tParcel = dryAdiabatTemperature(theta: theta, pressureHPa: p)
                path.append(AtmosphericPoint(tempC: tParcel, pressureHPa: p))
            } else {
                // Moist adiabatic ascent (RK2)
                if path.isEmpty || p == lclPressure {
                    // First moist point — use LCL temperature for continuity
                    if let lcl {
                        tParcel = lcl.tempC
                    }
                    path.append(AtmosphericPoint(tempC: tParcel, pressureHPa: p))
                } else {
                    let dt1 = moistLapseRate(tempC: tParcel, pressureHPa: p) * (-dpStep)
                    let tMid = tParcel + dt1 / 2
                    let pMid = p - dpStep / 2
                    let dt2 = moistLapseRate(tempC: tMid, pressureHPa: pMid) * (-dpStep)
                    tParcel += dt2
                    path.append(AtmosphericPoint(tempC: tParcel, pressureHPa: p))
                }
            }
            p -= dpStep
            if tParcel < -100 { break }
        }
        return path
    }

    /// Compute CAPE and CIN from environment profile and parcel path.
    /// Uses virtual temperature for accurate buoyancy calculation and trapezoidal integration.
    public static func computeCAPECIN(
        environmentLevels: [SoundingLevel],
        parcelPath: [AtmosphericPoint]
    ) -> CAPECINResult {
        guard parcelPath.count >= 2, environmentLevels.count >= 2 else {
            return CAPECINResult(cape: 0, cin: 0)
        }

        // Sort environment levels once (decreasing pressure = surface to top)
        let sortedEnv = environmentLevels.sorted { $0.pressureHPa > $1.pressureHPa }

        var cape: Double = 0
        var cin: Double = 0

        for i in 0..<(parcelPath.count - 1) {
            let pParcel = parcelPath[i].pressureHPa
            let tParcel = parcelPath[i].tempC
            let pNext = parcelPath[i + 1].pressureHPa
            let tParcelNext = parcelPath[i + 1].tempC

            guard let tEnv = interpolateEnvironment(at: pParcel, sortedLevels: sortedEnv),
                  let tEnvNext = interpolateEnvironment(at: pNext, sortedLevels: sortedEnv)
            else { continue }

            // Virtual temperature correction: Tv = T * (1 + 0.61 * w)
            // Parcel: use saturation mixing ratio (exact above LCL, slight overestimate below)
            let wParcel = saturationMixingRatio(tempC: tParcel, pressureHPa: pParcel)
            let tvParcelK = (tParcel + 273.15) * (1.0 + 0.61 * wParcel)

            let wParcelNext = saturationMixingRatio(tempC: tParcelNext, pressureHPa: pNext)
            let tvParcelNextK = (tParcelNext + 273.15) * (1.0 + 0.61 * wParcelNext)

            // Environment: use mixing ratio from dewpoint if available, otherwise dry (w=0)
            let wEnv = interpolateEnvironmentDewpoint(at: pParcel, sortedLevels: sortedEnv)
                .map { saturationMixingRatio(tempC: $0, pressureHPa: pParcel) } ?? 0
            let tvEnvK = (tEnv + 273.15) * (1.0 + 0.61 * wEnv)

            let wEnvNext = interpolateEnvironmentDewpoint(at: pNext, sortedLevels: sortedEnv)
                .map { saturationMixingRatio(tempC: $0, pressureHPa: pNext) } ?? 0
            let tvEnvNextK = (tEnvNext + 273.15) * (1.0 + 0.61 * wEnvNext)

            // Trapezoidal buoyancy
            let buoyancy1 = (tvParcelK - tvEnvK) / tvEnvK
            let buoyancy2 = (tvParcelNextK - tvEnvNextK) / tvEnvNextK
            let avgBuoyancy = (buoyancy1 + buoyancy2) / 2.0

            // Layer thickness in meters (hypsometric equation)
            let avgTvK = (tvEnvK + tvEnvNextK) / 2.0
            let dz = Rd * avgTvK / g * log(pParcel / pNext)

            if avgBuoyancy > 0 {
                cape += avgBuoyancy * g * dz
            } else {
                cin += avgBuoyancy * g * dz
            }
        }

        return CAPECINResult(cape: cape, cin: cin)
    }

    // MARK: - Environment interpolation

    /// Interpolate environment temperature at a given pressure using log-linear interpolation.
    /// Levels must be sorted by decreasing pressure (surface first).
    public static func interpolateEnvironment(at pressureHPa: Double, sortedLevels: [SoundingLevel]) -> Double? {
        for i in 0..<(sortedLevels.count - 1) {
            let pBelow = sortedLevels[i].pressureHPa
            let pAbove = sortedLevels[i + 1].pressureHPa
            if pressureHPa <= pBelow && pressureHPa >= pAbove {
                let logFrac = log(pBelow / pressureHPa) / log(pBelow / pAbove)
                return sortedLevels[i].temperatureC + logFrac * (sortedLevels[i + 1].temperatureC - sortedLevels[i].temperatureC)
            }
        }
        // Extrapolate from nearest level
        if let closest = sortedLevels.min(by: { abs($0.pressureHPa - pressureHPa) < abs($1.pressureHPa - pressureHPa) }) {
            return closest.temperatureC
        }
        return nil
    }

    /// Interpolate environment dewpoint at a given pressure.
    /// Returns nil if surrounding levels lack dewpoint data.
    private static func interpolateEnvironmentDewpoint(at pressureHPa: Double, sortedLevels: [SoundingLevel]) -> Double? {
        for i in 0..<(sortedLevels.count - 1) {
            let pBelow = sortedLevels[i].pressureHPa
            let pAbove = sortedLevels[i + 1].pressureHPa
            if pressureHPa <= pBelow && pressureHPa >= pAbove {
                guard let tdBelow = sortedLevels[i].dewpointC,
                      let tdAbove = sortedLevels[i + 1].dewpointC else { return nil }
                let logFrac = log(pBelow / pressureHPa) / log(pBelow / pAbove)
                return tdBelow + logFrac * (tdAbove - tdBelow)
            }
        }
        return sortedLevels.min(by: { abs($0.pressureHPa - pressureHPa) < abs($1.pressureHPa - pressureHPa) })?.dewpointC
    }
}
