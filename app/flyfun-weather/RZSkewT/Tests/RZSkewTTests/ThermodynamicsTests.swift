import Testing
@testable import RZSkewT

@Suite("Thermodynamics")
struct ThermodynamicsTests {

    // MARK: - Saturation vapor pressure

    @Test("Saturation vapor pressure at 0°C ≈ 6.112 hPa")
    func saturationVaporPressureAtZero() {
        let es = Thermodynamics.saturationVaporPressure(tempC: 0)
        #expect(abs(es - 6.112) < 0.01)
    }

    @Test("Saturation vapor pressure at 20°C ≈ 23.4 hPa")
    func saturationVaporPressureAt20() {
        let es = Thermodynamics.saturationVaporPressure(tempC: 20)
        #expect(abs(es - 23.4) < 0.5)
    }

    @Test("Saturation vapor pressure at 100°C is in the right ballpark (Magnus less accurate above 60°C)")
    func saturationVaporPressureAt100() {
        let es = Thermodynamics.saturationVaporPressure(tempC: 100)
        #expect(es > 900 && es < 1100)
    }

    @Test("Saturation vapor pressure at -40°C ≈ 0.189 hPa (icing-relevant cold temps)")
    func saturationVaporPressureAtMinus40() {
        let es = Thermodynamics.saturationVaporPressure(tempC: -40)
        // Literature value ~0.189 hPa at -40°C
        #expect(abs(es - 0.189) < 0.02)
    }

    // MARK: - Potential temperature & dry adiabats

    @Test("Potential temperature at surface (15°C, 1000hPa) ≈ 288K")
    func potentialTemperatureAtSurface() {
        let theta = Thermodynamics.potentialTemperature(tempC: 15, pressureHPa: 1000)
        #expect(abs(theta - 288.15) < 0.1)
    }

    @Test("Dry adiabat roundtrip: T → θ → T")
    func dryAdiabatRoundtrip() {
        let t0: Double = 20
        let p0: Double = 1000
        let theta = Thermodynamics.potentialTemperature(tempC: t0, pressureHPa: p0)
        let tBack = Thermodynamics.dryAdiabatTemperature(theta: theta, pressureHPa: p0)
        #expect(abs(tBack - t0) < 0.001)
    }

    @Test("Dry adiabatic cooling: 20°C at 1000 lifted to 700 hPa")
    func dryAdiabatCooling() {
        let theta = Thermodynamics.potentialTemperature(tempC: 20, pressureHPa: 1000)
        let t700 = Thermodynamics.dryAdiabatTemperature(theta: theta, pressureHPa: 700)
        // Should cool ~30°C → about -10°C
        #expect(t700 < 0)
        #expect(t700 > -15)
    }

    // MARK: - Mixing ratio

    @Test("Saturation mixing ratio at 20°C, 1000hPa ≈ 14.7 g/kg")
    func saturationMixingRatioAt20() {
        let ws = Thermodynamics.saturationMixingRatio(tempC: 20, pressureHPa: 1000) * 1000
        #expect(abs(ws - 14.7) < 1.0)
    }

    @Test("Dewpoint from mixing ratio roundtrip")
    func dewpointMixingRatioRoundtrip() {
        let p: Double = 850
        let td: Double = 10
        let w = Thermodynamics.saturationMixingRatio(tempC: td, pressureHPa: p)
        let tdBack = Thermodynamics.dewpointFromMixingRatio(w: w, pressureHPa: p)
        #expect(abs(tdBack - td) < 0.1)
    }

    // MARK: - LCL

    @Test("LCL computation: 25°C/15°C at 1000 hPa ≈ 860 hPa")
    func lclComputation() {
        let lcl = Thermodynamics.liftingCondensationLevel(tempC: 25, dewpointC: 15, pressureHPa: 1000)
        #expect(lcl != nil)
        if let lcl {
            // Literature: LCL ~860 hPa for 25/15 at 1000
            #expect(abs(lcl.pressureHPa - 860) < 20)
        }
    }

    @Test("LCL when already saturated (T == Td) returns near surface pressure")
    func lclAlreadySaturated() {
        let lcl = Thermodynamics.liftingCondensationLevel(
            tempC: 15, dewpointC: 15, pressureHPa: 1000)
        #expect(lcl != nil)
        #expect(lcl!.pressureHPa > 990) // should be at or very near surface
    }

    // MARK: - Moist vs dry adiabat invariant

    @Test("Moist adiabat cools slower than dry adiabat from same starting point")
    func moistCoolsSlowerThanDry() {
        let startT = 20.0
        let theta = Thermodynamics.potentialTemperature(tempC: startT, pressureHPa: 1000)
        let tDry700 = Thermodynamics.dryAdiabatTemperature(theta: theta, pressureHPa: 700)

        let moistCurves = Thermodynamics.moistAdiabats(
            startTemps: [startT], pRange: 200...1000)
        let tMoist700 = moistCurves[0].first(where: { abs($0.pressureHPa - 700) < 15 })?.tempC

        #expect(tMoist700 != nil)
        // Moist adiabat always warmer than dry at same pressure (latent heat release)
        #expect(tMoist700! > tDry700)
    }

