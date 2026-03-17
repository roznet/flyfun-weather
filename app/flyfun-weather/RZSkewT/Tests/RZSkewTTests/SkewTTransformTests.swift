import CoreGraphics
import Testing
@testable import RZSkewT

@Suite("SkewTTransform")
struct SkewTTransformTests {

    private func makeTransform() -> SkewTTransform {
        SkewTTransform(size: CGSize(width: 600, height: 800))
    }

    @Test("Bottom pressure maps to bottom of plot area")
    func bottomPressure() {
        let t = makeTransform()
        let y = t.pressureToY(t.config.pBottom)
        #expect(abs(y - t.plotArea.bottom) < 1)
    }

    @Test("Top pressure maps to top of plot area")
    func topPressure() {
        let t = makeTransform()
        let y = t.pressureToY(t.config.pTop)
        #expect(abs(y - t.plotArea.top) < 1)
    }

    @Test("Pressure roundtrip: p → y → p")
    func pressureRoundtrip() {
        let t = makeTransform()
        let p: Double = 500
        let y = t.pressureToY(p)
        let pBack = t.yToPressure(y)
        #expect(abs(pBack - p) < 0.1)
    }

    @Test("Temperature roundtrip: T → x → T at same pressure")
    func temperatureRoundtrip() {
        let t = makeTransform()
        let temp: Double = 10
        let p: Double = 700
        let x = t.temperatureToX(temp, atPressure: p)
        let tBack = t.xToTemperature(x, atPressure: p)
        #expect(abs(tBack - temp) < 0.01)
    }

    @Test("Isotherms are skewed: same T at lower pressure maps further right")
    func skewDirection() {
        let t = makeTransform()
        let temp: Double = 0
        let x1000 = t.temperatureToX(temp, atPressure: 1000)
        let x500 = t.temperatureToX(temp, atPressure: 500)
        #expect(x500 > x1000) // skew pushes right at lower pressure
    }

    @Test("Visible pressure levels are within configured range")
    func visibleLevels() {
        let t = makeTransform()
        for p in t.visiblePressureLevels {
            #expect(p >= t.config.pTop)
            #expect(p <= t.config.pBottom)
        }
    }

    @Test("Point helper returns correct CGPoint")
    func pointHelper() {
        let t = makeTransform()
        let pt = t.point(tempC: 0, pressureHPa: 500)
        let x = t.temperatureToX(0, atPressure: 500)
        let y = t.pressureToY(500)
        #expect(abs(pt.x - x) < 0.001)
        #expect(abs(pt.y - y) < 0.001)
    }
}
