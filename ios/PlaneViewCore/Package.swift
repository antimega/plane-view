// swift-tools-version:5.9
import PackageDescription

// The part of the companion app that has no screen: reading a flight pack, working out where the aircraft is, and
// what can be seen from it. It builds on macOS too, so `pvcore` can replay a flight on the Mac and the Python tests
// can compare its answers with planeview's own.
let package = Package(
    name: "PlaneViewCore",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [
        .library(name: "PlaneViewCore", targets: ["PlaneViewCore"]),
        .executable(name: "pvcore", targets: ["pvcore"]),
    ],
    targets: [
        // Always optimised, even in the app's Debug builds: working out every sighting along a path is a second
        // or two unoptimised and a twentieth of that optimised, and that is the wait before the list appears.
        .target(name: "PlaneViewCore", swiftSettings: [.unsafeFlags(["-O"])]),
        .executableTarget(name: "pvcore", dependencies: ["PlaneViewCore"]),
    ]
)
