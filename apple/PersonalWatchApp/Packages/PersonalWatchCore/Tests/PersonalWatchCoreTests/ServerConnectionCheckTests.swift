import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import XCTest
@testable import PersonalWatchCore

final class ServerConnectionCheckTests: XCTestCase {
    private let base = URL(string: "https://private-service.invalid:8650")!
    private let health = Data("""
    {"status":"ok","service":"mobile-capture-v1-staging","mode":"allowlisted-write","write_domains":["aura.grocery_capture","aura.reminder_capture"]}
    """.utf8)

    func testCheckSendsOnlyBoundedUnsignedHealthGet() async {
        let expectedData = health
        let session = ProbeStub { request in
            XCTAssertEqual(request.url?.absoluteString, "https://private-service.invalid:8650/health")
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertNil(request.httpBody)
            XCTAssertNil(request.httpBodyStream)
            XCTAssertEqual(request.timeoutInterval, 8)
            XCTAssertEqual(request.cachePolicy, .reloadIgnoringLocalCacheData)
            XCTAssertEqual(Set(request.allHTTPHeaderFields?.keys.map { $0.lowercased() } ?? []), ["accept"])
            XCTAssertNil(request.value(forHTTPHeaderField: WebhookHeaders.signatureV2))
            XCTAssertNil(request.value(forHTTPHeaderField: WebhookHeaders.requestID))
            return (expectedData, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
        }
        let result = await ServerConnectionCheck.check(baseURL: base, session: session)
        XCTAssertEqual(result, .ready(reminderEnabled: true))
        XCTAssertNil(result.diagnosticCode)
        XCTAssertFalse(result.message.contains("guardado"))
    }

    func testDNSOfflineTimeoutAndTLSRemainDistinctAndSanitized() async {
        for code in [-1003, -1006, -1004, -1009, -1001, -1005, -1200, -1201, -1202, -1203, -1204, -1205, -1206, -999, -1002] {
            let session = ProbeStub { _ in
                throw URLError(URLError.Code(rawValue: code), userInfo: [NSURLErrorFailingURLStringErrorKey: "https://private-secret.invalid/do-not-display"])
            }
            let result = await ServerConnectionCheck.check(baseURL: base, session: session)
            XCTAssertEqual(result, .network(code))
            XCTAssertEqual(result.diagnosticCode, "network_\(code)")
            XCTAssertFalse(result.message.contains("private-secret"))
            XCTAssertFalse(result.message.contains("do-not-display"))
            XCTAssertFalse(result.message.contains("guardado"), "A health check does not send any task")
        }
        XCTAssertNotEqual(ServerConnectionCheckResult.network(-1003).message, ServerConnectionCheckResult.network(-1009).message)
        XCTAssertNotEqual(ServerConnectionCheckResult.network(-1202).message, ServerConnectionCheckResult.network(-1004).message)
        XCTAssertTrue(OutboxDeliveryFailure.network(-1001).localizedDescription.contains("puede haberse guardado"))
        XCTAssertTrue(OutboxDeliveryFailure.network(-1005).localizedDescription.contains("puede haberse guardado"))
        XCTAssertFalse(OutboxDeliveryFailure.network(-1003).localizedDescription.contains("Sin conexión"))
        XCTAssertTrue(OutboxDeliveryFailure.network(-1202).localizedDescription.contains("Conexión segura rechazada"))
        XCTAssertEqual(OutboxDeliveryFailure.network(-1003).storageCode, "network_-1003")
        XCTAssertEqual(OutboxDeliveryFailure.http(401).storageCode, "http_401")
    }

    func testHTTPAndDisabledReminderDoNotClaimWriteReadiness() async {
        for status in [301, 302, 307, 401, 403, 404, 500, 502] {
            let session = ProbeStub { request in
                (Data("private upstream error".utf8), HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!)
            }
            let result = await ServerConnectionCheck.check(baseURL: base, session: session)
            XCTAssertEqual(result, .http(status))
            XCTAssertEqual(result.diagnosticCode, "http_\(status)")
            XCTAssertFalse(result.message.contains("private upstream"))
        }
        let session = ProbeStub { request in
            (Data("""
            {"status":"ok","service":"mobile-capture-v1-staging","mode":"allowlisted-write","write_domains":["aura.grocery_capture"]}
            """.utf8), HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
        }
        let result = await ServerConnectionCheck.check(baseURL: base, session: session)
        XCTAssertEqual(result, .ready(reminderEnabled: false))
    }

    func testBadHealthAndUnexpectedOriginAreRejected() async {
        let invalid = [Data(), Data("{}".utf8), Data(repeating: 65, count: 4097), Data("""
        {"status":"ok","service":"different","mode":"allowlisted-write","write_domains":["aura.reminder_capture"]}
        """.utf8)]
        for data in invalid {
            let session = ProbeStub { request in
                (data, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
            }
            let result = await ServerConnectionCheck.check(baseURL: base, session: session)
            XCTAssertEqual(result, .invalidResponse)
        }
        for wrong in ["http://private-service.invalid:8650/health", "https://other.invalid:8650/health", "https://private-service.invalid:8650/elsewhere", "https://private-service.invalid:8650/health?token=private"] {
            let data = health
            let session = ProbeStub { _ in
                (data, HTTPURLResponse(url: URL(string: wrong)!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
            }
            let result = await ServerConnectionCheck.check(baseURL: base, session: session)
            XCTAssertEqual(result, .invalidResponse)
        }
    }

    func testUnsafeConfigurationNeverTouchesTransport() async {
        let session = ProbeStub { _ in
            XCTFail("No request may be made")
            throw URLError(.unknown)
        }
        for raw in ["http://service.invalid", "https://127.0.0.1", "https://user:private@service.invalid"] {
            let result = await ServerConnectionCheck.check(baseURL: URL(string: raw)!, session: session)
            XCTAssertEqual(result, .invalidConfiguration)
        }
    }
}

private final class ProbeStub: URLSessioning {
    private let handler: (URLRequest) throws -> (Data, URLResponse)
    init(_ handler: @escaping (URLRequest) throws -> (Data, URLResponse)) { self.handler = handler }
    func data(for request: URLRequest) async throws -> (Data, URLResponse) { try handler(request) }
}
