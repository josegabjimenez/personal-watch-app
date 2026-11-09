import XCTest

#if DEBUG && targetEnvironment(simulator)
@MainActor
final class PersonalWatchAppUITests: XCTestCase {
    private var app: XCUIApplication!
    private var caseID = ""

    override func setUpWithError() throws {
        continueAfterFailure = false
        caseID = UUID().uuidString.lowercased()
        app = XCUIApplication()
    }

    override func tearDownWithError() throws {
        app?.terminate()
    }

    func testHomeOffersOnlyThreeApprovedCaptureActions() {
        launch()
        for action in ["grocery", "reminder", "expense"] {
            XCTAssertTrue(app.buttons["capture.\(action)"].waitForExistence(timeout: 5))
        }
        XCTAssertFalse(app.buttons["capture.general"].exists)
        XCTAssertFalse(app.buttons["Capturar"].exists)
        XCTAssertFalse(app.staticTexts["Capturar"].exists)
    }

    func testGroceryCaptureShowsCommittedResult() {
        launch()
        enterCapture("grocery", text: "leche")
        XCTAssertFalse(app.buttons["capture.anotherGrocery"].exists)
        app.buttons["capture.send"].tap()
        assertStatusContains("Mercado actualizado")
        XCTAssertFalse(app.buttons["expense.confirm"].exists)
        XCTAssertFalse(app.buttons["capture.anotherGrocery"].exists)
    }

    func testExhaustedGroceryCanRetryOriginalAfterRelaunch() {
        launch(scenario: "grocery_network_failure")
        enterCapture("grocery", text: "leche")
        failGroceryCapture(attempts: 5)

        relaunchAndOpenCapture("grocery")
        let field = app.textFields["capture.text"]
        XCTAssertEqual(field.value as? String, "leche")
        XCTAssertFalse(field.isEnabled)
        XCTAssertEqual(app.buttons["capture.send"].label, "Reintentar")

        // The sixth explicit attempt must reach the local transport fixture,
        // rather than fail at the normal five-attempt delivery cap.
        app.buttons["capture.send"].tap()
        assertStatusContains("Servidor inaccesible")
        XCTAssertEqual(field.value as? String, "leche")
        XCTAssertFalse(field.isEnabled)
        XCTAssertTrue(app.buttons["capture.anotherGrocery"].isEnabled)
    }

    func testAnotherGroceryClearsEditorAndPreservesOriginalAfterRelaunch() {
        launch(scenario: "grocery_network_failure")
        enterCapture("grocery", text: "leche")
        failGroceryCapture(attempts: 5)

        app.buttons["capture.anotherGrocery"].tap()
        let field = app.textFields["capture.text"]
        XCTAssertTrue(field.isEnabled)
        XCTAssertTrue(["", "Ej. arroz"].contains(field.value as? String ?? "<missing>"))
        XCTAssertFalse(app.buttons["capture.send"].isEnabled)
        assertStatusContains("queda guardado")
        field.tap()
        field.typeText("arroz")
        XCTAssertEqual(field.value as? String, "arroz")
        XCTAssertTrue(app.buttons["capture.send"].isEnabled)
        XCTAssertFalse(app.buttons["capture.anotherGrocery"].exists)

        // An unsent distinct draft never replaces or acknowledges the old row.
        relaunchAndOpenCapture("grocery")
        XCTAssertEqual(field.value as? String, "leche")
        XCTAssertFalse(field.isEnabled)
        XCTAssertTrue(app.buttons["capture.anotherGrocery"].isEnabled)
        app.buttons["capture.anotherGrocery"].tap()
        XCTAssertTrue(field.isEnabled)
        XCTAssertTrue(["", "Ej. arroz"].contains(field.value as? String ?? "<missing>"))
    }

    func testReminderShowsBogotaScheduleAndPendingAlerts() {
        launch()
        enterCapture("reminder", text: "recuérdame llamar mañana a las dos P.M.")
        app.buttons["capture.send"].tap()
        assertStatusContains("Bogotá")
        assertStatusContains("2:00")
        assertStatusContains("avisos pendientes")
    }

    func testReminderDNSFailureCanBeRetainedBeforeNewDraftAndRelaunch() {
        launch(scenario: "reminder_network_failure")
        let originalText = "recuérdame revisar la prueba anterior mañana"
        enterCapture("reminder", text: originalText)
        app.buttons["capture.send"].tap()
        assertStatusContains("No se encuentra el servidor")
        let field = app.textFields["capture.text"]
        XCTAssertFalse(field.isEnabled)
        XCTAssertEqual(field.value as? String, originalText)

        retainReminder()
        assertEmptyReminderEditor()
        assertStatusContains("Anterior conservado")
        XCTAssertTrue(app.staticTexts[originalText].exists)
        field.tap()
        field.typeText("una intención distinta sin enviar")

        relaunchAndOpenCapture("reminder")
        assertEmptyReminderEditor()
        XCTAssertTrue(app.staticTexts[originalText].exists)
        XCTAssertFalse(app.buttons["capture.newReminder"].exists)
    }

