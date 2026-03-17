// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "RZSkewT",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [
        .library(name: "RZSkewT", targets: ["RZSkewT"]),
    ],
    targets: [
        .target(name: "RZSkewT"),
        .testTarget(name: "RZSkewTTests", dependencies: ["RZSkewT"]),
    ]
)
