import Foundation

public struct SteamLibrary: Identifiable, Hashable, Codable {
    public let rootURL: URL
    public let steamappsURL: URL

    public var id: String {
        steamappsURL.path
    }

    public var displayName: String {
        rootURL.lastPathComponent
    }

    public init(rootURL: URL, steamappsURL: URL) {
        self.rootURL = rootURL.standardizedFileURL
        self.steamappsURL = steamappsURL.standardizedFileURL
    }
}

public struct ScanIssue: Identifiable, Hashable, Codable {
    public let id: String
    public let message: String
    public let fileURL: URL?

    public init(message: String, fileURL: URL? = nil) {
        self.id = "\(fileURL?.path ?? "")|\(message)"
        self.message = message
        self.fileURL = fileURL
    }
}

public struct ScanResult {
    public let libraries: [SteamLibrary]
    public let manifests: [AppManifest]
    public let issues: [ScanIssue]

    public init(
        libraries: [SteamLibrary],
        manifests: [AppManifest],
        issues: [ScanIssue]
    ) {
        self.libraries = libraries
        self.manifests = manifests
        self.issues = issues
    }
}

public enum SteamLibraryScanner {
    public static func defaultRoots() -> [URL] {
        [
            FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Application Support/Steam", isDirectory: true)
        ]
    }

    public static func scan(roots: [URL]) -> ScanResult {
        var libraries: [SteamLibrary] = []
        var issues: [ScanIssue] = []
        var seenLibraries = Set<String>()

        for root in roots {
            let discovery = discoverLibraries(root: root)
            issues.append(contentsOf: discovery.issues)

            for library in discovery.libraries where !seenLibraries.contains(library.id) {
                seenLibraries.insert(library.id)
                libraries.append(library)
            }
        }

        var manifests: [AppManifest] = []

        for library in libraries {
            do {
                let files = try FileManager.default.contentsOfDirectory(
                    at: library.steamappsURL,
                    includingPropertiesForKeys: nil,
                    options: [.skipsHiddenFiles]
                )

                for file in files {
                    let lowercasedName = file.lastPathComponent.lowercased()
                    guard file.pathExtension.lowercased() == "acf",
                          lowercasedName.hasPrefix("appmanifest_") else {
                        continue
                    }

                    do {
                        let rawText = try String(contentsOf: file, encoding: .utf8)
                        let manifest = try AppManifest(fileURL: file, rawText: rawText)
                        manifests.append(manifest)
                    } catch {
                        issues.append(
                            ScanIssue(
                                message: "Could not parse \(file.lastPathComponent): \(error.localizedDescription)",
                                fileURL: file
                            )
                        )
                    }
                }
            } catch {
                issues.append(
                    ScanIssue(
                        message: "Could not read \(library.steamappsURL.path): \(error.localizedDescription)",
                        fileURL: library.steamappsURL
                    )
                )
            }
        }

        if libraries.isEmpty {
            for root in roots {
                issues.append(
                    ScanIssue(
                        message: "No steamapps folder found under \(root.path)"
                    )
                )
            }
        }

        manifests.sort { lhs, rhs in
            let nameOrder = lhs.name.localizedCaseInsensitiveCompare(rhs.name)
            if nameOrder == .orderedSame {
                return lhs.appID < rhs.appID
            }
            return nameOrder == .orderedAscending
        }

        libraries.sort {
            $0.displayName.localizedCaseInsensitiveCompare($1.displayName) == .orderedAscending
        }

        return ScanResult(
            libraries: libraries,
            manifests: manifests,
            issues: issues
        )
    }

    public static func discoverLibraries(root: URL) -> (libraries: [SteamLibrary], issues: [ScanIssue]) {
        var libraries: [SteamLibrary] = []
        var issues: [ScanIssue] = []
        var seen = Set<String>()

        func addLibrary(root libraryRoot: URL) {
            let steamapps = libraryRoot.appendingPathComponent("steamapps", isDirectory: true)
            guard FileManager.default.fileExists(atPath: steamapps.path) else {
                return
            }

            let library = SteamLibrary(
                rootURL: libraryRoot,
                steamappsURL: steamapps
            )

            guard !seen.contains(library.id) else {
                return
            }

            seen.insert(library.id)
            libraries.append(library)
        }

        addLibrary(root: root)

        let libraryFoldersFile = root
            .appendingPathComponent("steamapps", isDirectory: true)
            .appendingPathComponent("libraryfolders.vdf")

        guard FileManager.default.fileExists(atPath: libraryFoldersFile.path) else {
            return (libraries, issues)
        }

        do {
            let rawText = try String(contentsOf: libraryFoldersFile, encoding: .utf8)
            let parsed = try VDFParser.parse(rawText)

            let folders = parsed["libraryfolders"]
                ?? parsed["LibraryFolders"]
                ?? parsed.first(where: {
                    $0.key.caseInsensitiveCompare("libraryfolders") == .orderedSame
                })?.value

            guard let folderObject = folders?.objectValue else {
                return (libraries, issues)
            }

            for (key, value) in folderObject {
                switch value {
                case .string(let path):
                    guard Int(key) != nil else {
                        continue
                    }

                    addLibrary(root: normalizedURL(from: path))

                case .object(let object):
                    let path = nestedString(in: object, key: "path")
                        ?? nestedString(in: object, key: "Path")

                    if let path {
                        addLibrary(root: normalizedURL(from: path))
                    }
                }
            }
        } catch {
            issues.append(
                ScanIssue(
                    message: "Could not parse \(libraryFoldersFile.lastPathComponent): \(error.localizedDescription)",
                    fileURL: libraryFoldersFile
                )
            )
        }

        return (libraries, issues)
    }

    private static func nestedString(
        in dictionary: [String: VDFValue],
        key: String
    ) -> String? {
        if let value = dictionary[key]?.stringValue {
            return value
        }

        return dictionary.first {
            $0.key.caseInsensitiveCompare(key) == .orderedSame
        }?.value.stringValue
    }

    private static func normalizedURL(from rawPath: String) -> URL {
        if rawPath.hasPrefix("file://"),
           let url = URL(string: rawPath) {
            return url.standardizedFileURL
        }

        let expandedPath = NSString(string: rawPath).expandingTildeInPath
        return URL(fileURLWithPath: expandedPath).standardizedFileURL
    }
}