    func testReminderRetentionCanBeCancelledWithoutDetachingOriginal() {
        launch(scenario: "reminder_network_failure")
        let originalText = "recuérdame llamar mañana a las dos"
        enterCapture("reminder", text: originalText)
        app.buttons["capture.send"].tap()
        assertStatusContains("No se encuentra el servidor")
        app.buttons["capture.newReminder"].tap()
        let cancel = app.buttons["Volver"].firstMatch
        if cancel.exists {
            cancel.tap()
        } else {
            // iOS popovers omit the cancel-role row and expose a native
            // dismissal region instead. This dismisses without choosing retain.
            let dismiss = app.otherElements["PopoverDismissRegion"].firstMatch
            XCTAssertTrue(dismiss.exists)
            dismiss.tap()
        }
        XCTAssertEqual(app.textFields["capture.text"].value as? String, originalText)
        XCTAssertFalse(app.textFields["capture.text"].isEnabled)
        XCTAssertEqual(app.buttons["capture.send"].label, "Reintentar")
    }

    func testUnknownReminderCanBeRetainedWithoutClaimingCancellation() {
        launch(scenario: "reminder_old_sending")
        openCapture("reminder")
        let field = app.textFields["capture.text"]
        let restored = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == %@", "Recordatorio anterior sintético"), object: field)
        XCTAssertEqual(XCTWaiter.wait(for: [restored], timeout: 5), .completed)
        XCTAssertFalse(field.isEnabled)
        XCTAssertFalse(app.buttons["capture.send"].isEnabled)
        retainReminder()
        assertEmptyReminderEditor()
        let preserved = app.buttons["history.retainedReminder.synthetic-old-reminder-\(caseID)"]
        XCTAssertTrue(preserved.waitForExistence(timeout: 5))
        preserved.tap()
        XCTAssertTrue(app.staticTexts["Estado del envío: Por verificar"].exists)
        XCTAssertTrue(app.staticTexts["Capturado: 2026-11-01T00:00:00Z"].exists)
        XCTAssertTrue(app.staticTexts["Intentos: 1"].exists)
        XCTAssertFalse(app.staticTexts["Estado del envío: Cancelado local"].exists)
        relaunchAndOpenCapture("reminder")
        assertEmptyReminderEditor()
        XCTAssertTrue(app.buttons["history.retainedReminder.synthetic-old-reminder-\(caseID)"].exists)
    }

