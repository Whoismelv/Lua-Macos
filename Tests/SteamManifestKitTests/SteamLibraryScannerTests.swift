import Foundation
import XCTest
@testable import SteamManifestKit

final class SteamLibraryScannerTests: XCTestCase {
    func testDiscoversExternalLibraryAndScansManifests() throws {
        let fileManager = FileManager.default
        let base = fileManager.temporaryDirectory
            .appendingPathComponent("SteamManifestManagerTests-\(UUID().uuidString)", isDirectory: true)
        let steamRoot = base.appendingPathComponent("Steam", isDirectory: true)
        let externalRoot = base.appendingPathComponent("External", isDirectory: true)
        let steamApps = steamRoot.appendingPathComponent("steamapps", isDirectory: true)
        let externalApps = externalRoot.appendingPathComponent("steamapps", isDirectory: true)

        defer {
            try? fileManager.removeItem(at: base)
        }

        try fileManager.createDirectory(at: steamApps, withIntermediateDirectories: true)
        try fileManager.createDirectory(at: externalApps, withIntermediateDirectories: true)

        let libraryFolders = """
        "libraryfolders"
        {
            "0"
            {
                "path" "\(externalRoot.path)"
                "label" "External"
            }
        }
        """

        try libraryFolders.write(
            to: steamApps.appendingPathComponent("libraryfolders.vdf"),
            atomically: true,
            encoding: .utf8
        )

        try makeManifest(appID: 400, name: "Portal").write(
            to: steamApps.appendingPathComponent("appmanifest_400.acf"),
            atomically: true,
            encoding: .utf8
        )

        try makeManifest(appID: 620, name: "Portal 2").write(
            to: externalApps.appendingPathComponent("appmanifest_620.acf"),
            atomically: true,
            encoding: .utf8
        )

        let result = SteamLibraryScanner.scan(roots: [steamRoot])

        XCTAssertEqual(result.libraries.count, 2)
        XCTAssertEqual(result.manifests.count, 2)
        XCTAssertEqual(
            Set(result.manifests.map(\.appID)),
            Set([400, 620])
        )
        XCTAssertTrue(result.issues.isEmpty)
    }

    func testReportsMissingSteamFolder() {
        let missingRoot = URL(fileURLWithPath: "/definitely/not/a/steam/root")
        let result = SteamLibraryScanner.scan(roots: [missingRoot])

        XCTAssertTrue(result.libraries.isEmpty)
        XCTAssertTrue(result.manifests.isEmpty)
        XCTAssertEqual(result.issues.count, 1)
        XCTAssertTrue(
            result.issues[0].message.contains("No steamapps folder")
        )
    }

    private func makeManifest(appID: Int, name: String) -> String {
        """
        "AppState"
        {
            "appid" "\(appID)"
            "name" "\(name)"
            "StateFlags" "4"
            "installdir" "\(name)"
            "LastUpdated" "1700000000"
            "SizeOnDisk" "1024"
            "buildid" "1"
            "BytesToDownload" "0"
            "BytesDownloaded" "0"
            "UserConfig"
            {
                "language" "english"
            }
            "InstalledDepots"
            {
                "\(appID + 1)"
                {
                    "manifest" "1234567890"
                }
            }
            "MountedDepots"
            {
                "\(appID + 1)" "1234567890"
            }
        }
        """
    }
}
