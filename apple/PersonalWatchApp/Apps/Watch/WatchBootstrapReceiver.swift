import Combine
import Foundation
import PersonalWatchCore
import WatchConnectivity

final class WatchBootstrapReceiver: NSObject, ObservableObject {
    @Published private(set) var isConfigured = false
    @Published private(set) var statusMessage = "Configura desde el iPhone"

    private let secretStore = KeychainRouteSecretStore()
    private var session: WCSession?

    override init() {
        super.init()
        if PersonalWatchAppRuntime.isSynthetic {
            isConfigured = true
            statusMessage = "Prueba sintética · sin servicios reales"
            return
        }
        refreshStoredConfigurationStatus()

        guard WCSession.isSupported() else {
            statusMessage = "WatchConnectivity no disponible"
            return
        }
        let session = WCSession.default
        self.session = session
        session.delegate = self
        session.activate()
    }

    private func refreshStoredConfigurationStatus() {
        let endpoint = UserDefaults.standard.string(forKey: "hermes.baseURL") ?? ""
        let secret = try? secretStore.loadRouteSecretSynchronously()
        isConfigured = !endpoint.isEmpty && !(secret ?? "").isEmpty
        statusMessage = isConfigured ? "Configuración segura lista" : "Configura desde el iPhone"
    }

    private func updateStatus(configured: Bool, message: String) {
        DispatchQueue.main.async { [weak self] in
            self?.isConfigured = configured
            self?.statusMessage = message
        }
    }

    private func diagnosticsReply() -> [String: Any] {
        let endpoint = UserDefaults.standard.string(forKey: "hermes.baseURL") ?? ""
        let secret = (try? secretStore.loadRouteSecretSynchronously()) ?? ""
        let configured = !endpoint.isEmpty && !secret.isEmpty

        let items: [OutboxItem]
        let outboxReadable: Bool
        do {
            if FileManager.default.fileExists(atPath: outboxURL.path) {
                let data = try Data(contentsOf: outboxURL)
                items = try JSONDecoder.captureDecoder().decode([OutboxItem].self, from: data)
            } else {
                items = []
            }
            outboxReadable = true
        } catch {
            items = []
            outboxReadable = false
        }

        let lastDeliveryPath = items
            .filter { $0.status == .sent && $0.lastDeliveryPath != nil }
            .max { $0.updatedAt < $1.updatedAt }?
            .lastDeliveryPath?
            .rawValue

        var reply: [String: Any] = [
            WatchBootstrapMessage.replyOKKey: true,
            WatchDiagnosticsMessage.configuredKey: configured,
            WatchDiagnosticsMessage.outboxReadableKey: outboxReadable,
            WatchDiagnosticsMessage.totalKey: items.count,
            WatchDiagnosticsMessage.pendingKey: items.filter { $0.status == .pending }.count,
            WatchDiagnosticsMessage.sendingKey: items.filter { $0.status == .sending }.count,
            WatchDiagnosticsMessage.sentKey: items.filter { $0.status == .sent }.count,
            WatchDiagnosticsMessage.failedKey: items.filter { $0.status == .failed }.count
        ]
        if let lastDeliveryPath {
            reply[WatchDiagnosticsMessage.lastDeliveryPathKey] = lastDeliveryPath
        }
        return reply
    }

    private var outboxURL: URL {
        let root = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        return root
            // Share the installed app's legacy outbox location without migrating data.
            .appendingPathComponent("HermesCapture", isDirectory: true)
            .appendingPathComponent("outbox.json", isDirectory: false)
    }
}

extension WatchBootstrapReceiver: WCSessionDelegate {
    func session(
        _ session: WCSession,
        activationDidCompleteWith activationState: WCSessionActivationState,
        error: Error?
    ) {
        if let error {
            updateStatus(configured: isConfigured, message: "WatchConnectivity: \(error.localizedDescription)")
        }
    }

    func session(
        _ session: WCSession,
        didReceiveMessage message: [String: Any],
        replyHandler: @escaping ([String: Any]) -> Void
    ) {
        guard !PersonalWatchAppRuntime.isSynthetic else {
            replyHandler([
                WatchBootstrapMessage.replyOKKey: false,
                WatchBootstrapMessage.replyErrorKey: "synthetic_runtime"
            ])
            return
        }
        guard let command = message[WatchBootstrapMessage.commandKey] as? String else {
            replyHandler([
                WatchBootstrapMessage.replyOKKey: false,
                WatchBootstrapMessage.replyErrorKey: "unsupported_command"
            ])
            return
        }
        if command == WatchDiagnosticsMessage.command {
            replyHandler(diagnosticsReply())
            return
        }
        guard command == WatchBootstrapMessage.command else {
            replyHandler([
                WatchBootstrapMessage.replyOKKey: false,
                WatchBootstrapMessage.replyErrorKey: "unsupported_command"
            ])
            return
        }
        guard
            let rawBaseURL = message[WatchBootstrapMessage.baseURLKey] as? String,
            let routeSecret = message[WatchBootstrapMessage.routeSecretKey] as? String,
            !routeSecret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else {
            replyHandler([
                WatchBootstrapMessage.replyOKKey: false,
                WatchBootstrapMessage.replyErrorKey: "invalid_payload"
            ])
            return
        }

        do {
            let baseURL = try EndpointValidator.normalizedBaseURL(from: rawBaseURL)
            try secretStore.saveRouteSecretSynchronously(
                routeSecret.trimmingCharacters(in: .whitespacesAndNewlines)
            )
            UserDefaults.standard.set(baseURL.absoluteString, forKey: "hermes.baseURL")
            updateStatus(configured: true, message: "Configuración segura lista")
            replyHandler([WatchBootstrapMessage.replyOKKey: true])
        } catch {
            updateStatus(configured: false, message: "No se pudo guardar configuración")
            replyHandler([
                WatchBootstrapMessage.replyOKKey: false,
                WatchBootstrapMessage.replyErrorKey: "secure_storage_failed"
            ])
        }
    }
}
