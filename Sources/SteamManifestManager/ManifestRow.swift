import SteamManifestKit
import SwiftUI

struct ManifestRow: View {
    let manifest: AppManifest

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: manifest.isFullyInstalled ? "shippingbox.fill" : "shippingbox")
                .font(.title3)
                .foregroundStyle(manifest.isFullyInstalled ? Color.green : Color.orange)
                .frame(width: 24)

            VStack(alignment: .leading, spacing: 2) {
                Text(manifest.name)
                    .font(.headline)
                    .lineLimit(1)

                Text("AppID \(manifest.appID) · \(manifest.libraryDisplayName)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }

            Spacer(minLength: 8)

            VStack(alignment: .trailing, spacing: 2) {
                Text(Format.bytes(manifest.sizeOnDisk))
                    .font(.caption)
                    .monospacedDigit()

                if !manifest.warnings.isEmpty {
                    Label(
                        String(manifest.warnings.count),
                        systemImage: "exclamationmark.triangle.fill"
                    )
                    .font(.caption2)
                    .foregroundStyle(.orange)
                }
            }
        }
        .padding(.vertical, 2)
    }
}
