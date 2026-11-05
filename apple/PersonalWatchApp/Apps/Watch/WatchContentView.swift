import SwiftUI
import PersonalWatchCore

struct WatchContentView: View {
    @EnvironmentObject private var bootstrapReceiver: WatchBootstrapReceiver
    @State private var isRetrying = false
    @State private var retryMessage: String?

    private let actions = QuickActionKind.mvpActions

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(actions) { action in
                        NavigationLink {
                            WatchCaptureView(action: action)
                        } label: {
                            Label(action.title, systemImage: action.symbolName)
                        }
                    }

                    NavigationLink {
                        WatchHistoryView()
                    } label: {
                        Label("Historial", systemImage: "clock.arrow.circlepath")
                    }
                }

                Section {
                    Label(
                        bootstrapReceiver.statusMessage,
                        systemImage: bootstrapReceiver.isConfigured ? "checkmark.shield" : "iphone.gen3"
                    )
                    .font(.footnote)
                    .foregroundStyle(bootstrapReceiver.isConfigured ? .green : .secondary)

                    if bootstrapReceiver.isConfigured {
                        Button {
                            Task { @MainActor in
                                await retryPendingCaptures()
                            }
                        } label: {
                            if isRetrying {
                                ProgressView()
                            } else {
                                Label("Reintentar pendientes", systemImage: "arrow.clockwise")
                            }
                        }
                        .disabled(isRetrying)
                    }

                    if let retryMessage {
                        Text(retryMessage)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Personal Watch App")
        }
    }

    @MainActor
    private func retryPendingCaptures() async {
        guard !isRetrying else { return }
        isRetrying = true
        defer { isRetrying = false }

        guard
            let credentials = try? PersonalWatchAppRuntime.credentials()
        else {
            retryMessage = "Configura desde el iPhone"
            return
        }

        let store = FileOutboxStore(fileURL: outboxURL)
        do {
            let items = try await store.loadDeliverable()
            guard !items.isEmpty else {
                retryMessage = "Sin pendientes"
                return
            }

            var sent = 0
            var failed = 0
            var results: [String] = []

            for item in items {
                do {
                    let response = try await WatchCaptureDeliveryCoordinator.deliver(
                        payload: item.payload,
                        secret: credentials.secret,
                        baseURL: credentials.baseURL,
                        outbox: store
                    )
                    if let message = response.displayMessage ?? response.question { results.append(message) }
                    sent += 1
                } catch {
                    failed += 1
                }
            }

            let summary = failed == 0
                ? "\(sent) respuesta(s) recibidas"
                : "\(sent) respuestas · \(failed) pendientes"
            retryMessage = ([summary] + results).joined(separator: "\n")
        } catch {
            retryMessage = "No se pudo leer outbox"
        }
    }

    private var outboxURL: URL { PersonalWatchAppRuntime.outboxURL }
}

#Preview {
    WatchContentView()
        .environmentObject(WatchBootstrapReceiver())
}
