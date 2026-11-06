import Foundation
import PersonalWatchCore

/// Shared app storage and transport. Synthetic UI support is compiled only for
/// Debug simulator builds and never writes app configuration or Keychain.
enum PersonalWatchAppRuntime {
    static var isSynthetic: Bool {
        #if DEBUG && targetEnvironment(simulator)
        return syntheticCaseID != nil
        #else
        return false
        #endif
    }

    static var outboxURL: URL {
        #if DEBUG && targetEnvironment(simulator)
        if let caseID = syntheticCaseID {
            return FileManager.default.temporaryDirectory
                .appendingPathComponent("PersonalWatchSyntheticUI", isDirectory: true)
                .appendingPathComponent(caseID, isDirectory: true)
                .appendingPathComponent("outbox.json", isDirectory: false)
        }
        #endif
        // Keep the installed app's existing outbox and hermes.* preference keys.
        let root = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        return root.appendingPathComponent("HermesCapture", isDirectory: true)
            .appendingPathComponent("outbox.json", isDirectory: false)
    }

    static func credentials() throws -> (baseURL: URL, secret: String)? {
        #if DEBUG && targetEnvironment(simulator)
        if syntheticCaseID != nil {
            return (URL(string: "https://personal-watch-ui.invalid")!, "synthetic-simulator-only-not-a-real-secret")
        }
        #endif
        guard let rawBaseURL = UserDefaults.standard.string(forKey: "hermes.baseURL"),
              !rawBaseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        let baseURL = try EndpointValidator.normalizedBaseURL(from: rawBaseURL)
        guard let secret = try KeychainRouteSecretStore().loadRouteSecretSynchronously(),
              !secret.isEmpty else { return nil }
        return (baseURL, secret)
    }

    static func makeClient(baseURL: URL) -> WebhookClient {
        #if DEBUG && targetEnvironment(simulator)
        if syntheticCaseID != nil {
            let configuration = URLSessionConfiguration.ephemeral
            // This protocol claims every request made by this session. Even a
            // malformed fixture URL fails locally instead of reaching a network.
            configuration.protocolClasses = [PersonalWatchSyntheticUIURLProtocol.self]
            configuration.urlCache = nil
            configuration.urlCredentialStorage = nil
            configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
            return WebhookClient(endpoint: EndpointValidator.captureURL(from: baseURL),
                                 session: URLSession(configuration: configuration))
        }
        #endif
        return WebhookClient(endpoint: EndpointValidator.captureURL(from: baseURL))
    }

    /// Connection diagnostics never enqueue or sign a capture.
    static func checkServerConnection(baseURL: URL) async -> ServerConnectionCheckResult {
        #if DEBUG && targetEnvironment(simulator)
        if syntheticCaseID != nil {
            let configuration = URLSessionConfiguration.ephemeral
            configuration.protocolClasses = [PersonalWatchSyntheticUIURLProtocol.self]
            configuration.urlCache = nil
            configuration.urlCredentialStorage = nil
            configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
            let session = URLSession(configuration: configuration)
            defer { session.invalidateAndCancel() }
            return await ServerConnectionCheck.check(baseURL: baseURL, session: session)
        }
        #endif
        return await ServerConnectionCheck.check(baseURL: baseURL)
    }

    static func prepareSyntheticStateIfNeeded() async {
        #if DEBUG && targetEnvironment(simulator)
        guard let caseID = syntheticCaseID,
              ProcessInfo.processInfo.arguments.contains("--personal-watch-ui-scenario=reminder_old_sending")
              || ProcessInfo.processInfo.arguments.contains("--hermes-ui-scenario=reminder_old_sending")
        else { return }
        let url = outboxURL
        guard !FileManager.default.fileExists(atPath: url.path) else { return }
        let capturedAt = "2026-11-01T00:00:00Z"
        let payload = CaptureFactory(
            appVersion: "synthetic-ui", platform: "simulator", osVersion: "synthetic",
            deviceID: "synthetic-ui-device", surface: "synthetic-ui",
            nowISO8601: { capturedAt }, makeRequestID: { "synthetic-old-reminder-" + caseID }
        ).makePayload(kind: .reminder, text: "Recordatorio anterior sintético", requestWrite: true)
        let store = FileOutboxStore(fileURL: url)
        do {
            _ = try await store.enqueue(payload, now: capturedAt)
            _ = try await store.claimDelivery(payload: payload, now: capturedAt)
        } catch {
            // Fixtures remain fail-closed and never alter configuration or credentials.
        }
        #endif
    }

