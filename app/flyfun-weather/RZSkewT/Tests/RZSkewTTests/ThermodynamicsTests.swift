import Testing
@testable import RZSkewT

@Suite("Thermodynamics")
struct ThermodynamicsTests {

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

    @Test("LCL computation: 25°C/15°C at 1000 hPa")
    func lclComputation() {
        let lcl = Thermodynamics.liftingCondensationLevel(tempC: 25, dewpointC: 15, pressureHPa: 1000)
        #expect(lcl != nil)
        if let lcl {
            // LCL should be around 850-900 hPa
            #expect(lcl.pressureHPa < 950)
            #expect(lcl.pressureHPa > 750)
        }
    }

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
    func mixingRatioLines() {
        let lines = Thermodynamics.mixingRatioLines()
        #expect(lines.count == 8) // default 8 values
        for line in lines {
            #expect(line.points.count > 5)
            #expect(line.w > 0)
        }
    }
}
