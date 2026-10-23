import SteamManifestKit
import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: ManifestStore
    @State private var exportDocument: ManifestJSONDocument?
    @State private var isExporting = false

    var body: some View {
        NavigationSplitView {
            sidebar
        } detail: {
            detail
        }
        .searchable(
            text: $store.searchText,
            placement: .sidebar,
            prompt: "Name, AppID, folder"
        )
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button {
                    store.scan()
                } label: {
                    Label("Scan", systemImage: "arrow.clockwise")
                }
                .keyboardShortcut("r", modifiers: .command)
                .disabled(store.isScanning)

                Button {
                    store.chooseCustomRoot()
                } label: {
                    Label("Choose Folder", systemImage: "folder")
                }
                .disabled(store.isScanning)

                Button {
                    exportDocument = store.exportDocument
                    isExporting = exportDocument != nil
                } label: {
                    Label("Export JSON", systemImage: "square.and.arrow.up")
                }
                .disabled(store.filteredManifests.isEmpty)
            }

            ToolbarItem(placement: .status) {
                Text(store.status)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .fileExporter(
            isPresented: $isExporting,
            document: exportDocument,
            contentType: .json,
            defaultFilename: "steam-manifests.json"
        ) { result in
            store.recordExport(result)
        }
        .overlay {
            if store.isScanning {
                ZStack {
                    Color.black.opacity(0.08)

                    ProgressView("Scanning Steam libraries…")
                        .padding(20)
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
                }
            }
        }
        .frame(minWidth: 980, minHeight: 640)
        .task {
            if store.manifests.isEmpty, !store.isScanning {
                store.scan()
            }
        }
    }

    private var sidebar: some View {
        List(selection: $store.selectedID) {
            Section("Libraries") {
                if store.libraries.isEmpty {
                    Text("No libraries found")
                        .foregroundStyle(.secondary)
                }

                ForEach(store.libraries) { library in
                    HStack(spacing: 8) {
                        Image(systemName: "folder")
                            .foregroundStyle(.secondary)

                        Text(library.displayName)
                            .lineLimit(1)

                        Spacer()

                        Text(String(store.libraryCount(for: library)))
                            .foregroundStyle(.secondary)
                            .monospacedDigit()
                    }
                }
            }

            Section("Issues") {
                if store.issues.isEmpty {
                    Text("No scan issues")
                        .foregroundStyle(.secondary)
                }

                ForEach(store.issues) { issue in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(issue.message)
                            .font(.caption)
                            .foregroundStyle(.orange)
                            .lineLimit(3)

                        if let fileURL = issue.fileURL {
                            Text(fileURL.lastPathComponent)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                    }
                    .padding(.vertical, 2)
                }
            }

            Section("Manifests") {
                if store.filteredManifests.isEmpty {
                    Text("No matching manifests")
                        .foregroundStyle(.secondary)
                }

                ForEach(store.filteredManifests) { manifest in
                    ManifestRow(manifest: manifest)
                        .tag(manifest.id)
                }
            }
        }
        .listStyle(.sidebar)
        .navigationTitle("Steam Manifests")
    }

    private var detail: some View {
        Group {
            if let manifest = store.selectedManifest {
                ManifestDetailView(manifest: manifest)
            } else {
                VStack(spacing: 12) {
                    Image(systemName: "doc.text.magnifyingglass")
                        .font(.system(size: 44))
                        .foregroundStyle(.secondary)

                    Text("Select a manifest")
                        .font(.title3)

                    Text("Scan your Steam libraries to inspect local appmanifest files.")
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }
}
