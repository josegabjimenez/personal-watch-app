import Foundation
import PersonalWatchCore
#if DEBUG && targetEnvironment(simulator)
import CryptoKit
#endif

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
            return syntheticDirectory(caseID: caseID)
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
            return WebhookClient(endpoint: EndpointValidator.captureURL(from: baseURL),
                                 session: PersonalWatchSyntheticSession())
        }
        #endif
        return WebhookClient(endpoint: EndpointValidator.captureURL(from: baseURL))
    }

    /// Connection diagnostics never enqueue or sign a capture.
    static func checkServerConnection(baseURL: URL) async -> ServerConnectionCheckResult {
        #if DEBUG && targetEnvironment(simulator)
        if syntheticCaseID != nil {
            return await ServerConnectionCheck.check(baseURL: baseURL, session: PersonalWatchSyntheticSession())
        }
        #endif
        return await ServerConnectionCheck.check(baseURL: baseURL)
    }

    static func prepareSyntheticStateIfNeeded() async {
        #if DEBUG && targetEnvironment(simulator)
        guard let caseID = syntheticCaseID,
              ["reminder_old_sending", "reminder_old_dns", "expense_refreshed_confirmation_sending"].contains(syntheticScenario)
        else { return }
        let url = outboxURL
        guard !FileManager.default.fileExists(atPath: url.path) else { return }
        let capturedAt = "2026-11-01T00:00:00Z"
        let store = FileOutboxStore(fileURL: url)
        do {
            if syntheticScenario == "expense_refreshed_confirmation_sending" {
                try await seedRefreshedExpenseConfirmation(in: store, caseID: caseID, capturedAt: capturedAt)
            } else {
                let payload = syntheticFactory(id: "synthetic-old-reminder-" + caseID, capturedAt: capturedAt)
                    .makePayload(kind: .reminder, text: "Recordatorio anterior sintético", requestWrite: true)
                _ = try await store.enqueue(payload, now: capturedAt)
                if syntheticScenario == "reminder_old_dns" {
                    for _ in 0..<4 {
                        _ = try await store.claimDelivery(payload: payload, now: capturedAt)
                        try await store.markFailed(requestID: payload.requestID, message: "network_-1003", now: capturedAt)
                    }
                } else {
                    _ = try await store.claimDelivery(payload: payload, now: capturedAt)
                }
            }
        } catch {
            // Fixtures remain fail-closed and never alter configuration or credentials.
        }
        #endif
    }

    #if DEBUG && targetEnvironment(simulator)
    private static func syntheticFactory(id: String, capturedAt: String) -> CaptureFactory {
        CaptureFactory(appVersion: "synthetic-ui", platform: "simulator", osVersion: "synthetic",
                       deviceID: "synthetic-ui-device", surface: "synthetic-ui",
                       nowISO8601: { capturedAt }, makeRequestID: { id })
    }

    private static func seedRefreshedExpenseConfirmation(in store: FileOutboxStore, caseID: String, capturedAt: String) async throws {
        let operationID = "exp_" + String(repeating: "8", count: 32)
        let summary = ExpenseConfirmationSummary(amountCOP: 45000, description: "Uber", sourceName: "Efectivo",
                                                 categoryName: "Transporte", budgetName: "Movilidad")
        let text = "45 mil Uber Efectivo"
        let initialToken = "synthetic-ui-confirmation-token-old"
        let refreshedToken = "synthetic-ui-confirmation-token"
        let preview = syntheticFactory(id: "synthetic-expense-preview-" + caseID, capturedAt: capturedAt)
            .makePayload(kind: .expense, text: text, requestWrite: true)
        _ = try await store.enqueue(preview, now: capturedAt)
        _ = try await store.claimDelivery(payload: preview, now: capturedAt)
        try await store.markSent(requestID: preview.requestID, now: capturedAt,
                                 response: CaptureResponseV1(status: "needs_confirmation", dryRun: false,
                                    requestID: preview.requestID, domain: preview.route.domain.rawValue,
                                    operationID: operationID, confirmationToken: initialToken, confirmation: summary,
                                    writeCommitted: false, plan: .init(sideEffects: [], wouldWrite: false)))
        let old = syntheticFactory(id: "synthetic-expense-confirm-old-" + caseID, capturedAt: capturedAt)
            .makePayload(kind: .expense, text: text, requestWrite: true, userConfirmation: true,
                         confirmationToken: initialToken, operationID: operationID)
        _ = try await store.enqueue(old, now: capturedAt)
        _ = try await store.claimDelivery(payload: old, now: capturedAt)
        // A correlated server preview refresh is not a committed withdrawal.
        try await store.markSent(requestID: old.requestID, now: capturedAt,
                                 response: CaptureResponseV1(status: "needs_confirmation", dryRun: false,
                                    requestID: old.requestID, domain: old.route.domain.rawValue,
                                    operationID: operationID, confirmationToken: refreshedToken, confirmation: summary,
                                    displayMessage: "Revisa $45.000 COP · Uber · Efectivo · Transporte · Movilidad",
                                    writeCommitted: false, plan: .init(sideEffects: [], wouldWrite: false)))
        let current = syntheticFactory(id: "synthetic-expense-confirm-current-" + caseID, capturedAt: capturedAt)
            .makePayload(kind: .expense, text: text, requestWrite: true, userConfirmation: true,
                         confirmationToken: refreshedToken, operationID: operationID)
        _ = try await store.enqueue(current, now: capturedAt)
        _ = try await store.claimDelivery(payload: current, now: capturedAt)
    }

    fileprivate static func syntheticDirectory(caseID: String) -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("PersonalWatchSyntheticUI", isDirectory: true)
            .appendingPathComponent(caseID, isDirectory: true)
    }

    fileprivate static var syntheticScenario: String {
        let arguments = ProcessInfo.processInfo.arguments
        for prefix in ["--personal-watch-ui-scenario=", "--hermes-ui-scenario="] {
            if let argument = arguments.first(where: { $0.hasPrefix(prefix) }) {
                return String(argument.dropFirst(prefix.count))
            }
        }
        return "normal"
    }

    fileprivate static var syntheticCaseID: String? {
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
/// An in-memory simulator transport. It has no URLSession or network fallback.
/// No handler, credential, or launch-argument bypass exists in device/Release builds.
private final class PersonalWatchSyntheticSession: URLSessioning {
    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        let data: Data?
        do {
            data = request.httpBody != nil || request.httpBodyStream != nil ? try Self.requestBody(request) : nil
        } catch {
            PersonalWatchSyntheticRequestAudit.append(request: request, body: nil)
            throw error
        }
        PersonalWatchSyntheticRequestAudit.append(request: request, body: data)
        return try Self.fixtureResponse(for: request, body: data, scenario: PersonalWatchAppRuntime.syntheticScenario)
    }

    private static func fixtureResponse(for request: URLRequest, body data: Data?, scenario: String) throws -> (Data, URLResponse) {
        guard let url = request.url,
              url.scheme == "https", url.host == "personal-watch-ui.invalid",
              url.port == nil, url.user == nil, url.password == nil,
              url.query == nil, url.fragment == nil else {
            throw URLError(.unsupportedURL)
        }
        if url.path == "/health", request.httpMethod == "GET" {
            guard data == nil,
                  request.value(forHTTPHeaderField: WebhookHeaders.signatureV2) == nil,
                  request.value(forHTTPHeaderField: WebhookHeaders.requestID) == nil else {
                throw URLError(.unsupportedURL)
            }
            if ["reminder_network_failure", "reminder_old_dns"].contains(scenario) {
                throw URLError(.cannotFindHost)
            }
            if scenario == "health_tls_failure" { throw URLError(.secureConnectionFailed) }
            if scenario == "health_timeout" { throw URLError(.timedOut) }
            let domains = scenario == "health_gate_disabled"
                ? ["aura.grocery_capture"] : ["aura.reminder_capture", "aura.grocery_capture"]
            return try response(["status": "ok", "service": "mobile-capture-v1-staging",
                                 "mode": "allowlisted-write", "write_domains": domains],
                                status: scenario == "health_http_503" ? 503 : 200, url: url)
        }
        guard url.path == "/webhooks/mobile-capture-v1", request.httpMethod == "POST" else {
            throw URLError(.unsupportedURL)
        }
        let payload = try JSONDecoder.captureDecoder().decode(CapturePayloadV1.self, from: data ?? Data())
        guard payload.eventType == CapturePayloadV1.eventType,
              payload.schema == CapturePayloadV1.schema,
              payload.schemaVersion == CapturePayloadV1.schemaVersion,
              !payload.requestID.isEmpty,
              !payload.capture.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !payload.context.dryRun,
              OutboxDeliveryService.isSafeRequest(payload) else {
            throw URLError(.badServerResponse)
        }

        var body: [String: Any] = [
            "status": "committed", "dry_run": false, "write_committed": true,
            "request_id": payload.requestID, "domain": payload.route.domain.rawValue,
            "duplicate": false, "gates_missing": [],
            "plan": ["would_write": true, "side_effects": []]
        ]
        switch payload.route {
        case .grocery:
            if scenario == "grocery_network_failure" { throw URLError(.cannotConnectToHost) }
            body["display_message"] = "Mercado actualizado ✅ · leche (faltante)"
            body["external_ids"] = ["notion_block_id": "synthetic-grocery-block"]
            body["plan"] = ["would_write": true, "side_effects": ["notion.official_grocery_list.write"]]
        case .reminder:
            if ["reminder_network_failure", "reminder_old_dns"].contains(scenario) {
                throw URLError(.cannotFindHost)
            }
            if scenario == "reminder_pending" {
                return try response(["status": "error", "error": "reminder_pending_retry",
                                     "request_id": payload.requestID, "domain": payload.route.domain.rawValue], status: 502, url: url)
            }
            if scenario == "reminder_expired" {
                body["status"] = "needs_input"
                body["write_committed"] = false
                body["display_message"] = "La hora ya pasó · no creé este recordatorio"
                body["plan"] = ["would_write": false, "side_effects": []]
                return try response(body, status: 200, url: url)
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
                    throw URLError(.badServerResponse)
                }
                if scenario == "offline_confirmation" { throw URLError(.notConnectedToInternet) }
                body["operation_id"] = payload.context.operationID
                if scenario == "expired" {
                    body["status"] = "rejected"
                    body["write_committed"] = false
                    body["display_message"] = "Vista previa vencida · no se registró el gasto"
                    body["plan"] = ["would_write": false, "side_effects": []]
                    return try response(body, status: 410, url: url)
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
            throw URLError(.unsupportedURL)
        }
        return try response(body, status: 200, url: url)
    }

    private static func requestBody(_ request: URLRequest) throws -> Data {
        if let body = request.httpBody {
            guard body.count <= 64 * 1024 else { throw URLError(.dataLengthExceedsMaximum) }
            return body
        }
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

    private static func response(_ body: [String: Any], status: Int, url: URL) throws -> (Data, URLResponse) {
        guard let response = HTTPURLResponse(url: url, statusCode: status,
                                             httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"]) else {
            throw URLError(.badServerResponse)
        }
        return (try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys]), response)
    }
}

