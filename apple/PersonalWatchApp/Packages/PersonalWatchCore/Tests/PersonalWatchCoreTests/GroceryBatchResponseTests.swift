import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import XCTest
@testable import PersonalWatchCore

/// JSON fixtures are actual grocery_capture.execute output using GroceryAdapter,
/// the extracted deployed-helper AST and a synthetic Notion transport. No live
/// helper, credentials or network are used by these client compatibility tests.
final class GroceryBatchResponseTests: XCTestCase {
    private let now = "2026-11-02T12:00:01Z"
    private let terminalFixtures = ["full_success", "mixed_success", "all_already"]

    func testActualEngineBatchResponsesDecodeWithCompleteItemEvidence() throws {
        let payload = try requestFixture()
        let expectedCounts = [[3, 0, 0], [1, 1, 1], [0, 0, 3]]
        for (name, counts) in zip(terminalFixtures, expectedCounts) {
            let response = try decodeResponse(name)
            XCTAssertTrue(OutboxDeliveryService.isSafe(response: response, for: payload), name)
            XCTAssertEqual(response.requestID, payload.requestID)
            XCTAssertEqual(response.domain, "aura.grocery_capture")
            XCTAssertEqual(response.dryRun, false)
            XCTAssertEqual(response.writeCommitted, name != "all_already")
            XCTAssertEqual(response.plan?.wouldWrite, name != "all_already")
            XCTAssertEqual(response.status, name == "all_already" ? "duplicate" : "committed")

            let identifiers = try XCTUnwrap(response.externalIDs)
            XCTAssertEqual(identifiers["item_count"], .number(3))
            for (key, count) in zip(["created", "updated", "already_present"], counts) {
                XCTAssertEqual(identifiers[key], .number(Decimal(count)), name)
            }
            guard case .array(let blockIDs) = identifiers["notion_block_ids"],
                  case .array(let items) = identifiers["grocery_items"] else {
                return XCTFail("Missing multi-item evidence in \(name)")
            }
            XCTAssertEqual(blockIDs.count, 3)
            let strings = blockIDs.compactMap { value -> String? in
                if case .string(let string) = value { return string }
                return nil
            }
            XCTAssertEqual(strings.count, 3)
            XCTAssertEqual(Set(strings).count, 3)
            XCTAssertEqual(items.count, 3)
            var operationIDs: [String] = []
            for (value, expectedItem) in zip(items, ["2 litros de leche", "6 huevos", "pan integral"]) {
                guard case .object(let item) = value,
                      case .string(let operationID) = item["operation_id"] else {
                    return XCTFail("Missing per-item receipt in \(name)")
                }
                XCTAssertEqual(item["requested_item"], .string(expectedItem))
                XCTAssertEqual(item["item"], .string(expectedItem))
                XCTAssertEqual(item["checked"], .bool(false))
                operationIDs.append(operationID)
            }
            XCTAssertEqual(Set(operationIDs).count, 3)
            XCTAssertTrue(response.displayMessage?.contains("3 confirmados") == true)
        }
    }

    func testEachTerminalBatchReceiptSurvivesReopenAndPreventsRedispatch() async throws {
        let payload = try requestFixture()
        for name in terminalFixtures {
            let fixture = makeStore()
            defer { try? FileManager.default.removeItem(at: fixture.directory) }
            _ = try await fixture.store.enqueue(payload, now: payload.createdAt)
            let session = GroceryBatchSession(steps: [(200, try fixtureData(name))])
            let response = try await service(fixture.store, session).deliver(payload: payload, secret: "synthetic-contract-only")
            XCTAssertEqual(response, try decodeResponse(name))

            let reopened = FileOutboxStore(fileURL: fixture.url)
            let rows = try await reopened.loadAll()
            let row = try XCTUnwrap(rows.first)
            XCTAssertEqual(rows.count, 1)
            XCTAssertEqual(row.status, .sent)
            XCTAssertEqual(row.attempts, 1)
            XCTAssertEqual(row.payload, payload)
            XCTAssertEqual(row.lastResponse, response)
            XCTAssertEqual(row.lastDeliveryPath, .directHTTPS)
            let replay = try await service(reopened, session).deliver(payload: payload, secret: "synthetic-contract-only")
            XCTAssertEqual(replay, response)
            let callCount = await session.bodies.count
            XCTAssertEqual(callCount, 1, "A durable terminal batch receipt must not dispatch again")
            let remaining = try await reopened.loadDeliverable()
            XCTAssertTrue(remaining.isEmpty)
        }
    }

