import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import XCTest
@testable import PersonalWatchCore

final class OutboxDeliveryServiceTests: XCTestCase {
    func testSuccessfulDeliveryMarksItemSent() async throws {
        let fixture = try await makeFixture(requestID: "delivery-success")
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        let responseBody = Data("""
        {
          "status":"accepted",
          "dry_run":true,
          "request_id":"delivery-success",
          "domain":"argos.general_capture",
          "display_message":"Captura validada · dry-run ✅",
          "plan":{"would_write":false,"side_effects":[]}
        }
        """.utf8)
        let session = URLSessionStub { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertNotNil(request.value(forHTTPHeaderField: WebhookHeaders.signatureV2))
            XCTAssertEqual(request.value(forHTTPHeaderField: WebhookHeaders.requestID), "delivery-success")
            let response = try XCTUnwrap(
                HTTPURLResponse(
                    url: request.url!,
                    statusCode: 200,
                    httpVersion: nil,
                    headerFields: ["Content-Type": "application/json"]
                )
            )
            return (responseBody, response)
        }
        let client = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: session
        )
        let delivery = OutboxDeliveryService(
            store: fixture.store,
            client: client,
            nowISO8601: { "2026-07-13T22:00:01Z" }
        )

        let response = try await delivery.deliver(
            payload: fixture.payload,
            secret: "unit-test-secret"
        )
        let items = try await fixture.store.loadAll()

