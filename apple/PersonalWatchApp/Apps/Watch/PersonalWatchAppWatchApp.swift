import SwiftUI
import PersonalWatchCore

@main
struct PersonalWatchAppWatchApp: App {
    @StateObject private var bootstrapReceiver = WatchBootstrapReceiver()

    var body: some Scene {
        WindowGroup {
            WatchContentView()
                .environmentObject(bootstrapReceiver)
        }
    }
}