    func testPartial503RetainsExactParentBodyAcrossMonotonicRetryAndReopen() async throws {
        let payload = try requestFixture()
        let partial = try fixtureData("partial")
        let partialResponse = try JSONDecoder.captureDecoder().decode(CaptureResponseV1.self, from: partial)
        XCTAssertFalse(OutboxDeliveryService.isSafe(response: partialResponse, for: payload))
        let partialJSON = try XCTUnwrap(JSONSerialization.jsonObject(with: partial) as? [String: Any])
        XCTAssertEqual(partialJSON["completed_count"] as? Int, 1)
        XCTAssertEqual(partialJSON["total_count"] as? Int, 3)

        let fixture = makeStore()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        _ = try await fixture.store.enqueue(payload, now: payload.createdAt)
        let session = GroceryBatchSession(steps: [(503, partial), (503, partial), (200, try fixtureData("full_success"))])
        for attempt in 1...2 {
            let reopened = FileOutboxStore(fileURL: fixture.url)
            do {
                _ = try await service(reopened, session).deliver(payload: payload, secret: "synthetic-contract-only")
                XCTFail("A partially confirmed batch must not be marked sent")
            } catch {
                XCTAssertEqual(error as? OutboxDeliveryFailure, .http(503))
            }
            let rows = try await reopened.loadAll()
            let row = try XCTUnwrap(rows.first)
            XCTAssertEqual(rows.count, 1)
            XCTAssertEqual(row.payload, payload)
            XCTAssertEqual(row.attempts, attempt)
            XCTAssertEqual(row.status, .failed)
            XCTAssertEqual(row.lastError, "http_503")
            XCTAssertNil(row.lastResponse)
        }

        let reopened = FileOutboxStore(fileURL: fixture.url)
        let response = try await service(reopened, session).deliver(payload: payload, secret: "synthetic-contract-only")
        XCTAssertEqual(response, try decodeResponse("full_success"))
        let rows = try await reopened.loadAll()
        let row = try XCTUnwrap(rows.first)
        XCTAssertEqual(rows.count, 1)
        XCTAssertEqual(row.payload, payload)
        XCTAssertEqual(row.attempts, 3)
        XCTAssertEqual(row.status, .sent)
        XCTAssertNil(row.lastError)
        let bodies = await session.bodies
        let requestIDs = await session.requestIDs
        XCTAssertEqual(bodies.count, 3)
        XCTAssertTrue(bodies.allSatisfy { $0 == bodies.first }, "Retries must retain the raw body used by the backend payload hash")
        XCTAssertEqual(requestIDs, Array(repeating: payload.requestID, count: 3))
        XCTAssertEqual(try JSONDecoder.captureDecoder().decode(CapturePayloadV1.self, from: XCTUnwrap(bodies.first)), payload)
        _ = try await service(FileOutboxStore(fileURL: fixture.url), session).deliver(payload: payload, secret: "synthetic-contract-only")
        let finalCount = await session.bodies.count
        XCTAssertEqual(finalCount, 3)
    }

