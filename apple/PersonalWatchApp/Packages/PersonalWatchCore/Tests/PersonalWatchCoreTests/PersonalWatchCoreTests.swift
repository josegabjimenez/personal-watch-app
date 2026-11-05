import XCTest
@testable import PersonalWatchCore

final class PersonalWatchCoreTests: XCTestCase {
    func testPayloadEncodingUsesMobileCaptureContractKeys() throws {
        let payload = CapturePayloadV1(
            requestID: "unit-request-1",
            createdAt: "2026-07-06T20:55:00Z",
            source: CaptureSource(
                appVersion: "0.1.0",
                platform: "watchOS",
                osVersion: "10.0",
                deviceID: "unit-device",
                surface: "watch_app"
            ),
            route: .expense,
            capture: CaptureText(modality: "watch_dictation", text: "45 mil en Uber"),
            entities: CaptureEntities(currency: "COP"),
            context: CaptureContext(dryRun: true, allowWrite: false)
        )

        let data = try JSONEncoder.captureEncoder().encode(payload)
        let json = try XCTUnwrap(String(data: data, encoding: .utf8))

        XCTAssertTrue(json.contains("\"event_type\":\"mobile_capture.v1\""))
        XCTAssertTrue(json.contains("\"schema_version\":1"))
        XCTAssertTrue(json.contains("\"request_id\":\"unit-request-1\""))
        XCTAssertTrue(json.contains("\"app_version\":\"0.1.0\""))
        XCTAssertTrue(json.contains("\"device_id\":\"unit-device\""))
        XCTAssertTrue(json.contains("\"raw_text\":\"45 mil en Uber\""))
        XCTAssertTrue(json.contains("\"dry_run\":true"))
        XCTAssertTrue(json.contains("\"allow_write\":false"))
    }

