// swift-tools-version: 5.9
import PackageDescription

// Apple ships CryptoKit; Linux uses the equivalent swift-crypto module.
#if os(Linux)
let cryptoPackages: [Package.Dependency] = [.package(url: "https://github.com/apple/swift-crypto.git", from: "3.0.0")]
let cryptoTargets: [Target.Dependency] = [.product(name: "Crypto", package: "swift-crypto")]
#else
let cryptoPackages: [Package.Dependency] = []
let cryptoTargets: [Target.Dependency] = []
#endif

let package = Package(
    name: "PersonalWatchCore",
    platforms: [
        .iOS(.v17),
        .watchOS(.v10),
        .macOS(.v13)
    ],
    products: [
        .library(name: "PersonalWatchCore", targets: ["PersonalWatchCore"])
    ],
    dependencies: cryptoPackages,
    targets: [
        .target(
            name: "PersonalWatchCore",
            dependencies: cryptoTargets
        ),
        .testTarget(
            name: "PersonalWatchCoreTests",
            dependencies: ["PersonalWatchCore"]
        )
    ]
)