    func testReminderPendingServerResponseCanBeRetainedWithoutAnotherSend() {
        launch(scenario: "reminder_pending")
        enterCapture("reminder", text: "recuérdame revisar mañana")
        app.buttons["capture.send"].tap()
        assertStatusContains("pendiente de verificar")
        retainReminder()
        assertEmptyReminderEditor()
        let row = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "history.retainedReminder.")).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 5))
        row.tap()
        XCTAssertTrue(app.staticTexts["Estado del envío: Por verificar"].exists)
        XCTAssertTrue(app.staticTexts["Intentos: 1"].exists)
    }

    func testVerifiedExpiredReminderNoWriteReleasesComposer() {
        launch(scenario: "reminder_expired")
        enterCapture("reminder", text: "recuérdame una tarea con hora vencida")
        app.buttons["capture.send"].tap()
        assertStatusContains("La hora ya pasó")
        assertEmptyReminderEditor()
        XCTAssertFalse(app.buttons["capture.newReminder"].exists)
        relaunchAndOpenCapture("reminder")
        assertEmptyReminderEditor()
        assertStatusContains("La hora ya pasó")
    }

    func testReadOnlyReminderConnectionCheckDoesNotRetryPendingCapture() {
        launch(scenario: "reminder_network_failure")
        let originalText = "recuérdame revisar mañana"
        enterCapture("reminder", text: originalText)
        app.buttons["capture.send"].tap()
        assertStatusContains("No se encuentra el servidor")
        let check = app.buttons["capture.checkConnection"]
        reveal(check)
        check.tap()
        assertConnectionContains("no resuelve el nombre del servidor")
        XCTAssertEqual(app.textFields["capture.text"].value as? String, originalText)
        XCTAssertFalse(app.textFields["capture.text"].isEnabled)
        XCTAssertEqual(app.buttons["capture.send"].label, "Reintentar")
        reveal(app.buttons["capture.newReminder"])
        retainReminder()
        let row = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "history.retainedReminder.")).firstMatch
        reveal(row)
        row.tap()
        XCTAssertTrue(app.staticTexts["Intentos: 1"].exists)
    }

    func testExpenseRequiresExplicitConfirmationBeforeCommit() {
        launch()
        requestExpensePreview()
        XCTAssertTrue(app.buttons["expense.cancel"].exists)
        XCTAssertFalse(app.staticTexts["capture.status"].label.contains("Gasto registrado"))
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "Efectivo")).firstMatch.exists)
        app.buttons["expense.confirm"].tap()
        assertStatusContains("Gasto registrado")
        XCTAssertFalse(app.buttons["expense.confirm"].exists)
        relaunchAndOpenExpense()
        assertNoRestoredConfirmation()
    }

    func testCancellationDoesNotRestorePreviewAfterRelaunch() {
        launch()
        requestExpensePreview()
        app.buttons["expense.cancel"].tap()
        assertStatusContains("Gasto cancelado · no se registró nada")
        XCTAssertFalse(app.staticTexts["capture.status"].label.contains("registro previo"))
        relaunchAndOpenExpense()
        assertNoRestoredConfirmation()
        assertStatusContains("Gasto cancelado · no se registró nada")
    }

    func testOfflineConfirmationCanBeCancelledWithoutRestoringPreview() {
        launch(scenario: "offline_confirmation")
        requestExpensePreview()
        app.buttons["expense.confirm"].tap()
        assertStatusContains("Sin internet disponible")
        XCTAssertTrue(app.buttons["expense.cancel"].waitForExistence(timeout: 5))
        app.buttons["expense.cancel"].tap()
        assertStatusContains("Cancelación recibida · verifica si hubo un registro previo en Firefly")
        XCTAssertFalse(app.staticTexts["capture.status"].label.contains("no se registró nada"))
        relaunchAndOpenExpense()
        assertNoRestoredConfirmation()
        assertStatusContains("Cancelación recibida · verifica si hubo un registro previo en Firefly")
    }

    func testOfflineCancellationWarningSurvivesRelaunchAndExactBulkRetry() {
        launch(scenario: "offline_cancellation")
        requestExpensePreview()
        app.buttons["expense.confirm"].tap()
        assertStatusContains("Sin internet disponible")
        app.buttons["expense.cancel"].tap()
        assertStatusContains("Cancelación solicitada · el envío anterior puede haberse registrado; verifica en Firefly")
        assertNoRestoredConfirmation()

        relaunchAndOpenExpense()
        assertNoRestoredConfirmation()
        assertStatusContains("Cancelación solicitada · el envío anterior puede haberse registrado; verifica en Firefly")

        app.terminate()
        // Preserve this case's saved request; only the local transport recovers.
        launch()
        let retry = app.buttons["capture.retryPending"]
        reveal(retry)
        let enabled = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == true AND enabled == true"), object: retry)
        XCTAssertEqual(XCTWaiter.wait(for: [enabled], timeout: 5), .completed)
        retry.tap()
        let result = app.staticTexts["home.status"]
        reveal(result)
        let received = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "exists == true AND label CONTAINS %@", "Cancelación recibida · verifica si hubo un registro previo en Firefly"),
            object: result)
        XCTAssertEqual(XCTWaiter.wait(for: [received], timeout: 5), .completed)
        XCTAssertFalse(result.label.contains("no se registró nada"))

        relaunchAndOpenExpense()
        assertNoRestoredConfirmation()
        assertStatusContains("Cancelación recibida · verifica si hubo un registro previo en Firefly")
    }

    func testSendingCancellationRestoresWarningWithoutClaimingAcknowledgmentOrRetrying() {
        launch(scenario: "expense_cancellation_sending")
        // A real local adapter call provides a positive audit control; opening
        // the expense form and checking its retry state must add no POST.
        openCapture("reminder")
        let check = app.buttons["capture.checkConnection"]
        reveal(check)
        check.tap()
        assertConnectionContains("Servidor accesible · Recordatorio habilitado")

        app.terminate()
        launch(scenario: "expense_cancellation_sending")
        let retry = app.buttons["capture.retryPending"]
        reveal(retry)
        XCTAssertTrue(retry.exists)
        XCTAssertFalse(retry.isEnabled)
        // Relaunch to open the expense action from the top of the home form.
        relaunchAndOpenExpense()
        assertNoRestoredConfirmation()
        assertStatusContains("Cancelación solicitada · el envío anterior puede haberse registrado; verifica en Firefly")
        XCTAssertFalse(app.staticTexts["capture.status"].label.contains("Cancelación recibida"))
        XCTAssertFalse(app.staticTexts["capture.status"].label.contains("no se registró nada"))

        relaunchAndOpenExpense()
        assertNoRestoredConfirmation()
        assertStatusContains("Cancelación solicitada · el envío anterior puede haberse registrado; verifica en Firefly")
        XCTAssertFalse(app.staticTexts["capture.status"].label.contains("Cancelación recibida"))
    }

    func testExpiredPreviewIsTerminalAndNotRestored() {
        launch(scenario: "expired")
        requestExpensePreview()
        app.buttons["expense.confirm"].tap()
        assertStatusContains("vencida")
        XCTAssertFalse(app.buttons["expense.confirm"].exists)
        relaunchAndOpenExpense()
        assertNoRestoredConfirmation()
    }

    func testDoubleTapConfirmationLeavesOneTerminalResult() {
        launch()
        requestExpensePreview()
        app.buttons["expense.confirm"].doubleTap()
        assertStatusContains("Gasto registrado")
        XCTAssertFalse(app.buttons["expense.confirm"].exists)
        relaunchAndOpenExpense()
        assertNoRestoredConfirmation()
    }

    private func launch(scenario: String = "normal") {
        app.launchArguments = ["--personal-watch-synthetic-ui", "--personal-watch-ui-case=\(caseID)",
                               "--personal-watch-ui-scenario=\(scenario)"]
        app.launch()
    }

    private func enterCapture(_ action: String, text: String) {
        openCapture(action)
        let field = app.textFields["capture.text"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        field.tap()
        field.typeText(text)
        XCTAssertTrue(app.buttons["capture.send"].isEnabled)
    }

    private func openCapture(_ action: String) {
        let entry = app.buttons["capture.\(action)"]
        XCTAssertTrue(entry.waitForExistence(timeout: 5))
        entry.tap()
        XCTAssertTrue(app.textFields["capture.text"].waitForExistence(timeout: 5))
    }

    private func retainReminder() {
        let button = app.buttons["capture.newReminder"]
        reveal(button)
        XCTAssertTrue(button.isEnabled)
        button.tap()
        let confirm = app.buttons["capture.retainReminder.confirm"].firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        confirm.tap()
    }

    private func assertEmptyReminderEditor(file: StaticString = #filePath, line: UInt = #line) {
        let field = app.textFields["capture.text"]
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate(format: "enabled == true"), object: field)
        XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: 5), .completed, file: file, line: line)
        XCTAssertTrue(["", "Ej. llamar mañana a las 2 pm"].contains(field.value as? String ?? "<missing>"), file: file, line: line)
        XCTAssertEqual(app.buttons["capture.send"].label, "Enviar", file: file, line: line)
        XCTAssertFalse(app.buttons["capture.send"].isEnabled, file: file, line: line)
    }

    private func reveal(_ element: XCUIElement) {
        for _ in 0..<4 where !element.isHittable { app.swipeUp() }
        if !element.isHittable {
            for _ in 0..<4 where !element.isHittable { app.swipeDown() }
        }
    }

    private func assertConnectionContains(_ text: String, file: StaticString = #filePath, line: UInt = #line) {
        let status = app.staticTexts["capture.connectionStatus"]
        let expectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == true AND label CONTAINS %@", text), object: status)
        XCTAssertEqual(XCTWaiter.wait(for: [expectation], timeout: 5), .completed, file: file, line: line)
    }

    private func requestExpensePreview() {
        enterCapture("expense", text: "45 mil en Uber")
        app.buttons["capture.send"].tap()
        XCTAssertTrue(app.buttons["expense.confirm"].waitForExistence(timeout: 5))
    }

    private func failGroceryCapture(attempts: Int) {
        for _ in 0..<attempts {
            let send = app.buttons["capture.send"]
            XCTAssertTrue(send.isEnabled)
            send.tap()
            assertStatusContains("Servidor inaccesible")
            XCTAssertTrue(send.isEnabled)
            XCTAssertTrue(app.buttons["capture.anotherGrocery"].isEnabled)
        }
    }

    private func relaunchAndOpenExpense() {
        relaunchAndOpenCapture("expense")
    }

    private func relaunchAndOpenCapture(_ action: String) {
        app.terminate()
        // The same case argument deliberately retains only this test's outbox.
        app.launch()
        let entry = app.buttons["capture.\(action)"]
        XCTAssertTrue(entry.waitForExistence(timeout: 5))
        entry.tap()
        XCTAssertTrue(app.textFields["capture.text"].waitForExistence(timeout: 5))
    }

    private func assertNoRestoredConfirmation(file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertFalse(app.buttons["expense.confirm"].waitForExistence(timeout: 1), file: file, line: line)
        XCTAssertFalse(app.buttons["expense.cancel"].exists, file: file, line: line)
    }

    private func assertStatusContains(_ text: String, file: StaticString = #filePath, line: UInt = #line) {
        let status = app.staticTexts["capture.status"]
        let predicate = NSPredicate(format: "exists == true AND label CONTAINS %@", text)
        let expectation = XCTNSPredicateExpectation(predicate: predicate, object: status)
        XCTAssertEqual(XCTWaiter.wait(for: [expectation], timeout: 5), .completed, file: file, line: line)
    }
}
#endif