    #if DEBUG && targetEnvironment(simulator)
    private static var syntheticCaseID: String? {
        let arguments = ProcessInfo.processInfo.arguments
        // Old fixture arguments remain aliases for existing local verification commands.
        guard arguments.contains("--personal-watch-synthetic-ui") || arguments.contains("--hermes-synthetic-ui") else { return nil }
        let prefixes = ["--personal-watch-ui-case=", "--hermes-ui-case="]
        guard let prefix = prefixes.first(where: { prefix in arguments.contains(where: { $0.hasPrefix(prefix) }) }),
              let argument = arguments.first(where: { $0.hasPrefix(prefix) }) else { return nil }
        let value = String(argument.dropFirst(prefix.count))
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyz0123456789-")
        guard !value.isEmpty, value.count <= 128,
              value.unicodeScalars.allSatisfy({ allowed.contains($0) }) else { return nil }
        return value
    }
    #endif
}

#if DEBUG && targetEnvironment(simulator)
/// A local-only protocol for exercising the app UI, not a backend substitute.
/// No handler, credential, or launch-argument bypass exists in device/Release builds.
private final class PersonalWatchSyntheticUIURLProtocol: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let url = request.url,
              url.scheme == "https", url.host == "personal-watch-ui.invalid",
              url.port == nil, url.user == nil, url.password == nil,
              url.query == nil, url.fragment == nil else {
            fail(URLError(.unsupportedURL))
            return
        }
        if url.path == "/health", request.httpMethod == "GET" {
            guard request.httpBody == nil,
                  request.value(forHTTPHeaderField: WebhookHeaders.signatureV2) == nil,
                  request.value(forHTTPHeaderField: WebhookHeaders.requestID) == nil else {
                fail(URLError(.unsupportedURL))
                return
            }
            if scenario == "reminder_network_failure" {
                fail(URLError(.cannotFindHost))
            } else {
                do {
                    try respond(["status": "ok", "service": "mobile-capture-v1-staging",
                                 "mode": "allowlisted-write", "write_domains": ["aura.reminder_capture", "aura.grocery_capture"]], status: 200)
                } catch { fail(error) }
            }
            return
        }
        guard url.path == "/webhooks/mobile-capture-v1", request.httpMethod == "POST" else {
            fail(URLError(.unsupportedURL))
            return
        }
        do {
            let data = try requestBody()
            let payload = try JSONDecoder.captureDecoder().decode(CapturePayloadV1.self, from: data)
            guard payload.eventType == CapturePayloadV1.eventType,
                  payload.schema == CapturePayloadV1.schema,
                  payload.schemaVersion == CapturePayloadV1.schemaVersion,
                  !payload.requestID.isEmpty,
                  !payload.capture.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  !payload.context.dryRun,
                  OutboxDeliveryService.isSafeRequest(payload) else {
                fail(URLError(.badServerResponse))
                return
            }

            var body: [String: Any] = [
                "status": "committed", "dry_run": false, "write_committed": true,
                "request_id": payload.requestID, "domain": payload.route.domain.rawValue,
                "duplicate": false, "gates_missing": [],
                "plan": ["would_write": true, "side_effects": []]
            ]
            switch payload.route {
            case .grocery:
                if scenario == "grocery_network_failure" {
                    fail(URLError(.cannotConnectToHost))
                    return
                }
                body["display_message"] = "Mercado actualizado ✅ · leche (faltante)"
                body["external_ids"] = ["notion_block_id": "synthetic-grocery-block"]
                body["plan"] = ["would_write": true, "side_effects": ["notion.official_grocery_list.write"]]
            case .reminder:
                if scenario == "reminder_network_failure" {
                    fail(URLError(.cannotFindHost))
                    return
                }
                if scenario == "reminder_pending" {
                    try respond(["status": "error", "error": "reminder_pending_retry",
                                 "request_id": payload.requestID, "domain": payload.route.domain.rawValue], status: 502)
                    return
                }
                if scenario == "reminder_expired" {
                    body["status"] = "needs_input"
                    body["write_committed"] = false
                    body["display_message"] = "La hora ya pasó · no creé este recordatorio"
                    body["plan"] = ["would_write": false, "side_effects": []]
                    try respond(body, status: 200)
                    return
                }
                body["display_message"] = "Guardado en Todoist · lunes 26 oct, 2:00 p. m. Bogotá · avisos pendientes"
                body["interpreted_due"] = ["start": "2026-10-26T14:00:00-05:00",
                                            "timezone": "America/Bogota", "display": "lunes 26 oct, 2:00 p. m. Bogotá"]
                body["auto_reminder_requested"] = true
                body["notification_verified"] = false
                body["delivery_status"] = "pending_verification"
                body["external_ids"] = ["todoist_task_id": "synthetic-reminder-task"]
                body["plan"] = ["would_write": true, "side_effects": ["todoist.task.write"]]
            case .expense:
                if payload.context.userCancellation == true {
                    body["status"] = "cancelled"
                    body["write_committed"] = false
                    body["operation_id"] = payload.context.operationID
                    body["display_message"] = "Gasto cancelado · no se registró nada"
                    body["plan"] = ["would_write": false, "side_effects": []]
                } else if payload.context.userConfirmation {
                    guard payload.context.confirmationToken == "synthetic-ui-confirmation-token" else {
                        fail(URLError(.badServerResponse))
                        return
                    }
                    if scenario == "offline_confirmation" {
                        fail(URLError(.notConnectedToInternet))
                        return
                    }
                    body["operation_id"] = payload.context.operationID
                    if scenario == "expired" {
                        body["status"] = "rejected"
                        body["write_committed"] = false
                        body["display_message"] = "Vista previa vencida · no se registró el gasto"
                        body["plan"] = ["would_write": false, "side_effects": []]
                        try respond(body, status: 410)
                        return
                    }
                    body["display_message"] = "Gasto registrado ✅ · $45.000 COP"
                    body["plan"] = ["would_write": true, "side_effects": ["firefly.withdrawal.write"]]
                    body["external_ids"] = ["firefly_journal_id": "synthetic-journal"]
                } else {
                    let hexadecimal = payload.requestID.lowercased().filter { "0123456789abcdef".contains($0) }
                    let operationSuffix = String((hexadecimal + String(repeating: "0", count: 32)).prefix(32))
                    body["status"] = "needs_confirmation"
                    body["write_committed"] = false
                    body["operation_id"] = "exp_" + operationSuffix
                    body["confirmation_token"] = "synthetic-ui-confirmation-token"
                    body["confirmation"] = ["amount_cop": 45000, "description": "Uber",
                                             "source_name": "Efectivo", "category_name": "Transporte",
                                             "budget_name": "Movilidad"]
                    body["display_message"] = "Revisa $45.000 COP · Uber · Efectivo · Transporte · Movilidad"
                    body["plan"] = ["would_write": false, "side_effects": []]
                }
            default:
                fail(URLError(.unsupportedURL))
                return
            }
            try respond(body, status: 200)
        } catch {
            fail(error)
        }
    }

    override func stopLoading() {}

    private var scenario: String {
        let arguments = ProcessInfo.processInfo.arguments
        for prefix in ["--personal-watch-ui-scenario=", "--hermes-ui-scenario="] {
            if let argument = arguments.first(where: { $0.hasPrefix(prefix) }) {
                return String(argument.dropFirst(prefix.count))
            }
        }
        return "normal"
    }

    private func requestBody() throws -> Data {
        if let body = request.httpBody, body.count <= 64 * 1024 { return body }
        guard let stream = request.httpBodyStream else { throw URLError(.badServerResponse) }
        stream.open()
        defer { stream.close() }
        var body = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while true {
            let count = stream.read(&buffer, maxLength: 4096)
            if count == 0 { break }
            guard count > 0, body.count + count <= 64 * 1024 else { throw URLError(.dataLengthExceedsMaximum) }
            body.append(buffer, count: count)
        }
        return body
    }

    private func respond(_ body: [String: Any], status: Int) throws {
        guard let url = request.url,
              let response = HTTPURLResponse(url: url, statusCode: status,
                                             httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"]) else {
            throw URLError(.badServerResponse)
        }
        let data = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }

    private func fail(_ error: Error) {
        client?.urlProtocol(self, didFailWithError: error)
    }
}
#endif