    func testUncertainPartialAndUncorrelatedWholeBatchClaimsNeverBecomeSent() async throws {
        let payload = try requestFixture()
        let base = try XCTUnwrap(JSONSerialization.jsonObject(with: fixtureData("full_success")) as? [String: Any])
        var variants: [(String, [String: Any])] = []
        for status in ["uncertain", "partial", "needs_reconciliation"] {
            var value = base; value["status"] = status
            variants.append((status, value))
        }
        var noWrite = base; noWrite["write_committed"] = false
        variants.append(("write false with a write plan", noWrite))
        var noPlan = base; noPlan["plan"] = ["would_write": false, "side_effects": []]
        variants.append(("write true with a no-write plan", noPlan))
        var falseCommit = noWrite; falseCommit["plan"] = ["would_write": false, "side_effects": []]
        variants.append(("committed without a write", falseCommit))
        var wrongParent = base; wrongParent["request_id"] = "different-parent"
        variants.append(("wrong parent", wrongParent))
        var wrongDomain = base; wrongDomain["domain"] = "aura.reminder_capture"
        variants.append(("wrong domain", wrongDomain))
        var dryRun = base; dryRun["dry_run"] = true
        variants.append(("dry-run mismatch", dryRun))
        var noProof = base; noProof.removeValue(forKey: "write_committed")
        variants.append(("missing write proof", noProof))
        let partial = try XCTUnwrap(JSONSerialization.jsonObject(with: fixtureData("partial")) as? [String: Any])
        variants.append(("actual partial envelope over HTTP200", partial))

        for (label, value) in variants {
            let data = try JSONSerialization.data(withJSONObject: value)
            let response = try JSONDecoder.captureDecoder().decode(CaptureResponseV1.self, from: data)
            XCTAssertFalse(OutboxDeliveryService.isSafe(response: response, for: payload), label)
            let fixture = makeStore()
            defer { try? FileManager.default.removeItem(at: fixture.directory) }
            _ = try await fixture.store.enqueue(payload, now: payload.createdAt)
            let session = GroceryBatchSession(steps: [(200, data)])
            do {
                _ = try await service(fixture.store, session).deliver(payload: payload, secret: "synthetic-contract-only")
                XCTFail(label)
            } catch {
                XCTAssertEqual(error as? OutboxDeliveryFailure, .unsafeResponse, label)
            }
            let rows = try await FileOutboxStore(fileURL: fixture.url).loadAll()
            let row = try XCTUnwrap(rows.first)
            XCTAssertEqual(row.payload, payload)
            XCTAssertEqual(row.status, .failed, label)
            XCTAssertEqual(row.attempts, 1)
            XCTAssertEqual(row.lastError, "unsafe_response")
            XCTAssertNil(row.lastResponse)
        }
    }

    private func requestFixture() throws -> CapturePayloadV1 {
        try JSONDecoder.captureDecoder().decode(CapturePayloadV1.self, from: fixtureData("request"))
    }

    private func decodeResponse(_ name: String) throws -> CaptureResponseV1 {
        try JSONDecoder.captureDecoder().decode(CaptureResponseV1.self, from: fixtureData(name))
    }

    private func fixtureData(_ name: String) throws -> Data {
        var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<12 {
            let path = directory.appendingPathComponent("backend/tests/fixtures/grocery_batch_\(name).json")
            if FileManager.default.fileExists(atPath: path.path) { return try Data(contentsOf: path) }
            directory.deleteLastPathComponent()
        }
        throw NSError(domain: "GroceryBatchFixture", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "Missing synthetic grocery batch fixture: \(name)"])
    }

    private func makeStore() -> (directory: URL, url: URL, store: FileOutboxStore) {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let url = directory.appendingPathComponent("outbox.json")
        return (directory, url, FileOutboxStore(fileURL: url))
    }

    private func service(_ store: FileOutboxStore, _ session: URLSessioning) -> OutboxDeliveryService {
        OutboxDeliveryService(store: store,
            client: WebhookClient(endpoint: URL(string: "https://grocery-batch.invalid/webhooks/mobile-capture-v1")!, session: session),
            nowISO8601: { "2026-11-02T12:00:01Z" })
    }
}

private actor GroceryBatchSession: URLSessioning {
    private let steps: [(Int, Data)]
    private(set) var bodies: [Data] = []
    private(set) var requestIDs: [String] = []

    init(steps: [(Int, Data)]) { self.steps = steps }

    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        let index = bodies.count
        bodies.append(try XCTUnwrap(request.httpBody))
        requestIDs.append(try XCTUnwrap(request.value(forHTTPHeaderField: WebhookHeaders.requestID)))
        guard index < steps.count else { throw URLError(.cannotConnectToHost) }
        let (status, body) = steps[index]
        let response = try XCTUnwrap(HTTPURLResponse(url: request.url!, statusCode: status,
                                                   httpVersion: nil, headerFields: ["Content-Type": "application/json"]))
        return (body, response)
    }
}
