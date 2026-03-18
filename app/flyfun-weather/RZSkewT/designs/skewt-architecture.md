# RZSkewT Architecture

> Native Swift Skew-T log-P diagram renderer with client-side atmospheric thermodynamics

## Intent

Standalone, open-source Swift package for rendering meteorological Skew-T log-P diagrams in SwiftUI. No server dependency — all thermodynamics (dry/moist adiabats, mixing ratios, parcel analysis, CAPE/CIN) computed client-side. Reusable by any aviation weather or meteorology app.

## Architecture

```
RZSkewT/
├── Models/
│   ├── SoundingProfile.swift      # Data types: levels, indices, overlays, result structs
│   └── SkewTConfiguration.swift   # Appearance: axis ranges, colors, margins
├── Transform/
│   ├── SkewTTransform.swift       # Coordinate system: (T,p) ↔ pixels
│   └── Thermodynamics.swift       # Atmospheric physics + standard atmosphere
├── Rendering/
│   ├── SkewTRenderer.swift        # Main orchestrator + axes + indices panel
│   ├── BackgroundLinesRenderer.swift  # Isotherms, adiabats, mixing ratio lines
│   ├── ProfileRenderer.swift      # T/Td curves, parcel path, CAPE/CIN shading
│   └── WindBarbRenderer.swift     # WMO standard wind barbs
└── Views/
    └── SkewTView.swift            # SwiftUI Canvas wrapper + preview + accessibility
```

## Usage Examples

```swift
// Minimal: just a profile
let profile = SoundingProfile(levels: [
    SoundingLevel(pressureHPa: 1000, temperatureC: 20, dewpointC: 15),
    SoundingLevel(pressureHPa: 850,  temperatureC: 8,  dewpointC: 2),
    SoundingLevel(pressureHPa: 700,  temperatureC: -4, dewpointC: -12),
    SoundingLevel(pressureHPa: 500,  temperatureC: -20, dewpointC: -35),
])
SkewTView(profile: profile)
    .aspectRatio(1.0, contentMode: .fit)

// With custom config
let config = SkewTConfiguration(pTop: 250, tMin: -50, tMax: 40)
SkewTView(profile: profile, config: config)

// Direct renderer access (for embedding in existing Canvas)
let renderer = SkewTRenderer(profile: profile)
Canvas { context, size in
    renderer.render(context: &context, size: size)
}

// Thermodynamics engine standalone
let lcl = Thermodynamics.liftingCondensationLevel(tempC: 25, dewpointC: 15, pressureHPa: 1000)
let path = Thermodynamics.parcelPath(surfaceTempC: 25, surfaceDewpointC: 15, surfacePressureHPa: 1000)
let result = Thermodynamics.computeCAPECIN(environmentLevels: levels, parcelPath: path)
```

## Key Choices

**Canvas rendering (not Swift Charts)**: The skewed coordinate system (isotherms tilted 45°, log-pressure Y-axis) doesn't fit any standard chart framework. Canvas gives full pixel control.

**Client-side thermodynamics**: All computations (moist adiabats, parcel path, CAPE/CIN) run in Swift. No server dependency. Moist adiabat integration uses RK2 (midpoint method) for accuracy. Background lines are computed once at init and cached.

**Proper result types**: Public API uses named structs (`AtmosphericPoint`, `LCLResult`, `CAPECINResult`, `MixingRatioLine`) instead of tuples for protocol conformance and documentation.

**Virtual temperature in CAPE/CIN**: Uses Tv = T(1 + 0.61w) for both parcel and environment in buoyancy integration. More accurate than dry-temperature-only approach, especially for moist tropical soundings.

**Axis ranges match MetPy defaults**: pBottom=1050, pTop=100 hPa, T=-40 to +50°C, skew angle=45°. FL labels on right axis via standard atmosphere.

**Protocol conformances**: All model types conform to `Sendable`, `Equatable`, `Hashable`, and `Codable` for Swift 6 concurrency safety, SwiftUI diffing, and JSON serialization.

## Coordinate Transform

The Skew-T log-P coordinate system:

- **Y-axis** (pressure → vertical): `y = plotArea.bottom - fraction * height` where `fraction = (ln(pBottom) - ln(p)) / (ln(pBottom) - ln(pTop))`. Higher pressure = bottom.
- **X-axis** (temperature, skewed): `x = plotArea.left + (normalizedT + skewOffset) * width` where `skewOffset = logFraction * tan(skewAngle)`. Isotherms tilt right at lower pressures.
- **Inverse transforms**: `yToPressure()`, `xToTemperature()` for hit-testing.

## Thermodynamics

Key computations in `Thermodynamics.swift` (all use Bolton 1980 Magnus constants):

| Function | Purpose | Used For |
|----------|---------|----------|
| `saturationVaporPressure` | es(T) via Magnus | Mixing ratio, LCL |
| `saturationMixingRatio` | ws(T, p) | Dewpoint lines, LCL |
| `potentialTemperature` | θ(T, p) | Dry adiabats |
| `moistLapseRate` | dT/dp for saturated ascent | Moist adiabats, parcel path |
| `liftingCondensationLevel` | LCL (p, T) with interpolation | Parcel analysis |
| `parcelPath` | Surface → LCL (dry) → top (moist) | CAPE/CIN shading |
| `computeCAPECIN` | Trapezoidal buoyancy with Tv | Indices panel |
| `pressureToAltitude` | Barometric formula p→ft | FL labels, overlays |
| `altitudeToPressure` | Barometric formula ft→p | Freezing level marker |
| `interpolateEnvironment` | Log-linear T interpolation | CAPE/CIN, shading |

Background lines precomputed at init: ~10 dry adiabats (θ 250-450K, step 20K), ~14 moist adiabats (-30 to +35°C, step 5°C), 8 mixing ratio lines, isotherms every 10°C.

## Rendering Pipeline

`SkewTRenderer.render()` draws in order (back to front):
1. Background fill
2. **Clipped to plot area**: isotherms, 0°C highlight, dry adiabats, moist adiabats (dashed), mixing ratio lines with labels, LCL/LFC/EL/freezing level markers (dashed lines with label pills), CAPE/CIN shading, parcel path (dashed black), T profile (red), Td profile (green)
3. **Outside clip**: axes (hPa left, FL right, °C bottom), wind barbs (right column), indices text panel (top-right)

## Gotchas

- `GraphicsContext` clipping uses a copy (`var clipped = context`) — not `clipToLayer` which doesn't composite correctly on iOS.
- Moist adiabat `moistLapseRate` works entirely in hPa — no Pa conversion needed.
- `SoundingLevel.dewpointC` is optional — profiles without dewpoint still render T curve and wind barbs.
- Wind barbs use meteorological convention: direction is where wind blows FROM.
- Default colors are optimized for light mode. Pass custom `SkewTConfiguration` for dark mode.
- Tests are pinned against textbook values (Stull 2000 LCL, tropical CAPE range) — changes to thermodynamic code should pass all 47 tests.

## References

- Key code: `Sources/RZSkewT/Transform/Thermodynamics.swift` (physics), `Sources/RZSkewT/Rendering/SkewTRenderer.swift` (orchestrator)
- MetPy reference implementation: Python atmospheric science library
- Bolton, D. (1980). "The Computation of Equivalent Potential Temperature." *Monthly Weather Review*, 108(7).