/// Only simulator-generated requests reach this logger. It records neither
/// request bodies nor authentication headers, and never contacts a network.
private enum PersonalWatchSyntheticRequestAudit {
    private static let lock = NSLock()

    static func append(request: URLRequest, body: Data?) {
        guard let caseID = PersonalWatchAppRuntime.syntheticCaseID else { return }
        let payload = body.flatMap { try? JSONDecoder.captureDecoder().decode(CapturePayloadV1.self, from: $0) }
        let entry: [String: Any] = [
            "method": request.httpMethod ?? "GET",
            "path": request.url?.path ?? "",
            "body_sha256": SHA256.hash(data: body ?? Data()).map { String(format: "%02x", $0) }.joined(),
            "request_id": payload?.requestID as Any? ?? NSNull()
        ]
        lock.lock()
        defer { lock.unlock() }
        do {
            let directory = PersonalWatchAppRuntime.syntheticDirectory(caseID: caseID)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let url = directory.appendingPathComponent("transport-audit.jsonl")
            if !FileManager.default.fileExists(atPath: url.path) {
                guard FileManager.default.createFile(atPath: url.path, contents: Data()) else { return }
            }
            var line = try JSONSerialization.data(withJSONObject: entry, options: [.sortedKeys])
            line.append(0x0A)
            let handle = try FileHandle(forWritingTo: url)
            defer { try? handle.close() }
            try handle.seekToEnd()
            try handle.write(contentsOf: line)
        } catch {
            // Failed diagnostics must not trigger a fallback transport or alter the outbox.
        }
    }
}
#endif
