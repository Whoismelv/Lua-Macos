// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "SteamManifestManager",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "SteamManifestManager", targets: ["SteamManifestManager"]),
        .library(name: "SteamManifestKit", targets: ["SteamManifestKit"])
    ],
    targets: [
        .target(name: "SteamManifestKit"),
        .executableTarget(
            name: "SteamManifestManager",
            dependencies: ["SteamManifestKit"]
        ),
        .testTarget(
            name: "SteamManifestKitTests",
            dependencies: ["SteamManifestKit"]
        )
    ]
)