    func testHMACV2KnownVector() {
        let body = Data(#"{"event_type":"mobile_capture.v1","request_id":"unit-test"}"#.utf8)
        let signature = HMACSigner.signatureV2(
            rawBody: body,
            timestamp: "1700000000",
            secret: "test-secret"
        )

        XCTAssertEqual(signature, "e824cb05567e5cae90d3eede3f35c5ca69fb2a8c1ced969456145a4f6b251aab")
    }

    func testCaptureResponseDecodesLegacyRunIDAndExpenseConfirmation() throws {
        let data = Data(#"{"status":"needs_confirmation","run_id":"run_0123456789abcdef0123456789abcdef","operation_id":"exp_0123456789abcdef0123456789abcdef","confirmation_token":"v2.token","confirmation":{"amount_cop":45000,"description":"Almuerzos","source_name":"Nequi","category_name":"Food","budget_name":"Comidas afuera"}}"#.utf8)
        let response = try JSONDecoder().decode(CaptureResponseV1.self, from: data)
        XCTAssertEqual(response.runID, "run_0123456789abcdef0123456789abcdef")
        XCTAssertEqual(response.operationID, "exp_0123456789abcdef0123456789abcdef")
        XCTAssertEqual(response.confirmationToken, "v2.token")
        XCTAssertEqual(response.confirmation?.amountCOP, 45_000)
        XCTAssertEqual(response.confirmation?.sourceName, "Nequi")
        XCTAssertEqual(response.confirmation?.budgetName, "Comidas afuera")
    }

    func testWebhookRequestContainsRequiredHeaders() throws {
        let payload = CapturePayloadV1(
            requestID: "unit-request-2",
            createdAt: "2026-07-06T20:55:00Z",
            source: CaptureSource(
                appVersion: "0.1.0",
                platform: "watchOS",
                osVersion: "10.0",
                deviceID: "unit-device",
                surface: "watch_app"
            ),
            route: .reminder,
            capture: CaptureText(
                modality: "watch_dictation",
                text: "mañana a las dos llamar a mamá"
            )
        )
        let endpoint = try XCTUnwrap(URL(string: "https://example.invalid/webhooks/mobile-capture-v1"))
        let client = WebhookClient(endpoint: endpoint)
        let request = try client.makeRequest(
            payload: payload,
            secret: "test-secret",
            timestamp: "1700000000"
        )

        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.value(forHTTPHeaderField: WebhookHeaders.timestamp), "1700000000")
        XCTAssertNotNil(request.value(forHTTPHeaderField: WebhookHeaders.signatureV2))
        XCTAssertEqual(request.value(forHTTPHeaderField: WebhookHeaders.requestID), "unit-request-2")
        XCTAssertEqual(request.value(forHTTPHeaderField: WebhookHeaders.payloadVersion), "1")
        XCTAssertEqual(request.value(forHTTPHeaderField: WebhookHeaders.deviceID), "unit-device")
    }

    func testCaptureFactoryCreatesDryRunPayload() {
        let factory = CaptureFactory(
            appVersion: "0.1.0",
            platform: "watchOS",
            osVersion: "10.0",
            deviceID: "unit-device",
            surface: "watch_app",
            nowISO8601: { "2026-07-06T20:55:00Z" },
            makeRequestID: { "factory-request" }
        )

        let payload = factory.makePayload(
            kind: .grocery,
            text: "agrega leche al mercado"
        )

        XCTAssertEqual(payload.requestID, "factory-request")
        XCTAssertEqual(payload.route.domain, .auraGroceryCapture)
        XCTAssertEqual(payload.context.dryRun, true)
        XCTAssertEqual(payload.context.allowWrite, false)
    }

    func testCaptureFactoryAllowsAppIntentModality() {
        let factory = CaptureFactory(
            appVersion: "0.1.0",
            platform: "watchOS",
            osVersion: "26.5",
            deviceID: "unit-device",
            surface: "app_intent_watch",
            nowISO8601: { "2026-07-17T20:00:00Z" },
            makeRequestID: { "intent-request" }
        )

        let payload = factory.makePayload(
            kind: .expense,
            text: "45 mil en Uber",
            modality: "app_intent"
        )

        XCTAssertEqual(payload.capture.modality, "app_intent")
        XCTAssertEqual(payload.source.surface, "app_intent_watch")
        XCTAssertEqual(payload.context.dryRun, true)
        XCTAssertEqual(payload.context.allowWrite, false)
    }

    func testCaptureFactoryCanExplicitlyRequestGroceryWrite() {
        let factory = CaptureFactory(
            appVersion: "0.3.3",
            platform: "watchOS",
            osVersion: "27.0",
            deviceID: "unit-device",
            surface: "watch_app",
            nowISO8601: { "2026-08-04T23:30:00Z" },
            makeRequestID: { "grocery-write-request" }
        )

        let payload = factory.makePayload(
            kind: .grocery,
            text: "leche",
            requestWrite: true
        )

        XCTAssertFalse(payload.context.dryRun)
        XCTAssertTrue(payload.context.allowWrite)
        XCTAssertEqual(payload.context.allowFireflyWrite, false)
    }

    func testCaptureFactoryBuildsConfirmedExpenseWithServerToken() {
        let factory = CaptureFactory(
            appVersion: "0.3.4",
            platform: "watchOS",
            osVersion: "27.0",
            deviceID: "unit-device",
            surface: "watch_app",
            nowISO8601: { "2026-08-30T20:00:00Z" },
            makeRequestID: { "confirmed-expense" }
        )
        let payload = factory.makePayload(
            kind: .expense,
            text: "45 mil almuerzo por Nequi",
            requestWrite: true,
            userConfirmation: true,
            confirmationToken: "v2.server-signed-token",
            operationID: "exp_0123456789abcdef0123456789abcdef"
        )
        XCTAssertTrue(payload.context.userConfirmation)
        XCTAssertEqual(payload.context.userCancellation, false)
        XCTAssertFalse(payload.context.requiresConfirmation)
        XCTAssertFalse(payload.context.dryRun)
        XCTAssertTrue(payload.context.allowWrite)
        XCTAssertEqual(payload.context.allowFireflyWrite, true)
        XCTAssertEqual(payload.context.confirmationToken, "v2.server-signed-token")
        XCTAssertEqual(payload.context.operationID, "exp_0123456789abcdef0123456789abcdef")
    }

    func testCaptureFactoryBuildsDistinctExpenseCancellationPayloadWithExactFlags() throws {
        func factory(requestID: String) -> CaptureFactory {
            CaptureFactory(
                appVersion: "0.3.4",
                platform: "watchOS",
                osVersion: "27.0",
                deviceID: "unit-device",
                surface: "watch_app",
                nowISO8601: { "2026-08-30T20:00:00Z" },
                makeRequestID: { requestID }
            )
        }

        let capturedText = "45 mil almuerzo por Nequi"
        let preview = factory(requestID: "expense-preview-request").makePayload(
            kind: .expense,
            text: capturedText,
            requestWrite: true
        )
        let cancellation = factory(requestID: "expense-cancellation-request").makePayload(
            kind: .expense,
            text: capturedText,
            requestWrite: true,
            userConfirmation: false,
            userCancellation: true,
            confirmationToken: nil,
            operationID: "exp_0123456789abcdef0123456789abcdef"
        )

        XCTAssertNotEqual(cancellation.requestID, preview.requestID)
        XCTAssertEqual(cancellation.route, .expense)
        XCTAssertEqual(cancellation.capture.text, preview.capture.text)
        XCTAssertEqual(cancellation.capture.rawText, preview.capture.rawText)
        XCTAssertEqual(cancellation.context.userCancellation, true)
        XCTAssertFalse(cancellation.context.userConfirmation)
        XCTAssertFalse(cancellation.context.requiresConfirmation)
        XCTAssertFalse(cancellation.context.dryRun)
        XCTAssertTrue(cancellation.context.allowWrite)
        XCTAssertEqual(cancellation.context.allowFireflyWrite, false)
        XCTAssertNil(cancellation.context.confirmationToken)
        XCTAssertEqual(cancellation.context.operationID, "exp_0123456789abcdef0123456789abcdef")

        let data = try JSONEncoder.captureEncoder().encode(cancellation)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let context = try XCTUnwrap(object["context"] as? [String: Any])
        XCTAssertEqual(context["user_cancellation"] as? Bool, true)
        XCTAssertEqual(context["user_confirmation"] as? Bool, false)
        XCTAssertEqual(context["allow_write"] as? Bool, true)
        XCTAssertEqual(context["allow_firefly_write"] as? Bool, false)
        XCTAssertNil(context["confirmation_token"])
    }

    func testQuickActionWritePolicyOnlyAllowsThreeMVPActions() {
        let factory = CaptureFactory(
            appVersion: "0.3.4",
            platform: "watchOS",
            osVersion: "27.0",
            deviceID: "unit-device",
            surface: "watch_app",
            nowISO8601: { "2026-08-30T20:00:00Z" },
            makeRequestID: { UUID().uuidString }
        )
        for kind in QuickActionKind.allCases {
            let payload = factory.makePayload(
                kind: kind,
                text: "unit capture",
                requestWrite: kind.requestsLiveWrite
            )
            XCTAssertEqual(kind.requestsLiveWrite, kind != .general)
            XCTAssertEqual(payload.context.dryRun, kind == .general)
            XCTAssertEqual(payload.context.allowWrite, kind != .general)
            XCTAssertEqual(
                payload.context.allowFireflyWrite,
                kind == .expense,
                "Only Gasto may carry the scoped Firefly request flag"
            )
        }
        XCTAssertEqual(QuickActionKind.general.title, "Capturar")
    }

    func testPhoneFallbackRequestPolicyMirrorsExactRouteCapabilities() {
        func payload(route: CaptureRoute, context: CaptureContext) -> CapturePayloadV1 {
            CapturePayloadV1(
                requestID: UUID().uuidString,
                createdAt: "2026-08-30T20:00:00Z",
                source: CaptureSource(
                    appVersion: "0.3.4",
                    platform: "watchOS",
                    osVersion: "27.0",
                    deviceID: "unit-watch",
                    surface: "watch_app"
                ),
                route: route,
                capture: CaptureText(modality: "watch_dictation", text: "unit capture"),
                context: context
            )
        }
        XCTAssertTrue(OutboxDeliveryService.isSafeRequest(payload(route: .general, context: CaptureContext())))
        XCTAssertTrue(
            OutboxDeliveryService.isSafeRequest(
                payload(
                    route: .expense,
                    context: CaptureContext(dryRun: false, allowWrite: true, allowFireflyWrite: true)
                )
            )
        )
        XCTAssertTrue(
            OutboxDeliveryService.isSafeRequest(
                payload(
                    route: .expense,
                    context: CaptureContext(
                        userConfirmation: true,
                        dryRun: false,
                        allowWrite: true,
                        allowFireflyWrite: true,
                        confirmationToken: "v2.server-token",
                        operationID: "exp_0123456789abcdef0123456789abcdef"
                    )
                )
            )
        )
        XCTAssertFalse(
            OutboxDeliveryService.isSafeRequest(
                payload(
                    route: .expense,
                    context: CaptureContext(dryRun: false, allowWrite: true, allowFireflyWrite: false)
                )
            )
        )
        XCTAssertFalse(
            OutboxDeliveryService.isSafeRequest(
                payload(
                    route: .general,
                    context: CaptureContext(dryRun: false, allowWrite: true, allowFireflyWrite: false)
                )
            )
        )
        XCTAssertFalse(
            OutboxDeliveryService.isSafeRequest(
                payload(
                    route: .general,
                    context: CaptureContext(dryRun: false, allowWrite: true, allowFireflyWrite: true)
                )
            )
        )
        XCTAssertFalse(
            OutboxDeliveryService.isSafeRequest(
                payload(
                    route: .general,
                    context: CaptureContext(
                        userConfirmation: true,
                        dryRun: false,
                        allowWrite: true,
                        allowFireflyWrite: false,
                        confirmationToken: "v2.expense-token",
                        operationID: "exp_0123456789abcdef0123456789abcdef"
                    )
                )
            )
        )

        let operationID = "exp_0123456789abcdef0123456789abcdef"
        let cancellation = CaptureContext(
            userConfirmation: false,
            userCancellation: true,
            dryRun: false,
            allowWrite: true,
            allowFireflyWrite: false,
            confirmationToken: nil,
            operationID: operationID
        )
        XCTAssertTrue(
            OutboxDeliveryService.isSafeRequest(
                payload(route: .expense, context: cancellation)
            )
        )

        let unsafeCancellations: [(String, CaptureRoute, CaptureContext)] = [
            (
                "cross-domain-with-operation",
                .general,
                cancellation
            ),
            (
                "cross-domain-without-operation",
                .general,
                CaptureContext(
                    userCancellation: true,
                    dryRun: false,
                    allowWrite: true,
                    allowFireflyWrite: false
                )
            ),
            (
                "missing-operation",
                .expense,
                CaptureContext(
                    userCancellation: true,
                    dryRun: false,
                    allowWrite: true,
                    allowFireflyWrite: false
                )
            ),
            (
                "malformed-operation",
                .expense,
                CaptureContext(
                    userCancellation: true,
                    dryRun: false,
                    allowWrite: true,
                    allowFireflyWrite: false,
                    operationID: "expense-not-server-bound"
                )
            ),
            (
                "firefly-capability",
                .expense,
                CaptureContext(
                    userCancellation: true,
                    dryRun: false,
                    allowWrite: true,
                    allowFireflyWrite: true,
                    operationID: operationID
                )
            ),
            (
                "missing-explicit-firefly-denial",
                .expense,
                CaptureContext(
                    userCancellation: true,
                    dryRun: false,
                    allowWrite: true,
                    allowFireflyWrite: nil,
                    operationID: operationID
                )
            ),
            (
                "confirmation-and-cancellation",
                .expense,
                CaptureContext(
                    userConfirmation: true,
                    userCancellation: true,
                    dryRun: false,
                    allowWrite: true,
                    allowFireflyWrite: false,
                    operationID: operationID
                )
            ),
            (
                "confirmation-token",
                .expense,
                CaptureContext(
                    userCancellation: true,
                    dryRun: false,
                    allowWrite: true,
                    allowFireflyWrite: false,
                    confirmationToken: "v2.must-not-be-present",
                    operationID: operationID
                )
            ),
            (
                "dry-run",
                .expense,
                CaptureContext(
                    userCancellation: true,
                    dryRun: true,
                    allowWrite: false,
                    allowFireflyWrite: false,
                    operationID: operationID
                )
            )
        ]
        for (label, route, context) in unsafeCancellations {
            XCTAssertFalse(
                OutboxDeliveryService.isSafeRequest(payload(route: route, context: context)),
                "Unsafe cancellation accepted: \(label)"
            )
        }

        XCTAssertFalse(
            OutboxDeliveryService.isSafeRequest(
                payload(
                    route: .grocery,
                    context: CaptureContext(dryRun: true, allowWrite: true, allowFireflyWrite: false)
                )
            )
        )
    }

    func testFallbackEligibilityOnlyAllowsTransientTransportFailures() {
        XCTAssertTrue(OutboxDeliveryFailure.network(-1009).isTransientTransportFailure)
        XCTAssertTrue(OutboxDeliveryFailure.http(502).isTransientTransportFailure)
        XCTAssertTrue(OutboxDeliveryFailure.http(503).isTransientTransportFailure)
        XCTAssertTrue(OutboxDeliveryFailure.http(504).isTransientTransportFailure)

        XCTAssertFalse(OutboxDeliveryFailure.http(400).isTransientTransportFailure)
        XCTAssertFalse(OutboxDeliveryFailure.http(401).isTransientTransportFailure)
        XCTAssertFalse(OutboxDeliveryFailure.invalidResponse.isTransientTransportFailure)
        XCTAssertFalse(OutboxDeliveryFailure.unsafeResponse.isTransientTransportFailure)
        XCTAssertFalse(OutboxDeliveryFailure.unknown.isTransientTransportFailure)
    }

    func testFileOutboxPersistsAndDeduplicatesRequestID() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let fileURL = directory.appendingPathComponent("outbox.json")
        defer { try? FileManager.default.removeItem(at: directory) }

        let payload = CapturePayloadV1(
            requestID: "outbox-request-1",
            createdAt: "2026-07-13T20:00:00Z",
            source: CaptureSource(
                appVersion: "0.1.0",
                platform: "watchOS",
                osVersion: "26.5",
                deviceID: "unit-device",
                surface: "watch_app"
            ),
            route: .expense,
            capture: CaptureText(modality: "watch_dictation", text: "45 mil en Uber")
        )
        let store = FileOutboxStore(fileURL: fileURL)

        let first = try await store.enqueue(payload, now: payload.createdAt)
        let duplicate = try await store.enqueue(payload, now: payload.createdAt)
        var items = try await store.loadAll()

        XCTAssertEqual(first.id, "outbox-request-1")
        XCTAssertEqual(duplicate.id, first.id)
        XCTAssertEqual(items.count, 1)
        XCTAssertEqual(items.first?.status, .pending)

        try await store.markSending(requestID: payload.requestID, now: "2026-07-13T20:00:01Z")
        try await store.markSent(
            requestID: payload.requestID,
            now: "2026-07-13T20:00:02Z",
            deliveryPath: .iPhoneFallback
        )
        items = try await store.loadAll()

        XCTAssertEqual(items.first?.status, .sent)
        XCTAssertEqual(items.first?.attempts, 1)
        XCTAssertEqual(items.first?.lastDeliveryPath, .iPhoneFallback)
        XCTAssertNil(items.first?.lastError)
    }