    // MARK: - Parcel path

    @Test("Parcel path has reasonable number of points")
    func parcelPathGeneration() {
        let path = Thermodynamics.parcelPath(
            surfaceTempC: 25, surfaceDewpointC: 15,
            surfacePressureHPa: 1000, topPressureHPa: 200
        )
        #expect(path.count > 100) // (1000-200)/5 = 160 steps
        #expect(path.first?.pressureHPa == 1000)
        // Temperature should decrease
        #expect(path.last!.tempC < path.first!.tempC)
    }

    @Test("Parcel path has no temperature discontinuities at LCL")
    func parcelPathContinuousAtLCL() {
        let path = Thermodynamics.parcelPath(
            surfaceTempC: 25, surfaceDewpointC: 15,
            surfacePressureHPa: 1000)
        for i in 1..<path.count {
            let dT = abs(path[i].tempC - path[i - 1].tempC)
            // No jump > 5°C between adjacent 5hPa levels
            #expect(dT < 5.0, "Temperature jump of \(dT)°C at index \(i) (p=\(path[i].pressureHPa))")
        }
    }

    @Test("Parcel path from high-altitude surface (e.g. 850 hPa)")
    func parcelPathHighAltitude() {
        let path = Thermodynamics.parcelPath(
            surfaceTempC: 10, surfaceDewpointC: 5,
            surfacePressureHPa: 850, topPressureHPa: 200
        )
        #expect(path.count > 100)
        #expect(path.first?.pressureHPa == 850)
        #expect(path.last!.tempC < path.first!.tempC)
    }

    // MARK: - CAPE / CIN

    @Test("CAPE > 0 for unstable profile, CIN <= 0 below LFC")
    func capeUnstableProfile() {
        // Warm moist surface, cold aloft = obviously unstable
        let levels = [
            SoundingLevel(pressureHPa: 1000, temperatureC: 28, dewpointC: 20),
            SoundingLevel(pressureHPa: 850, temperatureC: 10),
            SoundingLevel(pressureHPa: 700, temperatureC: -5),
            SoundingLevel(pressureHPa: 500, temperatureC: -20),
            SoundingLevel(pressureHPa: 300, temperatureC: -45),
        ]
        let path = Thermodynamics.parcelPath(
            surfaceTempC: 28, surfaceDewpointC: 20,
            surfacePressureHPa: 1000)
        let result = Thermodynamics.computeCAPECIN(
            environmentLevels: levels, parcelPath: path)
        #expect(result.cape > 0, "CAPE should be positive for unstable profile, got \(result.cape)")
        #expect(result.cin <= 0, "CIN should be non-positive, got \(result.cin)")
    }

    @Test("CAPE is zero for stable isothermal profile")
    func capeStableProfile() {
        // Isothermal atmosphere: parcel always colder than environment above LCL
        let levels = (0..<10).map { i in
            SoundingLevel(pressureHPa: 1000 - Double(i) * 80, temperatureC: 15)
        }
        let path = Thermodynamics.parcelPath(
            surfaceTempC: 15, surfaceDewpointC: 5,
            surfacePressureHPa: 1000)
        let result = Thermodynamics.computeCAPECIN(
            environmentLevels: levels, parcelPath: path)
        // Virtual temperature correction may produce a tiny positive residual
        #expect(result.cape < 10, "CAPE should be near-zero for stable isothermal profile, got \(result.cape)")
    }

    @Test("CAPE/CIN returns (0,0) for empty inputs")
    func capeEmptyInputs() {
        let r1 = Thermodynamics.computeCAPECIN(environmentLevels: [], parcelPath: [])
        #expect(r1.cape == 0 && r1.cin == 0)

        let r2 = Thermodynamics.computeCAPECIN(
            environmentLevels: [SoundingLevel(pressureHPa: 1000, temperatureC: 20)],
            parcelPath: [AtmosphericPoint(tempC: 25, pressureHPa: 1000)])
        #expect(r2.cape == 0 && r2.cin == 0) // single point, need >= 2
    }

    // MARK: - Pinned against known values (standard atmosphere / radiosonde reference)

    @Test("Standard atmosphere: 15°C/15°C/1013.25 hPa LCL at surface")
    func pinnedLCLSaturated() {
        // Fully saturated surface → LCL at surface
        let lcl = Thermodynamics.liftingCondensationLevel(
            tempC: 15, dewpointC: 15, pressureHPa: 1013.25)
        #expect(lcl != nil)
        #expect(lcl!.pressureHPa > 1008, "Saturated LCL should be at surface")
    }

