import SwiftUI

/// An empty, offline host for iOS package tests. It does not initialize the
/// product's App Intents, WatchConnectivity, configuration, or outbox UI.
@main
struct PersonalWatchCoreTestHost: App {
    var body: some Scene {
        WindowGroup { Color.clear }
    }
}
