import AppKit
import SteamManifestKit
import SwiftUI

struct ManifestDetailView: View {
    @EnvironmentObject private var store: ManifestStore

    let manifest: AppManifest

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header
                overviewSection
                depotSection
                validationSection
                rawSection
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(Color(nsColor: .textBackgroundColor))
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button {
                    store.backupSelected()
                } label: {
                    Label("Back Up", systemImage: "externaldrive.badge.plus")
                }

                Button {
                    store.revealSelected()
                } label: {
                    Label("Reveal", systemImage: "folder")
                }

                Button {
                    store.copySelectedRaw()
                } label: {
                    Label("Copy ACF", systemImage: "doc.on.doc")
                }
            }
        }
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 20) {
            AsyncImage(url: headerImageURL) { phase in
                switch phase {
                case .success(let image):
                    image
                        .resizable()
                        .aspectRatio(contentMode: .fill)

                case .failure:
                    placeholderHeader

                case .empty:
                    ProgressView()

                @unknown default:
                    placeholderHeader
                }
            }
            .frame(width: 460, height: 215)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .overlay {
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(Color(nsColor: .separatorColor))
            }

            VStack(alignment: .leading, spacing: 8) {
                Text(manifest.name)
                    .font(.system(.title, design: .default).bold())
                    .lineLimit(2)
                    .textSelection(.enabled)

                Text("AppID \(manifest.appID)")
                    .font(.title3.monospaced())
                    .foregroundStyle(.secondary)

                Text(manifest.stateDescription)
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 9)
                    .padding(.vertical, 5)
                    .background(Capsule().fill(stateColor.opacity(0.18)))
                    .foregroundStyle(stateColor)

                Text(manifest.libraryDisplayName)
                    .font(.callout)
                    .foregroundStyle(.secondary)

                Text(manifest.installDirectory.isEmpty
                     ? "No install directory"
                     : manifest.installDirectory)
                    .font(.callout)
                    .textSelection(.enabled)
            }

            Spacer(minLength: 0)
        }
    }

    private var overviewSection: some View {
        GroupBox("Manifest") {
            Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 10) {
                GridRow {
                    Text("Field")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)

                    Text("Value")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                }

                ForEach(overviewRows) { row in
                    GridRow {
                        Text(row.label)
                            .foregroundStyle(.secondary)

                        Text(row.value)
                            .textSelection(.enabled)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(8)
        }
    }

    private var depotSection: some View {
        GroupBox("Installed Depots (\(manifest.installedDepots.count))") {
            if manifest.installedDepots.isEmpty {
                Text("No InstalledDepots section")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
            } else {
                Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 8) {
                    GridRow {
                        Text("Depot")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)

                        Text("Manifest")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)

                        Text("DLC AppID")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                    }

                    ForEach(manifest.installedDepots) { depot in
                        GridRow {
                            Text(depot.depotID)
                                .monospaced()

                            Text(depot.manifestID.isEmpty ? "—" : depot.manifestID)
                                .monospaced()
                                .textSelection(.enabled)

                            Text(depot.dlcAppID.map(String.init) ?? "—")
                                .monospaced()
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(8)
            }

            if !manifest.mountedDepots.isEmpty {
                Divider()

                Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 8) {
                    GridRow {
                        Text("Mounted depot")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)

                        Text("Manifest")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                    }

                    ForEach(manifest.mountedDepots.keys.sorted(), id: \.self) { depotID in
                        GridRow {
                            Text(depotID)
                                .monospaced()

                            Text(manifest.mountedDepots[depotID] ?? "—")
                                .monospaced()
                                .textSelection(.enabled)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(8)
            }
        }
    }

    private var validationSection: some View {
        GroupBox("Validation") {
            if manifest.warnings.isEmpty {
                Label("No structural warnings", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(manifest.warnings, id: \.self) { warning in
                        Label(warning, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(8)
            }
        }
    }

    private var rawSection: some View {
        GroupBox("Raw ACF") {
            ScrollView {
                Text(manifest.rawText)
                    .font(.system(size: 11, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
            }
            .frame(minHeight: 220, maxHeight: 440)
        }
    }

    private var overviewRows: [DetailRow] {
        [
            DetailRow(
                id: "state",
                label: "State",
                value: "\(manifest.stateDescription) (\(manifest.stateFlags))"
            ),
            DetailRow(
                id: "build",
                label: "Build ID",
                value: manifest.buildID.map(String.init) ?? "—"
            ),
            DetailRow(
                id: "size",
                label: "Size on disk",
                value: Format.bytes(manifest.sizeOnDisk)
            ),
            DetailRow(
                id: "updated",
                label: "Last updated",
                value: Format.date(manifest.lastUpdated)
            ),
            DetailRow(
                id: "download",
                label: "Download",
                value: "\(Format.bytes(manifest.bytesDownloaded)) / \(Format.bytes(manifest.bytesToDownload))"
            ),
            DetailRow(
                id: "language",
                label: "Language",
                value: manifest.language ?? "—"
            ),
            DetailRow(
                id: "file",
                label: "File",
                value: manifest.fileURL.path
            )
        ]
    }

    private var headerImageURL: URL? {
        URL(string: "https://cdn.cloudflare.steamstatic.com/steam/apps/\(manifest.appID)/header.jpg")
    }

    private var placeholderHeader: some View {
        ZStack {
            Color(nsColor: .windowBackgroundColor)

            Image(systemName: "gamecontroller")
                .font(.system(size: 44))
                .foregroundStyle(.secondary)
        }
    }

    private var stateColor: Color {
        if manifest.isFullyInstalled {
            return .green
        }

        if manifest.stateFlags == 0 {
            return .gray
        }

        return .orange
    }
}

private struct DetailRow: Identifiable {
    let id: String
    let label: String
    let value: String
}
