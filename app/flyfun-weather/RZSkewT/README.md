# RZSkewT

A Swift package for rendering interactive [Skew-T log-P diagrams](https://en.wikipedia.org/wiki/Skew-T_log-P_diagram) — the standard atmospheric sounding chart used in meteorology and aviation weather briefings.

## Features

- **Full thermodynamic engine** — saturation vapor pressure (Bolton/Magnus), potential temperature, dry & moist adiabats, mixing ratio lines, LCL, parcel path, CAPE/CIN with virtual temperature correction
- **Standard Skew-T rendering** — isotherms, isobars, dry/moist adiabats, mixing ratio lines, temperature & dewpoint profiles, WMO wind barbs, CAPE/CIN shading
- **Pure SwiftUI** — renders via `Canvas` with no UIKit or external dependencies
- **Fully configurable** — axis ranges, colors, line widths, margins all injectable via `SkewTConfiguration`
- **Swift 6 ready** — all types are `Sendable`; models are `Equatable`, `Hashable`, and `Codable`
- **Zero dependencies** — only SwiftUI + Foundation

## Requirements

- iOS 18+ / macOS 15+
- Swift 6.0+

## Installation

Add RZSkewT to your project via Swift Package Manager:

```swift
// Package.swift
dependencies: [
    .package(url: "https://github.com/roznet/RZSkewT.git", from: "1.0.0"),
]
```

Or in Xcode: File > Add Package Dependencies, paste the repository URL.

## Usage

### Basic

```swift
import RZSkewT
import SwiftUI

let profile = SoundingProfile(levels: [
    SoundingLevel(pressureHPa: 1000, temperatureC: 28, dewpointC: 20,
                  windSpeedKt: 5, windDirectionDeg: 180),
    SoundingLevel(pressureHPa: 850, temperatureC: 16, dewpointC: 10,
                  windSpeedKt: 20, windDirectionDeg: 220),
    SoundingLevel(pressureHPa: 700, temperatureC: 2, dewpointC: -8,
                  windSpeedKt: 35, windDirectionDeg: 250),
    SoundingLevel(pressureHPa: 500, temperatureC: -15, dewpointC: -30,
                  windSpeedKt: 50, windDirectionDeg: 270),
    SoundingLevel(pressureHPa: 300, temperatureC: -42, dewpointC: -55,
                  windSpeedKt: 60, windDirectionDeg: 280),
])

struct ContentView: View {
    var body: some View {
        SkewTView(profile: profile)
            .frame(minWidth: 400, minHeight: 500)
    }
}
```

### With indices and custom configuration

```swift
let indices = SkewTIndices(
    lclPressureHPa: 880,
    capeSurfaceJkg: 1250,
    cinSurfaceJkg: -45,
    freezingLevelFt: 12500,
    liftedIndex: -4.2
)

let profile = SoundingProfile(levels: levels, indices: indices)

let config = SkewTConfiguration(
    pTop: 200,
    temperatureColor: .red,
    dewpointColor: .blue
)

SkewTView(profile: profile, config: config)
```

### Using the thermodynamics engine directly

```swift
// Compute LCL
let lcl = Thermodynamics.liftingCondensationLevel(
    tempC: 25, dewpointC: 15, pressureHPa: 1000)
// lcl?.pressureHPa ≈ 860

// Compute parcel path
let path = Thermodynamics.parcelPath(
    surfaceTempC: 25, surfaceDewpointC: 15,
    surfacePressureHPa: 1000)

// Compute CAPE/CIN
let result = Thermodynamics.computeCAPECIN(
    environmentLevels: levels, parcelPath: path)
// result.cape, result.cin
```

### Dark mode

The default colors are optimized for light backgrounds. For dark mode, provide a custom configuration:

```swift
let darkConfig = SkewTConfiguration(
    backgroundColor: Color(.sRGB, red: 0.12, green: 0.12, blue: 0.15),
    panelBackgroundColor: .black.opacity(0.7),
    isothermColor: .gray.opacity(0.2),
    dryAdiabatColor: .red.opacity(0.15),
    moistAdiabatColor: .green.opacity(0.15),
    mixingRatioColor: .blue.opacity(0.15)
)
```

## Architecture

```
Sources/RZSkewT/
├── Models/
│   ├── SoundingProfile.swift    — Data types: levels, indices, overlays
│   └── SkewTConfiguration.swift — Appearance and axis configuration
├── Transform/
│   ├── SkewTTransform.swift     — (T,p) ↔ pixel coordinate mapping
│   └── Thermodynamics.swift     — Physical computations (adiabats, LCL, CAPE)
├── Rendering/
│   ├── SkewTRenderer.swift      — Main render orchestrator
│   ├── BackgroundLinesRenderer.swift — Grid lines (isotherms, adiabats)
│   ├── ProfileRenderer.swift    — T/Td profiles, parcel path, CAPE shading
│   └── WindBarbRenderer.swift   — WMO standard wind barbs
└── Views/
    └── SkewTView.swift          — SwiftUI Canvas wrapper
```

## References

- Bolton, D. (1980). "The Computation of Equivalent Potential Temperature." *Monthly Weather Review*, 108(7), 1046–1053.
- [MetPy](https://unidata.github.io/MetPy/) — Python atmospheric science library (reference implementation)
- [WMO Manual on Codes](https://library.wmo.int/) — Wind barb specification

## License

MIT — see [LICENSE](LICENSE).
