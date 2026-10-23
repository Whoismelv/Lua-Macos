import SwiftUI

@main
struct SteamManifestManagerApp: App {
    @StateObject private var store = ManifestStore()

    var body: some Scene {
        WindowGroup("Steam Manifest Manager") {
            ContentView()
                .environmentObject(store)
        }
        .commands {
            CommandGroup(after: .newItem) {
                Button("Scan Steam Libraries") {
                    store.scan()
                }
                .keyboardShortcut("r", modifiers: .command)

                Button("Choose Steam Folder…") {
                    store.chooseCustomRoot()
                }
                .keyboardShortcut("o", modifiers: [.command, .shift])
            }
        }
    }
}