    @Test("Pinned LCL: 30°C/20°C at 1000 hPa ≈ 865 hPa (textbook value)")
    func pinnedLCLTextbook() {
        // Stull (2000) gives LCL ≈ 865 hPa for T=30, Td=20, p=1000
        let lcl = Thermodynamics.liftingCondensationLevel(
            tempC: 30, dewpointC: 20, pressureHPa: 1000)
        #expect(lcl != nil)
        #expect(abs(lcl!.pressureHPa - 865) < 15,
                "LCL should be ~865 hPa, got \(lcl!.pressureHPa)")
    }

    @Test("Pinned CAPE: warm/moist tropical sounding has CAPE > 1000 J/kg")
    func pinnedCAPETropical() {
        // Simplified tropical sounding (warm moist surface, standard lapse rate)
        let levels = [
            SoundingLevel(pressureHPa: 1000, temperatureC: 30, dewpointC: 24),
            SoundingLevel(pressureHPa: 925, temperatureC: 24, dewpointC: 20),
            SoundingLevel(pressureHPa: 850, temperatureC: 18, dewpointC: 14),
            SoundingLevel(pressureHPa: 700, temperatureC: 4, dewpointC: -4),
            SoundingLevel(pressureHPa: 500, temperatureC: -12, dewpointC: -24),
            SoundingLevel(pressureHPa: 300, temperatureC: -38, dewpointC: -50),
            SoundingLevel(pressureHPa: 200, temperatureC: -55, dewpointC: -65),
        ]
        let path = Thermodynamics.parcelPath(
            surfaceTempC: 30, surfaceDewpointC: 24,
            surfacePressureHPa: 1000, topPressureHPa: 200)
        let result = Thermodynamics.computeCAPECIN(
            environmentLevels: levels, parcelPath: path)
        // Tropical soundings typically have CAPE 1000-4000 J/kg
        // Virtual temperature correction may increase CAPE somewhat
        #expect(result.cape > 1000, "Tropical CAPE should be > 1000 J/kg, got \(result.cape)")
        #expect(result.cape < 6000, "Tropical CAPE should be < 6000 J/kg, got \(result.cape)")
    }

    // MARK: - Background line generation

    @Test("Dry adiabats produce reasonable curves")
    func dryAdiabatCurves() {
        let curves = Thermodynamics.dryAdiabats()
        #expect(curves.count > 15) // (450-250)/10 = 20 curves
        for curve in curves {
            #expect(curve.count > 10)
            // Temperature should decrease with decreasing pressure
            #expect(curve.first!.tempC > curve.last!.tempC)
        }
    }

    @Test("Moist adiabats produce reasonable curves")
    func moistAdiabatCurves() {
        let curves = Thermodynamics.moistAdiabats()
        #expect(curves.count > 10)
        for curve in curves {
            #expect(curve.count >= 2)
        }
    }

    @Test("Mixing ratio lines produce reasonable curves")
    func mixingRatioLinesCurves() {
        let lines = Thermodynamics.mixingRatioLines()
        #expect(lines.count == 8) // default 8 values
        for line in lines {
            #expect(line.points.count > 5)
            #expect(line.mixingRatioGkg > 0)
        }
    }

    // MARK: - Standard atmosphere helpers

    @Test("Pressure to altitude: 1013.25 hPa → 0 ft (sea level)")
    func pressureToAltitudeSeaLevel() {
        let alt = Thermodynamics.pressureToAltitude(1013.25)
        #expect(abs(alt) < 1)
    }

    @Test("Pressure to altitude: 500 hPa ≈ 18000 ft")
    func pressureToAltitude500() {
        let alt = Thermodynamics.pressureToAltitude(500)
        #expect(abs(alt - 18300) < 500)
    }

    @Test("Altitude to pressure roundtrip")
    func altitudePressureRoundtrip() {
        let alt: Double = 10000
        let p = Thermodynamics.altitudeToPressure(alt)
        let altBack = Thermodynamics.pressureToAltitude(p)
        #expect(abs(altBack - alt) < 5)
    }

    // MARK: - Gravity constant

    @Test("Gravity constant matches standard value")
    func gravityConstant() {
        #expect(abs(Thermodynamics.g - 9.80665) < 0.00001)
    }

    // MARK: - Environment interpolation (public API)

    @Test("Interpolate environment temperature between two levels")
    func interpolateEnvironment() {
        let levels = [
            SoundingLevel(pressureHPa: 1000, temperatureC: 20),
            SoundingLevel(pressureHPa: 500, temperatureC: -10),
        ]
        let sorted = levels.sorted { $0.pressureHPa > $1.pressureHPa }
        let t = Thermodynamics.interpolateEnvironment(at: 700, sortedLevels: sorted)
        #expect(t != nil)
        // Should be between 20 and -10
        #expect(t! > -10 && t! < 20)
    }

    @Test("Interpolate environment extrapolates from nearest when outside range")
    func interpolateEnvironmentExtrapolation() {
        let levels = [
            SoundingLevel(pressureHPa: 850, temperatureC: 10),
            SoundingLevel(pressureHPa: 500, temperatureC: -15),
        ]
        let sorted = levels.sorted { $0.pressureHPa > $1.pressureHPa }
        // Below range: should return nearest (850 hPa → 10°C)
        let t = Thermodynamics.interpolateEnvironment(at: 1000, sortedLevels: sorted)
        #expect(t == 10)
    }
}