    func testOutboxItemDecodesLegacyJSONWithoutDeliveryPath() throws {
        let payload = CapturePayloadV1(
            requestID: "legacy-outbox-item",
            createdAt: "2026-07-22T12:00:00Z",
            source: CaptureSource(
                appVersion: "0.3.0",
                platform: "watchOS",
                osVersion: "26.5",
                deviceID: "unit-device",
                surface: "watch_app"
            ),
            route: .general,
            capture: CaptureText(modality: "watch_text", text: "legacy capture")
        )
        let item = OutboxItem(
            payload: payload,
            status: .sent,
            attempts: 1,
            createdAt: payload.createdAt,
            updatedAt: payload.createdAt
        )
        let data = try JSONEncoder.captureEncoder().encode([item])
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [[String: Any]])
        object[0].removeValue(forKey: "last_delivery_path")
        let legacyData = try JSONSerialization.data(withJSONObject: object)

        let decoded = try JSONDecoder.captureDecoder().decode([OutboxItem].self, from: legacyData)
        XCTAssertEqual(decoded.first?.status, .sent)
        XCTAssertNil(decoded.first?.lastDeliveryPath)
    }

    func testMultipleStoreInstancesDoNotLoseConcurrentEnqueues() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let fileURL = directory.appendingPathComponent("outbox.json")
        defer { try? FileManager.default.removeItem(at: directory) }

        try await withThrowingTaskGroup(of: Void.self) { group in
            for index in 0..<20 {
                group.addTask {
                    let payload = CapturePayloadV1(
                        requestID: "concurrent-\(index)",
                        createdAt: "2026-07-17T20:00:00Z",
                        source: CaptureSource(
                            appVersion: "0.1.0",
                            platform: "watchOS",
                            osVersion: "26.5",
                            deviceID: "unit-device",
                            surface: "app_intent_watch"
                        ),
                        route: .general,
                        capture: CaptureText(
                            modality: "app_intent",
                            text: "capture \(index)"
                        )
                    )
                    _ = try await FileOutboxStore(fileURL: fileURL)
                        .enqueue(payload, now: payload.createdAt)
                }
            }
            try await group.waitForAll()
        }

        let items = try await FileOutboxStore(fileURL: fileURL).loadAll()
        XCTAssertEqual(items.count, 20)
        XCTAssertEqual(Set(items.map(\.id)).count, 20)
    }

    func testEndpointValidatorRequiresHTTPSAndBuildsHealthURL() throws {
        let baseURL = try EndpointValidator.normalizedBaseURL(
            from: "  https://example.ts.net:8650/  "
        )

        XCTAssertEqual(baseURL.absoluteString, "https://example.ts.net:8650")
        XCTAssertEqual(
            EndpointValidator.healthURL(from: baseURL).absoluteString,
            "https://example.ts.net:8650/health"
        )
        XCTAssertEqual(
            EndpointValidator.captureURL(from: baseURL).absoluteString,
            "https://example.ts.net:8650/webhooks/mobile-capture-v1"
        )
        XCTAssertThrowsError(
            try EndpointValidator.normalizedBaseURL(from: "http://example.ts.net:8650")
        ) { error in
            XCTAssertEqual(error as? EndpointValidationError, .httpsRequired)
        }
        XCTAssertThrowsError(
            try EndpointValidator.normalizedBaseURL(from: "https://100.64.0.1:8650")
        ) { error in
            XCTAssertEqual(error as? EndpointValidationError, .dnsNameRequired)
        }
    }
}
