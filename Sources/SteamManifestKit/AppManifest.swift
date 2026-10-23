import Foundation

public enum ManifestParseError: LocalizedError, Equatable {
    case missingAppState
    case missingAppID

    public var errorDescription: String? {
        switch self {
        case .missingAppState:
            return "The ACF file does not contain an AppState object."
        case .missingAppID:
            return "The ACF file does not contain a valid appid."
        }
    }
}

public struct SteamDepot: Identifiable, Hashable, Codable {
    public let depotID: String
    public let manifestID: String
    public let dlcAppID: Int?

    public var id: String {
        depotID
    }

    public init(depotID: String, manifestID: String, dlcAppID: Int? = nil) {
        self.depotID = depotID
        self.manifestID = manifestID
        self.dlcAppID = dlcAppID
    }
}

public struct AppManifest: Identifiable, Hashable, Codable {
    public let fileURL: URL
    public let appID: Int
    public let name: String
    public let installDirectory: String
    public let stateFlags: Int
    public let buildID: Int?
    public let sizeOnDisk: Int64
    public let lastUpdated: Date?
    public let bytesToDownload: Int64
    public let bytesDownloaded: Int64
    public let language: String?
    public let installedDepots: [SteamDepot]
    public let mountedDepots: [String: String]
    public let rawText: String
    public let warnings: [String]

    public var id: String {
        fileURL.path
    }

    public var isFullyInstalled: Bool {
        stateFlags & 4 != 0
    }

    public var libraryDisplayName: String {
        fileURL
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .lastPathComponent
    }

    private static let stateFlagNames: [(flag: Int, name: String)] = [
        (1, "Uninstalled"),
        (2, "Update required"),
        (4, "Fully installed"),
        (8, "Update queued"),
        (16, "Update optional"),
        (32, "Files missing"),
        (64, "Shared only"),
        (128, "Files corrupt"),
        (256, "Update running"),
        (512, "Update paused"),
        (1024, "Update started"),
        (2048, "Uninstalling"),
        (4096, "Backup running"),
        (65536, "Reconfiguring"),
        (131072, "Validating"),
        (262144, "Adding files"),
        (524288, "Preallocating"),
        (1048576, "Downloading"),
        (2097152, "Staging"),
        (4194304, "Committing"),
        (8388608, "Update stopping")
    ]

    public var stateDescription: String {
        let states = Self.stateFlagNames.compactMap { flag, name in
            stateFlags & flag != 0 ? name : nil
        }

        if states.isEmpty {
            return stateFlags == 0 ? "Unknown state" : "Raw flags \(stateFlags)"
        }

        return states.joined(separator: " · ")
    }

    public init(fileURL: URL, rawText: String) throws {
        let root = try VDFParser.parse(rawText)

        guard let appState = root["AppState"] ?? root.first(where: {
            $0.key.caseInsensitiveCompare("AppState") == .orderedSame
        })?.value?.objectValue else {
            throw ManifestParseError.missingAppState
        }

        let state = VDFValue.object(appState)

        func nestedString(_ dictionary: [String: VDFValue], _ key: String) -> String? {
            if let value = dictionary[key]?.stringValue {
                return value
            }

            return dictionary.first {
                $0.key.caseInsensitiveCompare(key) == .orderedSame
            }?.value.stringValue
        }

        let appIDText = state.string(forKey: "appid")
        let parsedAppID = appIDText.flatMap(Int.init)
        let fileNameStem = fileURL
            .deletingPathExtension()
            .lastPathComponent
            .replacingOccurrences(of: "appmanifest_", with: "")
        let fileNameAppID = Int(fileNameStem)

        guard let appID = parsedAppID ?? fileNameAppID else {
            throw ManifestParseError.missingAppID
        }

        let name = state.string(forKey: "name") ?? ""
        let installDirectory = state.string(forKey: "installdir") ?? ""
        let stateFlags = state.string(forKey: "StateFlags").flatMap(Int.init) ?? 0
        let buildID = state.string(forKey: "buildid").flatMap(Int.init)
        let sizeOnDisk = state.string(forKey: "SizeOnDisk").flatMap(Int64.init) ?? 0
        let bytesToDownload = state.string(forKey: "BytesToDownload").flatMap(Int64.init) ?? 0
        let bytesDownloaded = state.string(forKey: "BytesDownloaded").flatMap(Int64.init) ?? 0
        let lastUpdated = state
            .string(forKey: "LastUpdated")
            .flatMap(Double.init)
            .map(Date.init(timeIntervalSince1970:))

        let userConfig = state.object(forKey: "UserConfig") ?? [:]
        let language = nestedString(userConfig, "language")

        var installedDepots: [SteamDepot] = []
        if let depots = state.object(forKey: "InstalledDepots") {
            for (depotID, value) in depots {
                guard let depotObject = value.objectValue else {
                    continue
                }

                let manifestID = nestedString(depotObject, "manifest") ?? ""
                let dlcAppID = nestedString(depotObject, "dlcappid").flatMap(Int.init)

                installedDepots.append(
                    SteamDepot(
                        depotID: depotID,
                        manifestID: manifestID,
                        dlcAppID: dlcAppID
                    )
                )
            }
        }

        installedDepots.sort { lhs, rhs in
            let lhsID = Int(lhs.depotID) ?? .max
            let rhsID = Int(rhs.depotID) ?? .max
            return lhsID < rhsID
        }

        var mountedDepots: [String: String] = [:]
        if let depots = state.object(forKey: "MountedDepots") {
            for (depotID, value) in depots {
                if let manifestID = value.stringValue {
                    mountedDepots[depotID] = manifestID
                }
            }
        }

        var warnings: [String] = []

        if name.isEmpty {
            warnings.append("Missing name")
        }

        if installDirectory.isEmpty {
            warnings.append("Missing installdir")
        }

        if buildID == nil {
            warnings.append("Missing buildid")
        }

        if installedDepots.isEmpty {
            warnings.append("No InstalledDepots section")
        }

        for depot in installedDepots where depot.manifestID.isEmpty {
            warnings.append("Depot \(depot.depotID) has no manifest ID")
        }

        if bytesDownloaded < bytesToDownload {
            warnings.append("Download incomplete")
        }

        self.fileURL = fileURL
        self.appID = appID
        self.name = name.isEmpty ? "App \(appID)" : name
        self.installDirectory = installDirectory
        self.stateFlags = stateFlags
        self.buildID = buildID
        self.sizeOnDisk = sizeOnDisk
        self.lastUpdated = lastUpdated
        self.bytesToDownload = bytesToDownload
        self.bytesDownloaded = bytesDownloaded
        self.language = language
        self.installedDepots = installedDepots
        self.mountedDepots = mountedDepots
        self.rawText = rawText
        self.warnings = warnings
    }
}
