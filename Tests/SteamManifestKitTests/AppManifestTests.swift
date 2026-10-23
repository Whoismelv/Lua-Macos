import Foundation
import XCTest
@testable import SteamManifestKit

final class AppManifestTests: XCTestCase {
    func testMapsManifestFields() throws {
        let rawText = """
        "AppState"
        {
            "appid" "400"
            "Universe" "1"
            "name" "Portal"
            "StateFlags" "4"
            "installdir" "Portal"
            "LastUpdated" "1700000000"
            "UpdateResult" "0"
            "SizeOnDisk" "536870912"
            "buildid" "14328459"
            "BytesToDownload" "0"
            "BytesDownloaded" "0"
            "UserConfig"
            {
                "language" "english"
            }
            "InstalledDepots"
            {
                "401"
                {
                    "manifest" "6777399203159127119"
                }
            }
            "MountedDepots"
            {
                "401" "6777399203159127119"
            }
        }
        """

        let fileURL = URL(fileURLWithPath: "/tmp/steamapps/appmanifest_400.acf")
        let manifest = try AppManifest(fileURL: fileURL, rawText: rawText)

        XCTAssertEqual(manifest.appID, 400)
        XCTAssertEqual(manifest.name, "Portal")
        XCTAssertEqual(manifest.installDirectory, "Portal")
        XCTAssertEqual(manifest.stateFlags, 4)
        XCTAssertEqual(manifest.buildID, 14328459)
        XCTAssertEqual(manifest.sizeOnDisk, 536_870_912)
        XCTAssertEqual(
            manifest.lastUpdated,
            Date(timeIntervalSince1970: 1_700_000_000)
        )
        XCTAssertEqual(manifest.language, "english")
        XCTAssertEqual(manifest.installedDepots.count, 1)
        XCTAssertEqual(
            manifest.installedDepots.first?.manifestID,
            "6777399203159127119"
        )
        XCTAssertEqual(
            manifest.mountedDepots["401"],
            "6777399203159127119"
        )
        XCTAssertTrue(manifest.isFullyInstalled)
        XCTAssertEqual(manifest.stateDescription, "Fully installed")
        XCTAssertTrue(manifest.warnings.isEmpty)
    }

    func testFallsBackToFileNameForAppID() throws {
        let rawText = """
        "AppState"
        {
            "name" "Missing AppID"
            "StateFlags" "4"
            "installdir" "MissingAppID"
            "buildid" "1"
            "SizeOnDisk" "1"
            "InstalledDepots"
            {
                "1" { "manifest" "2" }
            }
        }
        """

        let fileURL = URL(fileURLWithPath: "/tmp/steamapps/appmanifest_730.acf")
        let manifest = try AppManifest(fileURL: fileURL, rawText: rawText)

        XCTAssertEqual(manifest.appID, 730)
        XCTAssertEqual(manifest.name, "Missing AppID")
    }

    func testCollectsStructuralWarnings() throws {
        let rawText = """
        "AppState"
        {
            "appid" "10"
            "StateFlags" "0"
        }
        """

        let fileURL = URL(fileURLWithPath: "/tmp/steamapps/appmanifest_10.acf")
        let manifest = try AppManifest(fileURL: fileURL, rawText: rawText)

        XCTAssertTrue(manifest.warnings.contains("Missing installdir"))
        XCTAssertTrue(manifest.warnings.contains("Missing buildid"))
        XCTAssertTrue(manifest.warnings.contains("No InstalledDepots section"))
        XCTAssertEqual(manifest.stateDescription, "Unknown state")
    }

    func testThrowsWhenAppStateIsMissing() {
        let rawText = """
        "NotAppState"
        {
            "appid" "10"
        }
        """

        let fileURL = URL(fileURLWithPath: "/tmp/steamapps/appmanifest_10.acf")

        XCTAssertThrowsError(
            try AppManifest(fileURL: fileURL, rawText: rawText)
        ) { error in
            XCTAssertEqual(
                error as? ManifestParseError,
                .missingAppState
            )
        }
    }
}