        XCTAssertEqual(response.displayMessage, "Captura validada · dry-run ✅")
        XCTAssertEqual(items.first?.status, .sent)
        XCTAssertEqual(items.first?.attempts, 1)
        XCTAssertEqual(items.first?.lastDeliveryPath, .directHTTPS)
        XCTAssertNil(items.first?.lastError)
    }

    func testCommittedAllowlistedWriteMarksItemSent() async throws {
        let fixture = try await makeFixture(
            requestID: "delivery-grocery-write",
            route: .grocery,
            context: CaptureContext(
                dryRun: false,
                allowWrite: true,
                allowFireflyWrite: false
            )
        )
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        let responseBody = Data("""
        {
          "status":"committed",
          "dry_run":false,
          "write_committed":true,
          "request_id":"delivery-grocery-write",
          "domain":"aura.grocery_capture",
          "display_message":"Mercado actualizado ✅ · leche (faltante)",
          "plan":{"would_write":true,"side_effects":["notion.official_grocery_list.write"]}
        }
        """.utf8)
        let client = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: URLSessionStub { request in
                let response = try XCTUnwrap(
                    HTTPURLResponse(
                        url: request.url!,
                        statusCode: 200,
                        httpVersion: nil,
                        headerFields: ["Content-Type": "application/json"]
                    )
                )
                return (responseBody, response)
            }
        )
        let delivery = OutboxDeliveryService(
            store: fixture.store,
            client: client,
            nowISO8601: { "2026-08-04T23:30:01Z" }
        )

        let response = try await delivery.deliver(
            payload: fixture.payload,
            secret: "unit-test-secret"
        )
        let items = try await fixture.store.loadAll()

        XCTAssertEqual(response.writeCommitted, true)
        XCTAssertEqual(items.first?.status, .sent)
        XCTAssertEqual(items.first?.lastDeliveryPath, .directHTTPS)
    }

    func testLegacyGroceryRefusalFailsSafelyUntilSameRequestReceivesNormalizedNoWriteResponse() async throws {
        let fixture = try await makeFixture(
            requestID: "delivery-grocery-legacy-refusal",
            route: .grocery,
            context: CaptureContext(dryRun: false, allowWrite: true, allowFireflyWrite: false),
            captureText: "synthetic grocery item"
        )
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let canonicalBody = try JSONEncoder.captureEncoder().encode(fixture.payload)
        func client(returning body: Data) -> WebhookClient {
            WebhookClient(
                endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
                session: URLSessionStub { request in
                    XCTAssertEqual(request.httpBody, canonicalBody)
                    XCTAssertEqual(request.value(forHTTPHeaderField: WebhookHeaders.requestID), fixture.payload.requestID)
                    let response = try XCTUnwrap(
                        HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)
                    )
                    return (body, response)
                }
            )
        }

        let legacyBody = Data("""
        {
          "status":"needs_confirmation",
          "dry_run":true,
          "write_committed":false,
          "request_id":"delivery-grocery-legacy-refusal",
          "domain":"aura.grocery_capture",
          "display_message":"No parece un artículo de mercado · no cambié nada",
          "question":"¿Qué artículo quieres agregar o marcar como comprado?",
          "plan":{
            "would_write":false,
            "side_effects":["notion.official_grocery_list.plan_only"],
            "notes":"Would update official grocery list; unchecked means missing."
          },
          "gates_missing":["server domain allowlist","external write adapter"]
        }
        """.utf8)
        let firstAttempt = OutboxDeliveryService(
            store: fixture.store,
            client: client(returning: legacyBody),
            nowISO8601: { "2026-07-13T22:00:01Z" }
        )
        do {
            _ = try await firstAttempt.deliver(payload: fixture.payload, secret: "unit-test-secret")
            XCTFail("A live grocery must not accept the legacy dry-run refusal")
        } catch let failure as OutboxDeliveryFailure {
            XCTAssertEqual(failure, .unsafeResponse)
        }
        let failedItems = try await fixture.store.loadAll()
        XCTAssertEqual(failedItems.count, 1)
        let failed = try XCTUnwrap(failedItems.first)
        XCTAssertEqual(failed.payload, fixture.payload)
        XCTAssertEqual(failed.id, fixture.payload.requestID)
        XCTAssertEqual(failed.status, .failed)
        XCTAssertEqual(failed.attempts, 1)
        XCTAssertEqual(failed.lastError, "unsafe_response")
        XCTAssertNil(failed.lastResponse)
        let pending = try await fixture.store.loadDeliverable()
        XCTAssertEqual(pending.map(\.id), [fixture.payload.requestID])

        let normalizedBody = Data("""
        {
          "status":"needs_input",
          "dry_run":false,
          "write_committed":false,
          "request_id":"delivery-grocery-legacy-refusal",
          "domain":"aura.grocery_capture",
          "display_message":"No identifiqué el artículo · no cambié nada",
          "question":"¿Qué artículo quieres agregar al mercado?",
          "plan":{"would_write":false,"side_effects":[],"notes":"Live grocery clarification; no external write was attempted."},
          "gates_missing":[]
        }
        """.utf8)
        let retry = OutboxDeliveryService(
            store: fixture.store,
            client: client(returning: normalizedBody),
            nowISO8601: { "2026-07-13T22:00:02Z" }
        )
        let response = try await retry.deliver(payload: fixture.payload, secret: "unit-test-secret")
        XCTAssertEqual(response.status, "needs_input")
        XCTAssertEqual(response.dryRun, false)
        XCTAssertEqual(response.writeCommitted, false)
        XCTAssertEqual(response.plan?.wouldWrite, false)
        XCTAssertEqual(response.plan?.notes, "Live grocery clarification; no external write was attempted.")
        XCTAssertEqual(response.displayMessage, "No identifiqué el artículo · no cambié nada")
        XCTAssertEqual(response.question, "¿Qué artículo quieres agregar al mercado?")

        let reopened = FileOutboxStore(fileURL: fixture.directory.appendingPathComponent("outbox.json"))
        let retainedItems = try await reopened.loadAll()
        XCTAssertEqual(retainedItems.count, 1)
        let retained = try XCTUnwrap(retainedItems.first)
        XCTAssertEqual(retained.payload, fixture.payload)
        XCTAssertEqual(retained.id, failed.id)
        XCTAssertEqual(retained.status, .sent)
        XCTAssertEqual(retained.attempts, 2)
        XCTAssertEqual(retained.lastResponse, response)
        XCTAssertEqual(retained.lastDeliveryPath, .directHTTPS)
        XCTAssertNil(retained.lastError)
        let remaining = try await reopened.loadDeliverable()
        XCTAssertTrue(remaining.isEmpty)
    }

    func testCommittedReminderWriteWithCalendarFailSoftMarksItemSent() async throws {
        let fixture = try await makeFixture(
            requestID: "delivery-reminder-write",
            route: .reminder,
            context: CaptureContext(
                dryRun: false,
                allowWrite: true,
                allowFireflyWrite: false
            )
        )
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        let responseBody = Data("""
        {
          "status":"committed",
          "dry_run":false,
          "write_committed":true,
          "request_id":"delivery-reminder-write",
          "domain":"aura.reminder_capture",
          "display_message":"Recordatorio guardado ✅ · Llamar a mamá · Calendar pendiente",
          "calendar_synced":false,
          "calendar_sync_pending":true,
          "external_ids":{"notion_page_id":"notion-page"},
          "plan":{"would_write":true,"side_effects":["notion.task.write","google_calendar.best_effort"]}
        }
        """.utf8)
        let client = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: URLSessionStub { request in
                let response = try XCTUnwrap(
                    HTTPURLResponse(
                        url: request.url!,
                        statusCode: 200,
                        httpVersion: nil,
                        headerFields: ["Content-Type": "application/json"]
                    )
                )
                return (responseBody, response)
            }
        )
        let delivery = OutboxDeliveryService(store: fixture.store, client: client)

        let response = try await delivery.deliver(
            payload: fixture.payload,
            secret: "unit-test-secret"
        )
        let items = try await fixture.store.loadAll()

        XCTAssertEqual(response.writeCommitted, true)
        XCTAssertEqual(response.displayMessage, "Recordatorio guardado ✅ · Llamar a mamá · Calendar pendiente")
        XCTAssertEqual(items.first?.status, .sent)
        XCTAssertEqual(items.first?.lastDeliveryPath, .directHTTPS)
    }

    func testRejectedLiveReminderNoWriteMarksItemSent() async throws {
        let fixture = try await makeFixture(
            requestID: "delivery-reminder-rejected",
            route: .reminder,
            context: CaptureContext(
                dryRun: false,
                allowWrite: true,
                allowFireflyWrite: false
            )
        )
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        let responseBody = Data("""
        {
          "status":"rejected",
          "dry_run":false,
          "write_committed":false,
          "request_id":"delivery-reminder-rejected",
          "domain":"aura.reminder_capture",
          "display_message":"Solo creo recordatorios nuevos · no cambié nada",
          "plan":{"would_write":false,"side_effects":["notion.task.plan_only"]}
        }
        """.utf8)
        let client = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: URLSessionStub { request in
                let response = try XCTUnwrap(
                    HTTPURLResponse(
                        url: request.url!,
                        statusCode: 200,
                        httpVersion: nil,
                        headerFields: ["Content-Type": "application/json"]
                    )
                )
                return (responseBody, response)
            }
        )
        let delivery = OutboxDeliveryService(store: fixture.store, client: client)

        let response = try await delivery.deliver(
            payload: fixture.payload,
            secret: "unit-test-secret"
        )
        let items = try await fixture.store.loadAll()

        XCTAssertEqual(response.status, "rejected")
        XCTAssertEqual(response.writeCommitted, false)
        XCTAssertEqual(response.displayMessage, "Solo creo recordatorios nuevos · no cambié nada")
        XCTAssertEqual(items.first?.status, .sent)
        XCTAssertEqual(items.first?.lastDeliveryPath, .directHTTPS)
    }

    func testDuplicateTerminalNoWriteReminderMarksItemSent() async throws {
        let fixture = try await makeFixture(
            requestID: "delivery-reminder-rejected-replay",
            route: .reminder,
            context: CaptureContext(
                dryRun: false,
                allowWrite: true,
                allowFireflyWrite: false
            )
        )
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        let responseBody = Data("""
        {
          "status":"duplicate",
          "dry_run":false,
          "write_committed":false,
          "request_id":"delivery-reminder-rejected-replay",
          "domain":"aura.reminder_capture",
          "display_message":"Solo creo recordatorios nuevos · no cambié nada",
          "duplicate":true,
          "plan":{"would_write":false,"side_effects":["notion.task.plan_only"]}
        }
        """.utf8)
        let client = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: URLSessionStub { request in
                let response = try XCTUnwrap(
                    HTTPURLResponse(
                        url: request.url!,
                        statusCode: 200,
                        httpVersion: nil,
                        headerFields: ["Content-Type": "application/json"]
                    )
                )
                return (responseBody, response)
            }
        )
        let delivery = OutboxDeliveryService(store: fixture.store, client: client)

        let response = try await delivery.deliver(
            payload: fixture.payload,
            secret: "unit-test-secret"
        )
        let items = try await fixture.store.loadAll()

        XCTAssertEqual(response.status, "duplicate")
        XCTAssertEqual(response.duplicate, true)
        XCTAssertEqual(items.first?.status, .sent)
        XCTAssertEqual(items.first?.lastDeliveryPath, .directHTTPS)
    }

    func testLiveExpenseResponseIsRejectedEvenIfItClaimsCommit() async throws {
        let fixture = try await makeFixture(
            requestID: "delivery-expense-live-blocked",
            route: .expense,
            context: CaptureContext(
                dryRun: false,
                allowWrite: true,
                allowFireflyWrite: false
            )
        )
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        let responseBody = Data("""
        {
          "status":"committed",
          "dry_run":false,
          "write_committed":true,
          "request_id":"delivery-expense-live-blocked",
          "domain":"megan.expense_capture",
          "plan":{"would_write":true}
        }
        """.utf8)
        let client = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: URLSessionStub { request in
                let response = try XCTUnwrap(
                    HTTPURLResponse(
                        url: request.url!,
                        statusCode: 200,
                        httpVersion: nil,
                        headerFields: nil
                    )
                )
                return (responseBody, response)
            }
        )
        let delivery = OutboxDeliveryService(store: fixture.store, client: client)

        do {
            _ = try await delivery.deliver(payload: fixture.payload, secret: "unit-test-secret")
            XCTFail("Expected a non-allowlisted live route to be rejected")
        } catch let failure as OutboxDeliveryFailure {
            XCTAssertEqual(failure, .unsafeResponse)
        }

        let items = try await fixture.store.loadAll()
        XCTAssertEqual(items.first?.status, .failed)
        XCTAssertEqual(items.first?.lastError, "unsafe_response")
    }

    func testExpensePreviewRequiresCorrelatedOperationAndSignedSummary() async throws {
        let fixture = try await makeFixture(
            requestID: "delivery-expense-preview",
            route: .expense,
            context: CaptureContext(dryRun: false, allowWrite: true, allowFireflyWrite: true)
        )
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let responseBody = Data("""
        {
          "status":"needs_confirmation",
          "dry_run":false,
          "write_committed":false,
          "request_id":"delivery-expense-preview",
          "domain":"megan.expense_capture",
          "operation_id":"exp_0123456789abcdef0123456789abcdef",
          "confirmation_token":"v2.server-token",
          "confirmation":{"amount_cop":45000,"description":"Almuerzos","source_name":"Nequi","category_name":"Food","budget_name":"Comidas afuera"},
          "plan":{"would_write":false,"side_effects":[]}
        }
        """.utf8)
        let client = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: URLSessionStub { request in
                let response = try XCTUnwrap(
                    HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)
                )
                return (responseBody, response)
            }
        )
        let delivery = OutboxDeliveryService(store: fixture.store, client: client)

        let response = try await delivery.deliver(payload: fixture.payload, secret: "unit-test-secret")
        XCTAssertEqual(response.status, "needs_confirmation")
        XCTAssertEqual(response.operationID, "exp_0123456789abcdef0123456789abcdef")
        let items = try await fixture.store.loadAll()
        XCTAssertEqual(items.first?.status, .sent)
        XCTAssertEqual(items.first?.lastResponse, response)
        let pending = try await fixture.store.loadPendingExpenseConfirmations()
        XCTAssertEqual(pending.map(\.id), [fixture.payload.requestID])

        try await fixture.store.consumeExpenseConfirmation(
            operationID: "exp_0123456789abcdef0123456789abcdef",
            now: "2026-07-13T22:05:00Z"
        )
        let pendingAfterConsumption = try await fixture.store.loadPendingExpenseConfirmations()
        XCTAssertTrue(pendingAfterConsumption.isEmpty)
    }

    func testCancelledExpenseDeliveryConsumesPendingConfirmation() async throws {
        let operationID = "exp_0123456789abcdef0123456789abcdef"
        let fixture = try await makeFixture(
            requestID: "delivery-expense-cancellation",
            route: .expense,
            context: CaptureContext(
                userCancellation: true,
                dryRun: false,
                allowWrite: true,
                allowFireflyWrite: false,
                operationID: operationID
            )
        )
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        let previewPayload = CapturePayloadV1(
            requestID: "delivery-expense-preview-before-cancel",
            createdAt: "2026-07-13T21:59:00Z",
            source: fixture.payload.source,
            route: .expense,
            capture: fixture.payload.capture,
            context: CaptureContext(
                dryRun: false,
                allowWrite: true,
                allowFireflyWrite: true
            )
        )
        _ = try await fixture.store.enqueue(previewPayload, now: previewPayload.createdAt)
        let previewResponse = CaptureResponseV1(
            status: "needs_confirmation",
            dryRun: false,
            requestID: previewPayload.requestID,
            domain: previewPayload.route.domain.rawValue,
            operationID: operationID,
            confirmationToken: "v2.server-token",
            confirmation: ExpenseConfirmationSummary(
                amountCOP: 45_000,
                description: "Almuerzos",
                sourceName: "Nequi",
                categoryName: "Food",
                budgetName: "Comidas afuera"
            ),
            writeCommitted: false,
            plan: CaptureResponsePlan(sideEffects: [], wouldWrite: false)
        )
        try await fixture.store.markSent(
            requestID: previewPayload.requestID,
            now: "2026-07-13T21:59:01Z",
            deliveryPath: .directHTTPS,
            response: previewResponse
        )
        // makeFixture already persisted the cancellation intent. Recovery must
        // hide its preview immediately, before any server acknowledgment.
        let pendingBeforeCancellation = try await fixture.store.loadPendingExpenseConfirmations()
        XCTAssertTrue(pendingBeforeCancellation.isEmpty)
        let itemsBeforeCancellation = try await fixture.store.loadAll()
        let storedPreviewBeforeCancellation = try XCTUnwrap(
            itemsBeforeCancellation.first { $0.id == previewPayload.requestID }
        )
        XCTAssertEqual(storedPreviewBeforeCancellation.lastResponse, previewResponse)

        let responseBody = Data("""
        {
          "status":"cancelled",
          "dry_run":false,
          "write_committed":false,
          "request_id":"delivery-expense-cancellation",
          "domain":"megan.expense_capture",
          "operation_id":"\(operationID)",
          "display_message":"Gasto cancelado · no se registró nada",
          "plan":{"would_write":false,"side_effects":[]}
        }
        """.utf8)
        let client = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: URLSessionStub { request in
                let body = try XCTUnwrap(request.httpBody)
                let submitted = try JSONDecoder.captureDecoder()
                    .decode(CapturePayloadV1.self, from: body)
                XCTAssertEqual(submitted.context.userCancellation, true)
                XCTAssertFalse(submitted.context.userConfirmation)
                XCTAssertEqual(submitted.context.allowFireflyWrite, false)
                XCTAssertNil(submitted.context.confirmationToken)
                XCTAssertEqual(submitted.context.operationID, operationID)
                let response = try XCTUnwrap(
                    HTTPURLResponse(
                        url: request.url!,
                        statusCode: 200,
                        httpVersion: nil,
                        headerFields: nil
                    )
                )
                return (responseBody, response)
            }
        )
        let delivery = OutboxDeliveryService(
            store: fixture.store,
            client: client,
            nowISO8601: { "2026-07-13T22:00:01Z" }
        )

        let response = try await delivery.deliver(
            payload: fixture.payload,
            secret: "unit-test-secret"
        )
        XCTAssertEqual(response.status, "cancelled")

        let items = try await fixture.store.loadAll()
        let previewItem = try XCTUnwrap(
            items.first { $0.payload.requestID == previewPayload.requestID }
        )
        let cancellationItem = try XCTUnwrap(
            items.first { $0.payload.requestID == fixture.payload.requestID }
        )
        XCTAssertEqual(previewItem.lastResponse, previewResponse)
        XCTAssertNotNil(previewItem.confirmationInvalidatedAt)
        XCTAssertEqual(cancellationItem.status, .sent)
        XCTAssertEqual(cancellationItem.lastResponse, response)
        let pendingAfterCancellation = try await fixture.store.loadPendingExpenseConfirmations()
        XCTAssertTrue(pendingAfterCancellation.isEmpty)
    }

    func testExpenseNeedsConfirmationIsReservedForCompleteSignedPreview() async throws {
        let fixture = try await makeFixture(
            requestID: "delivery-expense-incomplete-preview",
            route: .expense,
            context: CaptureContext(dryRun: false, allowWrite: true, allowFireflyWrite: true)
        )
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        let terminalPlan = CaptureResponsePlan(sideEffects: [], wouldWrite: false)
        let needsInput = CaptureResponseV1(
            status: "needs_input",
            dryRun: false,
            requestID: fixture.payload.requestID,
            domain: fixture.payload.route.domain.rawValue,
            writeCommitted: false,
            plan: terminalPlan
        )
        let rejected = CaptureResponseV1(
            status: "rejected",
            dryRun: false,
            requestID: fixture.payload.requestID,
            domain: fixture.payload.route.domain.rawValue,
            writeCommitted: false,
            plan: terminalPlan
        )
        let incompletePreview = CaptureResponseV1(
            status: "needs_confirmation",
            dryRun: false,
            requestID: fixture.payload.requestID,
            domain: fixture.payload.route.domain.rawValue,
            writeCommitted: false,
            plan: terminalPlan
        )

        XCTAssertTrue(OutboxDeliveryService.isSafe(response: needsInput, for: fixture.payload))
        XCTAssertTrue(OutboxDeliveryService.isSafe(response: rejected, for: fixture.payload))
        XCTAssertFalse(OutboxDeliveryService.isSafe(response: incompletePreview, for: fixture.payload))
    }

    func testExpiredConfirmedExpenseCanReceiveFreshTokenForSameOperationOnly() async throws {
        let operationID = "exp_0123456789abcdef0123456789abcdef"
        let fixture = try await makeFixture(
            requestID: "delivery-expense-refresh",
            route: .expense,
            context: CaptureContext(
                userConfirmation: true,
                dryRun: false,
                allowWrite: true,
                allowFireflyWrite: true,
                confirmationToken: "v2.expired-token",
                operationID: operationID
            )
        )
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        let summary = ExpenseConfirmationSummary(
            amountCOP: 45_000,
            description: "Almuerzos",
            sourceName: "Nequi",
            categoryName: "Food",
            budgetName: "Comidas afuera"
        )
        let refreshed = CaptureResponseV1(
            status: "needs_confirmation",
            dryRun: false,
            requestID: fixture.payload.requestID,
            domain: fixture.payload.route.domain.rawValue,
            operationID: operationID,
            confirmationToken: "v2.fresh-token",
            confirmation: summary,
            writeCommitted: false,
            plan: CaptureResponsePlan(sideEffects: [], wouldWrite: false)
        )
        var mismatched = refreshed
        mismatched.operationID = "exp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

        XCTAssertTrue(OutboxDeliveryService.isSafe(response: refreshed, for: fixture.payload))
        XCTAssertFalse(OutboxDeliveryService.isSafe(response: mismatched, for: fixture.payload))
    }

    func testExpenseCancellationResponseRequiresSafeTerminalCorrelation() async throws {
        let operationID = "exp_0123456789abcdef0123456789abcdef"
        let fixture = try await makeFixture(
            requestID: "delivery-expense-cancellation-safety",
            route: .expense,
            context: CaptureContext(
                userConfirmation: false,
                userCancellation: true,
                dryRun: false,
                allowWrite: true,
                allowFireflyWrite: false,
                confirmationToken: nil,
                operationID: operationID
            ),
            captureText: "45 mil almuerzo por Nequi"
        )
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        let safePlan = CaptureResponsePlan(sideEffects: [], wouldWrite: false)
        let cancelled = CaptureResponseV1(
            status: "cancelled",
            dryRun: false,
            requestID: fixture.payload.requestID,
            domain: CaptureDomain.meganExpenseCapture.rawValue,
            operationID: operationID,
            duplicate: false,
            writeCommitted: false,
            plan: safePlan
        )
        var duplicate = cancelled
        duplicate.status = "duplicate"
        duplicate.duplicate = true

        XCTAssertTrue(OutboxDeliveryService.isSafe(response: cancelled, for: fixture.payload))
        XCTAssertTrue(OutboxDeliveryService.isSafe(response: duplicate, for: fixture.payload))

        var wrongStatus = cancelled
        wrongStatus.status = "rejected"
        var wrongRequest = cancelled
        wrongRequest.requestID = "different-cancellation-request"
        var wrongDomain = cancelled
        wrongDomain.domain = CaptureDomain.auraGroceryCapture.rawValue
        var wrongOperation = cancelled
        wrongOperation.operationID = "exp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        var missingOperation = cancelled
        missingOperation.operationID = nil
        var claimsWrite = cancelled
        claimsWrite.writeCommitted = true
        var wouldWrite = cancelled
        wouldWrite.plan = CaptureResponsePlan(sideEffects: ["firefly.withdrawal.write"], wouldWrite: true)
        var dryRun = cancelled
        dryRun.dryRun = true
        var carriesToken = cancelled
        carriesToken.confirmationToken = "v2.must-not-return-on-cancel"

        let unsafeResponses: [(String, CaptureResponseV1)] = [
            ("wrong-status", wrongStatus),
            ("wrong-request", wrongRequest),
            ("wrong-domain", wrongDomain),
            ("wrong-operation", wrongOperation),
            ("missing-operation", missingOperation),
            ("claims-write", claimsWrite),
            ("would-write", wouldWrite),
            ("dry-run", dryRun),
            ("carries-token", carriesToken)
        ]
        for (label, response) in unsafeResponses {
            XCTAssertFalse(
                OutboxDeliveryService.isSafe(response: response, for: fixture.payload),
                "Unsafe cancellation response accepted: \(label)"
            )
        }
    }

    func testCancellationTransportFailureKeepsIntentQueuedAndHidesPendingPreview() async throws {
        let operationID = "exp_0123456789abcdef0123456789abcdef"
        let capturedText = "45 mil almuerzo por Nequi"
        let fixture = try await makeFixture(
            requestID: "delivery-expense-cancellation-offline",
            route: .expense,
            context: CaptureContext(
                userCancellation: true,
                dryRun: false,
                allowWrite: true,
                allowFireflyWrite: false,
                operationID: operationID
            ),
            captureText: capturedText
        )
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let preview = try await seedExpensePreview(
            in: fixture.store,
            requestID: "delivery-expense-preview-before-offline-cancel",
            operationID: operationID,
            text: capturedText
        )
        let client = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: URLSessionStub { _ in
                throw URLError(.notConnectedToInternet)
            }
        )
        let delivery = OutboxDeliveryService(
            store: fixture.store,
            client: client,
            nowISO8601: { "2026-07-13T22:00:01Z" }
        )

        do {
            _ = try await delivery.deliver(payload: fixture.payload, secret: "unit-test-secret")
            XCTFail("Expected cancellation transport failure")
        } catch let failure as OutboxDeliveryFailure {
            XCTAssertEqual(failure, .network(URLError.notConnectedToInternet.rawValue))
        }

        let pending = try await fixture.store.loadPendingExpenseConfirmations()
        let items = try await fixture.store.loadAll()
        let cancellationItem = items.first { $0.id == fixture.payload.requestID }
        let previewItem = items.first { $0.id == preview.requestID }
        XCTAssertTrue(pending.isEmpty)
        XCTAssertEqual(cancellationItem?.status, .failed)
        XCTAssertEqual(cancellationItem?.attempts, 1)
        XCTAssertEqual(cancellationItem?.lastError, "network_\(URLError.notConnectedToInternet.rawValue)")
        XCTAssertEqual(previewItem?.status, .sent)
        XCTAssertEqual(previewItem?.lastResponse?.status, "needs_confirmation")
        XCTAssertEqual(previewItem?.lastResponse?.operationID, operationID)
    }

    func testSafeCancellationDeliveryAtomicallyConsumesOnlyMatchingStoredPreview() async throws {
        let operationID = "exp_0123456789abcdef0123456789abcdef"
        let otherOperationID = "exp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        let capturedText = "45 mil almuerzo por Nequi"
        let fixture = try await makeFixture(
            requestID: "delivery-expense-cancellation-success",
            route: .expense,
            context: CaptureContext(
                userCancellation: true,
                dryRun: false,
                allowWrite: true,
                allowFireflyWrite: false,
                operationID: operationID
            ),
            captureText: capturedText
        )
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let preview = try await seedExpensePreview(
            in: fixture.store,
            requestID: "delivery-expense-preview-before-cancel",
            operationID: operationID,
            text: capturedText
        )
        let otherPreview = try await seedExpensePreview(
            in: fixture.store,
            requestID: "delivery-expense-other-preview",
            operationID: otherOperationID,
            text: "80 mil mercado D1"
        )
        let responseBody = Data("""
        {
          "status":"cancelled",
          "dry_run":false,
          "write_committed":false,
          "request_id":"delivery-expense-cancellation-success",
          "domain":"megan.expense_capture",
          "operation_id":"exp_0123456789abcdef0123456789abcdef",
          "display_message":"Gasto cancelado · no se registró nada",
          "duplicate":false,
          "plan":{"would_write":false,"side_effects":[]}
        }
        """.utf8)
        let client = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: URLSessionStub { request in
                let response = try XCTUnwrap(
                    HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)
                )
                return (responseBody, response)
            }
        )
        let delivery = OutboxDeliveryService(store: fixture.store, client: client)

        let response = try await delivery.deliver(payload: fixture.payload, secret: "unit-test-secret")
        let pendingAfterDelivery = try await fixture.store.loadPendingExpenseConfirmations()
        let items = try await fixture.store.loadAll()
        let cancellationItem = items.first { $0.id == fixture.payload.requestID }
        let previewItem = items.first { $0.id == preview.requestID }
        let otherPreviewItem = items.first { $0.id == otherPreview.requestID }
        let consumedUnknown = try await fixture.store.consumeExpenseConfirmation(
            operationID: "exp_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            now: "2026-07-13T22:06:00Z"
        )

        XCTAssertEqual(response.status, "cancelled")
        XCTAssertFalse(consumedUnknown)
        XCTAssertEqual(pendingAfterDelivery.map(\.id), [otherPreview.requestID])
        XCTAssertEqual(previewItem?.lastResponse?.operationID, operationID)
        XCTAssertNotNil(previewItem?.confirmationInvalidatedAt)
        XCTAssertEqual(otherPreviewItem?.lastResponse?.operationID, otherOperationID)
        XCTAssertEqual(cancellationItem?.status, .sent)
        XCTAssertEqual(cancellationItem?.lastResponse?.status, "cancelled")
        XCTAssertEqual(cancellationItem?.lastResponse?.operationID, operationID)
    }

    func testCommittedExpenseWithScopedFireflyFlagMarksItemSent() async throws {
        let fixture = try await makeFixture(
            requestID: "delivery-expense-live",
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
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let responseBody = Data("""
        {
          "status":"committed",
          "dry_run":false,
          "write_committed":true,
          "request_id":"delivery-expense-live",
          "domain":"megan.expense_capture",
          "operation_id":"exp_0123456789abcdef0123456789abcdef",
          "plan":{"would_write":true,"side_effects":["firefly.withdrawal.write"]}
        }
        """.utf8)
        let client = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: URLSessionStub { request in
                let response = try XCTUnwrap(HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil))
                return (responseBody, response)
            }
        )
        let delivery = OutboxDeliveryService(store: fixture.store, client: client)

        let response = try await delivery.deliver(payload: fixture.payload, secret: "unit-test-secret")
        XCTAssertEqual(response.writeCommitted, true)
        let items = try await fixture.store.loadAll()
        XCTAssertEqual(items.first?.status, .sent)
    }

    func testGeneralLiveRunIsBlockedBeforeTransport() async throws {
        let fixture = try await makeFixture(
            requestID: "delivery-general-run",
            route: .general,
            context: CaptureContext(dryRun: false, allowWrite: true, allowFireflyWrite: false)
        )
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let responseBody = Data("""
        {
          "status":"queued",
          "dry_run":false,
          "write_committed":false,
          "request_id":"delivery-general-run",
          "domain":"argos.general_capture",
          "run_id":"run_0123456789abcdef0123456789abcdef",
          "plan":{"would_write":false,"side_effects":["hermes.agent_run.queued"]}
        }
        """.utf8)
        let client = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: URLSessionStub { request in
                XCTFail("General live requests must not reach transport")
                let response = try XCTUnwrap(HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil))
                return (responseBody, response)
            }
        )
        let delivery = OutboxDeliveryService(store: fixture.store, client: client)

        do {
            _ = try await delivery.deliver(payload: fixture.payload, secret: "unit-test-secret")
            XCTFail("General is outside the MVP")
        } catch let error as OutboxDeliveryFailure { XCTAssertEqual(error, .unsafeResponse) }
        let items = try await fixture.store.loadAll()
        XCTAssertEqual(items.first?.status, .failed)
    }

    func testUnsafeGeneralRunResponsesAreRejected() async throws {
        let cases: [(String, String)] = [
            (
                "missing-run-id",
                #"{"status":"queued","dry_run":false,"write_committed":false,"request_id":"delivery-general-missing-run-id","domain":"argos.general_capture","plan":{"would_write":false}}"#
            ),
            (
                "claims-write",
                #"{"status":"committed","dry_run":false,"write_committed":true,"request_id":"delivery-general-claims-write","domain":"argos.general_capture","run_id":"run_0123456789abcdef0123456789abcdef","plan":{"would_write":true}}"#
            )
        ]
        for (label, responseJSON) in cases {
            let requestID = "delivery-general-\(label)"
            let fixture = try await makeFixture(
                requestID: requestID,
                route: .general,
                context: CaptureContext(dryRun: false, allowWrite: true, allowFireflyWrite: false)
            )
            defer { try? FileManager.default.removeItem(at: fixture.directory) }
            let client = WebhookClient(
                endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
                session: URLSessionStub { request in
                    let response = try XCTUnwrap(HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil))
                    return (Data(responseJSON.utf8), response)
                }
            )
            let delivery = OutboxDeliveryService(store: fixture.store, client: client)
            do {
                _ = try await delivery.deliver(payload: fixture.payload, secret: "unit-test-secret")
                XCTFail("Expected unsafe general response \(label) to fail")
            } catch let failure as OutboxDeliveryFailure {
                XCTAssertEqual(failure, .unsafeResponse)
            }
        }
    }

    func testLiveResponseWithoutCommitProofIsRejected() async throws {
        let fixture = try await makeFixture(
            requestID: "delivery-unproven-write",
            route: .grocery,
            context: CaptureContext(dryRun: false, allowWrite: true)
        )
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        let responseBody = Data("""
        {
          "status":"accepted",
          "dry_run":false,
          "request_id":"delivery-unproven-write",
          "domain":"aura.grocery_capture",
          "plan":{"would_write":true}
        }
        """.utf8)
        let client = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: URLSessionStub { request in
                let response = try XCTUnwrap(
                    HTTPURLResponse(
                        url: request.url!,
                        statusCode: 200,
                        httpVersion: nil,
                        headerFields: nil
                    )
                )
                return (responseBody, response)
            }
        )
        let delivery = OutboxDeliveryService(store: fixture.store, client: client)

        do {
            _ = try await delivery.deliver(payload: fixture.payload, secret: "unit-test-secret")
            XCTFail("Expected unproven write response to be rejected")
        } catch let failure as OutboxDeliveryFailure {
            XCTAssertEqual(failure, .unsafeResponse)
        }

        let items = try await fixture.store.loadAll()
        XCTAssertEqual(items.first?.status, .failed)
        XCTAssertEqual(items.first?.lastError, "unsafe_response")
    }

    func testLiveWriteRejectsUnknownOrErrorStatusesEvenWithCommitProof() async throws {
        for (index, status) in ["error", "partial_error", "accepted", "future_status"].enumerated() {
            let requestID = "delivery-unsafe-status-\(index)"
            let fixture = try await makeFixture(
                requestID: requestID,
                route: .reminder,
                context: CaptureContext(dryRun: false, allowWrite: true)
            )
            defer { try? FileManager.default.removeItem(at: fixture.directory) }

            let responseBody = Data("""
            {
              "status":"\(status)",
              "dry_run":false,
              "write_committed":true,
              "request_id":"\(requestID)",
              "domain":"aura.reminder_capture",
              "plan":{"would_write":true}
            }
            """.utf8)
            let client = WebhookClient(
                endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
                session: URLSessionStub { request in
                    let response = try XCTUnwrap(
                        HTTPURLResponse(
                            url: request.url!,
                            statusCode: 200,
                            httpVersion: nil,
                            headerFields: nil
                        )
                    )
                    return (responseBody, response)
                }
            )
            let delivery = OutboxDeliveryService(store: fixture.store, client: client)

            do {
                _ = try await delivery.deliver(payload: fixture.payload, secret: "unit-test-secret")
                XCTFail("Expected status \(status) to be rejected")
            } catch let failure as OutboxDeliveryFailure {
                XCTAssertEqual(failure, .unsafeResponse)
            }

            let items = try await fixture.store.loadAll()
            XCTAssertEqual(items.first?.status, .failed)
            XCTAssertEqual(items.first?.lastError, "unsafe_response")
        }
    }

    func testCommittedDuplicateAndLiveGroceryConfirmationAreTerminalSafe() async throws {
        let cases: [(String, String, Bool, Bool)] = [
            ("duplicate", "delivery-duplicate-write", true, true),
            ("needs_confirmation", "delivery-grocery-confirmation", false, false),
        ]
        for (status, requestID, wouldWrite, writeCommitted) in cases {
            let fixture = try await makeFixture(
                requestID: requestID,
                route: .grocery,
                context: CaptureContext(dryRun: false, allowWrite: true)
            )
            defer { try? FileManager.default.removeItem(at: fixture.directory) }

            let responseBody = Data("""
            {
              "status":"\(status)",
              "dry_run":false,
              "write_committed":\(writeCommitted),
              "request_id":"\(requestID)",
              "domain":"aura.grocery_capture",
              "duplicate":\(status == "duplicate"),
              "plan":{"would_write":\(wouldWrite)}
            }
            """.utf8)
            let client = WebhookClient(
                endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
                session: URLSessionStub { request in
                    let response = try XCTUnwrap(
                        HTTPURLResponse(
                            url: request.url!,
                            statusCode: 200,
                            httpVersion: nil,
                            headerFields: nil
                        )
                    )
                    return (responseBody, response)
                }
            )
            let delivery = OutboxDeliveryService(store: fixture.store, client: client)

            _ = try await delivery.deliver(payload: fixture.payload, secret: "unit-test-secret")
            let items = try await fixture.store.loadAll()
            XCTAssertEqual(items.first?.status, .sent)
            XCTAssertEqual(items.first?.lastDeliveryPath, .directHTTPS)
        }
    }

    func testMismatchedResponseRequestIDIsRejectedAsUnsafe() async throws {
        let fixture = try await makeFixture(requestID: "delivery-request-id")
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        let responseBody = Data("""
        {
          "status":"accepted",
          "dry_run":true,
          "request_id":"different-request-id",
          "domain":"argos.general_capture",
          "display_message":"Captura validada · dry-run ✅",
          "plan":{"would_write":false,"side_effects":[]}
        }
        """.utf8)
        let client = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: URLSessionStub { request in
                let response = try XCTUnwrap(
                    HTTPURLResponse(
                        url: request.url!,
                        statusCode: 200,
                        httpVersion: nil,
                        headerFields: ["Content-Type": "application/json"]
                    )
                )
                return (responseBody, response)
            }
        )
        let delivery = OutboxDeliveryService(
            store: fixture.store,
            client: client,
            nowISO8601: { "2026-07-13T22:00:01Z" }
        )

        do {
            _ = try await delivery.deliver(
                payload: fixture.payload,
                secret: "unit-test-secret"
            )
            XCTFail("Expected mismatched request ID to be rejected")
        } catch let failure as OutboxDeliveryFailure {
            XCTAssertEqual(failure, .unsafeResponse)
        }

        let items = try await fixture.store.loadAll()
        XCTAssertEqual(items.first?.status, .failed)
        XCTAssertEqual(items.first?.attempts, 1)
        XCTAssertEqual(items.first?.lastError, "unsafe_response")
        XCTAssertNil(items.first?.lastDeliveryPath)
    }

    func testMissingWouldWriteSafetyGateIsRejectedAsUnsafe() async throws {
        let fixture = try await makeFixture(requestID: "delivery-missing-plan")
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        let responseBody = Data("""
        {
          "status":"accepted",
          "dry_run":true,
          "request_id":"delivery-missing-plan",
          "domain":"argos.general_capture",
          "display_message":"Captura validada · dry-run ✅"
        }
        """.utf8)
        let client = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: URLSessionStub { request in
                let response = try XCTUnwrap(
                    HTTPURLResponse(
                        url: request.url!,
                        statusCode: 200,
                        httpVersion: nil,
                        headerFields: ["Content-Type": "application/json"]
                    )
                )
                return (responseBody, response)
            }
        )
        let delivery = OutboxDeliveryService(
            store: fixture.store,
            client: client,
            nowISO8601: { "2026-07-13T22:00:01Z" }
        )

        do {
            _ = try await delivery.deliver(
                payload: fixture.payload,
                secret: "unit-test-secret"
            )
            XCTFail("Expected missing would_write gate to be rejected")
        } catch let failure as OutboxDeliveryFailure {
            XCTAssertEqual(failure, .unsafeResponse)
        }

        let items = try await fixture.store.loadAll()
        XCTAssertEqual(items.first?.status, .failed)
        XCTAssertEqual(items.first?.attempts, 1)
        XCTAssertEqual(items.first?.lastError, "unsafe_response")
        XCTAssertNil(items.first?.lastDeliveryPath)
    }

    func testHTTPFailureStoresOnlySanitizedCode() async throws {
        let fixture = try await makeFixture(requestID: "delivery-unauthorized")
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        let session = URLSessionStub { request in
            let response = try XCTUnwrap(
                HTTPURLResponse(
                    url: request.url!,
                    statusCode: 401,
                    httpVersion: nil,
                    headerFields: ["Content-Type": "application/json"]
                )
            )
            return (Data(#"{"error":"body-must-not-enter-outbox"}"#.utf8), response)
        }
        let client = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: session
        )
        let delivery = OutboxDeliveryService(
            store: fixture.store,
            client: client,
            nowISO8601: { "2026-07-13T22:00:01Z" }
        )

        do {
            _ = try await delivery.deliver(
                payload: fixture.payload,
                secret: "wrong-secret"
            )
            XCTFail("Expected HTTP 401 failure")
        } catch let failure as OutboxDeliveryFailure {
            XCTAssertEqual(failure, .http(401))
        }

        let items = try await fixture.store.loadAll()
        XCTAssertEqual(items.first?.status, .failed)
        XCTAssertEqual(items.first?.attempts, 1)
        XCTAssertEqual(items.first?.lastError, "http_401")
        XCTAssertNil(items.first?.lastDeliveryPath)
        XCTAssertFalse(items.first?.lastError?.contains("body-must-not-enter-outbox") ?? true)
        let deliverableItems = try await fixture.store.loadDeliverable()
        XCTAssertEqual(deliverableItems.count, 1)
    }

    func testNetworkFailureRetriesSameRequestIDAndMarksSent() async throws {
        let fixture = try await makeFixture(requestID: "delivery-retry")
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        let failingClient = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: URLSessionStub { _ in
                throw URLError(.notConnectedToInternet)
            }
        )
        let firstAttempt = OutboxDeliveryService(
            store: fixture.store,
            client: failingClient,
            nowISO8601: { "2026-07-13T22:00:01Z" }
        )

        do {
            _ = try await firstAttempt.deliver(
                payload: fixture.payload,
                secret: "unit-test-secret"
            )
            XCTFail("Expected network failure")
        } catch let failure as OutboxDeliveryFailure {
            XCTAssertEqual(failure, .network(URLError.notConnectedToInternet.rawValue))
        }

        var items = try await fixture.store.loadAll()
        XCTAssertEqual(items.first?.status, .failed)
        XCTAssertEqual(items.first?.attempts, 1)
        XCTAssertEqual(items.first?.payload.requestID, "delivery-retry")
        XCTAssertNil(items.first?.lastDeliveryPath)

        let responseBody = Data("""
        {
          "status":"accepted",
          "dry_run":true,
          "request_id":"delivery-retry",
          "domain":"argos.general_capture",
          "display_message":"Captura validada · dry-run ✅",
          "plan":{"would_write":false,"side_effects":[]}
        }
        """.utf8)
        let successClient = WebhookClient(
            endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!,
            session: URLSessionStub { request in
                XCTAssertEqual(
                    request.value(forHTTPHeaderField: WebhookHeaders.requestID),
                    "delivery-retry"
                )
                let response = try XCTUnwrap(
                    HTTPURLResponse(
                        url: request.url!,
                        statusCode: 200,
                        httpVersion: nil,
                        headerFields: ["Content-Type": "application/json"]
                    )
                )
                return (responseBody, response)
            }
        )
        let retry = OutboxDeliveryService(
            store: fixture.store,
            client: successClient,
            nowISO8601: { "2026-07-13T22:00:02Z" }
        )

        _ = try await retry.deliver(
            payload: fixture.payload,
            secret: "unit-test-secret"
        )

        items = try await fixture.store.loadAll()
        XCTAssertEqual(items.count, 1)
        XCTAssertEqual(items.first?.status, .sent)
        XCTAssertEqual(items.first?.attempts, 2)
        XCTAssertEqual(items.first?.lastDeliveryPath, .directHTTPS)
        XCTAssertNil(items.first?.lastError)
        let remaining = try await fixture.store.loadDeliverable()
        XCTAssertTrue(remaining.isEmpty)
    }

    private func seedExpensePreview(
        in store: FileOutboxStore,
        requestID: String,
        operationID: String,
        text: String = "45 mil almuerzo por Nequi"
    ) async throws -> CapturePayloadV1 {
        let payload = CapturePayloadV1(
            requestID: requestID,
            createdAt: "2026-07-13T21:59:00Z",
            source: CaptureSource(
                appVersion: "0.3.4",
                platform: "watchOS",
                osVersion: "27.0",
                deviceID: "unit-device",
                surface: "watch_app"
            ),
            route: .expense,
            capture: CaptureText(modality: "watch_dictation", text: text),
            context: CaptureContext(
                dryRun: false,
                allowWrite: true,
                allowFireflyWrite: true
            )
        )
        let response = CaptureResponseV1(
            status: "needs_confirmation",
            dryRun: false,
            requestID: requestID,
            domain: CaptureDomain.meganExpenseCapture.rawValue,
            operationID: operationID,
            confirmationToken: "v2.server-token",
            confirmation: ExpenseConfirmationSummary(
                amountCOP: 45_000,
                description: "Almuerzos",
                sourceName: "Nequi",
                categoryName: "Food",
                budgetName: "Comidas afuera"
            ),
            duplicate: false,
            writeCommitted: false,
            plan: CaptureResponsePlan(sideEffects: [], wouldWrite: false)
        )
        _ = try await store.enqueue(payload, now: payload.createdAt)
        try await store.markSent(
            requestID: payload.requestID,
            now: "2026-07-13T22:00:00Z",
            deliveryPath: .directHTTPS,
            response: response
        )
        return payload
    }

    private func makeFixture(
        requestID: String,
        route: CaptureRoute = .general,
        context: CaptureContext = CaptureContext(),
        captureText: String = "integration test"
    ) async throws -> DeliveryFixture {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let fileURL = directory.appendingPathComponent("outbox.json")
        let payload = CapturePayloadV1(
            requestID: requestID,
            createdAt: "2026-07-13T22:00:00Z",
            source: CaptureSource(
                appVersion: "0.1.0",
                platform: "watchOS",
                osVersion: "26.5",
                deviceID: "unit-device",
                surface: "watch_app"
            ),
            route: route,
            capture: CaptureText(
                modality: "watch_dictation",
                text: captureText
            ),
            context: context
        )
        let store = FileOutboxStore(fileURL: fileURL)
        _ = try await store.enqueue(payload, now: payload.createdAt)
        return DeliveryFixture(
            directory: directory,
            payload: payload,
            store: store
        )
    }
}

private struct DeliveryFixture {
    let directory: URL
    let payload: CapturePayloadV1
    let store: FileOutboxStore
}

private final class URLSessionStub: URLSessioning {
    typealias Handler = (URLRequest) throws -> (Data, URLResponse)
    private let handler: Handler

    init(handler: @escaping Handler) {
        self.handler = handler
    }

    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        try handler(request)
    }
}
