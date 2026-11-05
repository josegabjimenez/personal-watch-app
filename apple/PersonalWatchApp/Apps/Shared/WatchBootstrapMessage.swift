import Foundation

/// Ephemeral WatchConnectivity message contract.
/// The route secret must only travel via `sendMessage`, never application context
/// or user-info queues that can persist plaintext for deferred delivery.
enum WatchBootstrapMessage {
    static let commandKey = "command"
    static let command = "bootstrap_mobile_capture_v1"
    static let baseURLKey = "base_url"
    static let routeSecretKey = "route_secret"
    static let replyOKKey = "ok"
    static let replyErrorKey = "error"

    static func make(baseURL: URL, routeSecret: String) -> [String: Any] {
        [
            commandKey: command,
            baseURLKey: baseURL.absoluteString,
            routeSecretKey: routeSecret
        ]
    }
}

enum WatchDiagnosticsMessage {
    static let command = "watch_outbox_diagnostics_v1"
    static let configuredKey = "configured"
    static let outboxReadableKey = "outbox_readable"
    static let totalKey = "outbox_total"
    static let pendingKey = "outbox_pending"
    static let sendingKey = "outbox_sending"
    static let sentKey = "outbox_sent"
    static let failedKey = "outbox_failed"
    static let lastDeliveryPathKey = "last_delivery_path"

    static var request: [String: Any] {
        [WatchBootstrapMessage.commandKey: command]
    }
}

struct WatchOutboxDiagnostics: Equatable, Sendable {
    let configured: Bool
    let outboxReadable: Bool
    let total: Int
    let pending: Int
    let sending: Int
    let sent: Int
    let failed: Int
    let lastDeliveryPath: String?

    init?(reply: [String: Any]) {
        guard
            let configured = reply[WatchDiagnosticsMessage.configuredKey] as? Bool,
            let outboxReadable = reply[WatchDiagnosticsMessage.outboxReadableKey] as? Bool,
            let total = reply[WatchDiagnosticsMessage.totalKey] as? Int,
            let pending = reply[WatchDiagnosticsMessage.pendingKey] as? Int,
            let sending = reply[WatchDiagnosticsMessage.sendingKey] as? Int,
            let sent = reply[WatchDiagnosticsMessage.sentKey] as? Int,
            let failed = reply[WatchDiagnosticsMessage.failedKey] as? Int
        else {
            return nil
        }
        guard
            total >= 0,
            pending >= 0,
            sending >= 0,
            sent >= 0,
            failed >= 0,
            pending + sending + sent + failed == total
        else {
            return nil
        }
        let lastDeliveryPath = reply[WatchDiagnosticsMessage.lastDeliveryPathKey] as? String
        guard
            lastDeliveryPath == nil ||
            lastDeliveryPath == "direct_https" ||
            lastDeliveryPath == "iphone_fallback"
        else {
            return nil
        }
        self.configured = configured
        self.outboxReadable = outboxReadable
        self.total = total
        self.pending = pending
        self.sending = sending
        self.sent = sent
        self.failed = failed
        self.lastDeliveryPath = lastDeliveryPath
    }
}

enum WatchCaptureFallbackMessage {
    static let command = "watch_capture_fallback_v1"
    static let payloadDataKey = "payload_data"
    static let responseDataKey = "response_data"

    static func make(payloadData: Data) -> [String: Any] {
        [
            WatchBootstrapMessage.commandKey: command,
            payloadDataKey: payloadData
        ]
    }
}
