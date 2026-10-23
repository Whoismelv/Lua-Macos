import Foundation
import SwiftUI
import UniformTypeIdentifiers

enum Format {
    private static let bytesFormatter: ByteCountFormatter = {
        let formatter = ByteCountFormatter()
        formatter.countStyle = .file
        return formatter
    }()

    private static let dateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter
    }()

    private static let backupDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return formatter
    }()

    static func bytes(_ value: Int64) -> String {
        bytesFormatter.string(fromByteCount: value)
    }

    static func date(_ value: Date?) -> String {
        guard let value else {
            return "—"
        }
        return dateFormatter.string(from: value)
    }

    static func backupTimestamp(_ value: Date = Date()) -> String {
        backupDateFormatter.string(from: value)
    }
}

struct ManifestJSONDocument: FileDocument {
    static var readableContentTypes: [UTType] {
        [.json]
    }

    enum DocumentError: LocalizedError {
        case missingContents

        var errorDescription: String? {
            switch self {
            case .missingContents:
                return "The selected file has no readable contents."
            }
        }
    }

    var text: String

    init(text: String) {
        self.text = text
    }

    init(configuration: ReadConfiguration) throws {
        guard let data = configuration.file.regularFileContents else {
            throw DocumentError.missingContents
        }

        text = String(decoding: data, as: UTF8.self)
    }

    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        FileWrapper(regularFileWithContents: Data(text.utf8))
    }
}
