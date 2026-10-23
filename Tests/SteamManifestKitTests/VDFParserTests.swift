import Foundation
import XCTest
@testable import SteamManifestKit

final class VDFParserTests: XCTestCase {
    func testParsesNestedACF() throws {
        let text = """
        // local Steam manifest
        "AppState"
        {
            "appid" "400"
            "name" "Portal"
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
        }
        """

        let root = try VDFParser.parse(text)
        let appState = try XCTUnwrap(root["AppState"]?.objectValue)

        XCTAssertEqual(appState["appid"]?.stringValue, "400")
        XCTAssertEqual(appState["name"]?.stringValue, "Portal")
        XCTAssertEqual(
            appState["UserConfig"]?.objectValue?["language"]?.stringValue,
            "english"
        )
        XCTAssertEqual(
            appState["InstalledDepots"]?.objectValue?["401"]?.objectValue?["manifest"]?.stringValue,
            "6777399203159127119"
        )
    }

    func testParsesEscapedQuotesAndControlCharacters() throws {
        let text = """
        "Root"
        {
            "name" "Portal \\"The Flash\\""
            "tab" "line\\tbreak"
        }
        """

        let root = try VDFParser.parse(text)
        let object = try XCTUnwrap(root["Root"]?.objectValue)

        XCTAssertEqual(object["name"]?.stringValue, "Portal \"The Flash\"")
        XCTAssertEqual(object["tab"]?.stringValue, "line\tbreak")
    }

    func testParsesBareKeysAndValues() throws {
        let text = """
        Root
        {
            key value
        }
        """

        let root = try VDFParser.parse(text)
        let object = try XCTUnwrap(root["Root"]?.objectValue)

        XCTAssertEqual(object["key"]?.stringValue, "value")
    }

    func testThrowsOnUnterminatedString() {
        let text = "\"unterminated"

        XCTAssertThrowsError(try VDFParser.parse(text)) { error in
            XCTAssertEqual(
                error as? VDFParseError,
                .unterminatedString(position: 0)
            )
        }
    }

    func testThrowsOnMissingClosingBrace() {
        let text = """
        "Root"
        {
            "key" "value"
        """

        XCTAssertThrowsError(try VDFParser.parse(text)) { error in
            XCTAssertEqual(
                error as? VDFParseError,
                .unexpectedEndOfInput(position: 4)
            )
        }
    }
}
