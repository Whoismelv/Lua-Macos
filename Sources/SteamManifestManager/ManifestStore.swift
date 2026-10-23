import AppKit
import Combine
import Foundation
import SteamManifestKit

final class ManifestStore: ObservableObject {
    @Published private(set) var manifests: [AppManifest] = []
    @Published private(set) var libraries: [SteamLibrary] = []
    @Published private(set) var issues: [ScanIssue] = []
    @Published var selectedID: String?
    @Published var searchText = ""
    @Published private(set) var isScanning = false
    @Published private(set) var status = "Ready"
    @Published private(set) var lastScan: Date?
    @Published private(set) var customRoot: URL?

    var selectedManifest: AppManifest? {
        guard let selectedID else {
            return nil
        }
        return manifests.first { $0.id == selectedID }
    }

    var filteredManifests: [AppManifest] {
        let query = searchText
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()

        if query.isEmpty {
            return manifests
        }

        return manifests.filter { manifest in
            manifest.name.lowercased().contains(query)
                || String(manifest.appID).contains(query)
                || manifest.installDirectory.lowercased().contains(query)
                || manifest.libraryDisplayName.lowercased().contains(query)
        }
    }

    var exportDocument: ManifestJSONDocument? {
        let source = filteredManifests

        guard !source.isEmpty else {
            status = "Nothing to export"
            return nil
        }

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        encoder.dateEncodingStrategy = .iso8601

        do {
            let data = try encoder.encode(source)
            return ManifestJSONDocument(
                text: String(decoding: data, as: UTF8.self)
            )
        } catch {
            status = "Export failed: \(error.localizedDescription)"
            return nil
        }
    }

    func libraryCount(for library: SteamLibrary) -> Int {
        manifests.filter {
            $0.fileURL
                .deletingLastPathComponent()
                .standardizedFileURL
                .path == library.steamappsURL.path
        }.count
    }

    func scan(root: URL? = nil) {
        if let root {
            customRoot = root
        }

        let roots: [URL]
        if let root {
            roots = [root]
        } else if let customRoot {
            roots = [customRoot]
        } else {
            roots = SteamLibraryScanner.defaultRoots()
        }

        isScanning = true
        status = "Scanning Steam libraries…"

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let result = SteamLibraryScanner.scan(roots: roots)

            DispatchQueue.main.async { [weak self] in
                guard let self else {
                    return
                }

                self.libraries = result.libraries
                self.manifests = result.manifests
                self.issues = result.issues
                self.isScanning = false
                self.lastScan = Date()

                if result.issues.isEmpty {
                    self.status = "Found \(result.manifests.count) manifests across \(result.libraries.count) libraries"
                } else {
                    self.status = "Found \(result.manifests.count) manifests · \(result.issues.count) issues"
                }

                if let selectedID = self.selectedID,
                   result.manifests.contains(where: { $0.id == selectedID }) {
                    return
                }

                self.selectedID = result.manifests.first?.id
            }
        }
    }

    func chooseCustomRoot() {
        let panel = NSOpenPanel()
        panel.title = "Choose Steam Installation Folder"
        panel.prompt = "Scan"
        panel.message = "Select the folder that contains steamapps."
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.showsHiddenFiles = false

        guard panel.runModal() == .OK, let url = panel.url else {
            return
        }

        scan(root: url)
    }

    func backupSelected() {
        guard let manifest = selectedManifest else {
            status = "Select a manifest first"
            return
        }

        let panel = NSOpenPanel()
        panel.title = "Choose Backup Folder"
        panel.prompt = "Back Up"
        panel.message = "The original ACF file will be copied without modification."
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false

        guard panel.runModal() == .OK, let directory = panel.url else {
            return
        }

        do {
            let destination = try backup(manifest, to: directory)
            status = "Backed up to \(destination.lastPathComponent)"
        } catch {
            status = "Backup failed: \(error.localizedDescription)"
        }
    }

    func revealSelected() {
        guard let manifest = selectedManifest else {
            status = "Select a manifest first"
            return
        }

        NSWorkspace.shared.activateFileViewerSelecting([manifest.fileURL])
    }

    func copySelectedRaw() {
        guard let manifest = selectedManifest else {
            status = "Select a manifest first"
            return
        }

        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(manifest.rawText, forType: .string)
        status = "Copied raw ACF for \(manifest.name)"
    }

    func recordExport(_ result: Result<URL, Error>) {
        switch result {
        case .success(let url):
            status = "Exported \(url.lastPathComponent)"
        case .failure(let error):
            status = "Export failed: \(error.localizedDescription)"
        }
    }

    private func backup(
        _ manifest: AppManifest,
        to directory: URL
    ) throws -> URL {
        let baseName = "appmanifest_\(manifest.appID).acf"
        var destination = directory.appendingPathComponent(baseName)

        if FileManager.default.fileExists(atPath: destination.path) {
            destination = directory.appendingPathComponent(
                "appmanifest_\(manifest.appID)_\(Format.backupTimestamp()).acf"
            )
        }

        try FileManager.default.copyItem(
            at: manifest.fileURL,
            to: destination
        )

        return destination
    }
}
