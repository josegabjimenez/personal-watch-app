import XCTest
@testable import PersonalWatchCore

final class ReminderResponseTests: XCTestCase {
    func testTodoistReceiptDecodesWithUnverifiedAlertAndSafeOutboxContract() throws {
        let json = #"""
        {"status":"committed","dry_run":false,"write_committed":true,
         "request_id":"synthetic-reminder","domain":"aura.reminder_capture",
         "display_message":"Guardado en Todoist · 2026-10-30 14:00 Bogotá · alertas pendientes de verificar",
         "interpreted_due":{"start":"2026-10-30T14:00:00-05:00","timezone":"America/Bogota","display":"2026-10-30 14:00 Bogotá"},
         "external_ids":{"todoist_task_id":"synthetic-task"},
         "notification_verified":false,"delivery_status":"pending_verification",
         "auto_reminder_requested":true,"alert_configured":null,
         "plan":{"would_write":true,"side_effects":["todoist.task.write"]}}
        """#
        let response = try JSONDecoder().decode(CaptureResponseV1.self, from: Data(json.utf8))
        let payload = CapturePayloadV1(requestID: "synthetic-reminder", createdAt: "2026-10-29T15:00:00-05:00",
            source: .init(appVersion: "test", platform: "watchOS", osVersion: "27", deviceID: "synthetic", surface: "watch_app"),
            route: .reminder, capture: .init(modality: "watch_dictation", text: "Llamar mañana a las 2 pm"),
            context: .init(dryRun: false, allowWrite: true, allowFireflyWrite: false))
        XCTAssertTrue(OutboxDeliveryService.isSafe(response: response, for: payload))
        XCTAssertEqual(response.externalIDs?["todoist_task_id"], .string("synthetic-task"))
        XCTAssertEqual(response.interpretedDue?.timezone, "America/Bogota")
        XCTAssertEqual(response.notificationVerified, false)
        XCTAssertEqual(response.deliveryStatus, "pending_verification")
        XCTAssertNil(response.calendarSynced)
        XCTAssertEqual(try JSONDecoder().decode(CaptureResponseV1.self, from: JSONEncoder().encode(response)), response)
    }
}
