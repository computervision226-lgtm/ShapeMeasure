import SwiftUI

@main
struct PartScanApp: App {
    @StateObject private var settings = ServerSettings()
    @StateObject private var store = ScanStore()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(settings)
                .environmentObject(store)
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var settings: ServerSettings

    var body: some View {
        TabView {
            NavigationStack { ScanView() }
                .tabItem { Label("Scan", systemImage: "camera.viewfinder") }
            NavigationStack { ScansListView() }
                .tabItem { Label("Scans", systemImage: "cube.transparent") }
            NavigationStack { SettingsView() }
                .tabItem { Label("Server", systemImage: "server.rack") }
                .badge(settings.isConfigured ? 0 : 1)
        }
        .tint(.orange)
    }
}
