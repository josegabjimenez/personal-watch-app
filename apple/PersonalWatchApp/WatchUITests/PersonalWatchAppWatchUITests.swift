import XCTest

#if DEBUG && targetEnvironment(simulator)
@MainActor
final class PersonalWatchAppWatchUITests: XCTestCase {
    private var app: XCUIApplication!
    private var caseID = ""

    override func setUpWithError() throws {
        continueAfterFailure = false
        let characters = name.lowercased().filter { $0.isASCII && ($0.isLetter || $0.isNumber) }
        caseID = String(characters.suffix(42)) + "-" + UUID().uuidString.lowercased()
        app = XCUIApplication(bundleIdentifier: "dev.josegabjimenez.HermesCapture.watchkitapp")
        print("WATCH_UI_CASE \(name) \(caseID)")
    }

    override func tearDownWithError() throws {
        if let run = testRun, run.failureCount > 0 {
            print("WATCH_FAILURE_AX_BEGIN\n\(app.debugDescription)\nWATCH_FAILURE_AX_END")
        }
        if app?.state == .runningForeground {
            let screenshot = XCTAttachment(screenshot: app.screenshot())
            screenshot.name = "Watch final screen · \(name)"
            screenshot.lifetime = .keepAlways
            add(screenshot)
        }
        app?.terminate()
    }

    func testWatchHomeLaunchesWithApprovedActions() {
        launch()
        for action in ["grocery", "reminder", "expense"] {
            let entry = app.buttons["capture.\(action)"]
            reveal(entry)
            XCTAssertTrue(entry.exists, action)
        }
        let history = app.buttons["history.open"]
        reveal(history)
        XCTAssertTrue(history.exists)
        XCTAssertFalse(app.buttons["capture.general"].exists)
        XCTAssertFalse(app.buttons["Capturar"].exists)
    }

    func testFreshWatchHistoryIsEmptyAndReadOnly() {
        launch()
        openHistory()
        XCTAssertTrue(app.staticTexts["Sin capturas"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["capture.send"].exists)
        let back = app.buttons["BackButton"].firstMatch
        XCTAssertTrue(back.exists)
        back.tap()
        XCTAssertTrue(app.buttons["capture.reminder"].waitForExistence(timeout: 5))
    }

    func testUnconfiguredWatchChecksWithoutSending() {
        // This task-created simulator pair contains no real endpoint or secret.
        // Leave synthetic mode only to exercise the ordinary configuration gate.
        app.launchArguments = []
        app.launch()
        let prompt = app.staticTexts["Configura desde el iPhone"]
        reveal(prompt)
        XCTAssertTrue(prompt.exists)
        openCapture("reminder")
        XCTAssertFalse(app.buttons["capture.send"].isEnabled)
        checkConnection()
        assertConnectionContains("Configuración pendiente")
        XCTAssertFalse(app.buttons["capture.send"].isEnabled)
    }

    func testGroceryCommitClearsEditor() {
        launch()
        enterCapture("grocery", text: "leche")
        app.buttons["capture.send"].tap()
        assertStatusContains("Mercado actualizado")
        XCTAssertTrue(app.textFields["capture.text"].isEnabled)
        XCTAssertFalse(app.buttons["capture.send"].isEnabled)
        XCTAssertFalse(app.buttons["expense.confirm"].exists)
        relaunch()
        openHistory()
        XCTAssertTrue(app.staticTexts["leche"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Intentos: 1"].exists)
    }

    func testGrocerySixthExplicitRetryPreservesOriginalAndDistinctDraft() {
        launch(scenario: "grocery_network_failure")
        enterCapture("grocery", text: "leche")
        for _ in 0..<6 {
            let send = app.buttons["capture.send"]
            reveal(send)
            XCTAssertTrue(send.isEnabled)
            send.tap()
            assertStatusContains("Servidor inaccesible")
        }
        relaunch()
        openCapture("grocery")
        XCTAssertEqual(app.textFields["capture.text"].value as? String, "leche")
        XCTAssertFalse(app.textFields["capture.text"].isEnabled)
        XCTAssertEqual(app.buttons["capture.send"].label, "Reintentar")
        let another = app.buttons["capture.anotherGrocery"]
        reveal(another)
        another.tap()
        XCTAssertTrue(app.textFields["capture.text"].isEnabled)
        XCTAssertFalse(app.buttons["capture.send"].isEnabled)
        relaunch()
        openHistory()
        XCTAssertTrue(app.staticTexts["leche"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Intentos: 6"].exists)
    }

    func testReminderShowsBogotaScheduleWithNotificationStillPending() {
        launch()
        enterCapture("reminder", text: "recuérdame llamar mañana a las dos")
        app.buttons["capture.send"].tap()
        assertStatusContains("Guardado en Todoist")
        assertStatusContains("Bogotá")
        assertStatusContains("avisos pendientes")
        assertEmptyReminderEditor()
    }

    func testFourDNSFailuresCanBeRetainedAfterCancellingFirst() {
        launch(scenario: "reminder_old_dns")
        openCapture("reminder")
        assertRestoredOldReminder()
        checkConnection()
        assertConnectionContains("no resuelve el nombre del servidor")
        let retain = app.buttons["capture.newReminder"]
        reveal(retain)
        retain.tap()
        let namedCancel = app.buttons["Volver"].firstMatch
        let systemClose = app.buttons["AX_ActionContentControllerCancelButton"].firstMatch
        let cancel = namedCancel.exists ? namedCancel : systemClose
        guard cancel.waitForExistence(timeout: 2) else {
            XCTFail("Native Watch cancel control differs: \(app.debugDescription)")
            return
        }
        cancel.tap()
        assertRestoredOldReminder()

        retainReminder()
        assertEmptyReminderEditor()
        assertStatusContains("Anterior conservado")
        relaunch()
        openCapture("reminder")
        assertEmptyReminderEditor()
        XCTAssertFalse(app.buttons["capture.newReminder"].exists)
        relaunch()
        openHistory()
        assertOldReminderHistory(attempts: 4, error: "network_-1003", retained: true)
    }

    func testRetainedReminderAllowsOnlyExplicitDifferentIntent() {
        launch(scenario: "reminder_old_dns")
        openCapture("reminder")
        assertRestoredOldReminder()
        checkConnection()
        assertConnectionContains("no resuelve el nombre del servidor")
        retainReminder()
        assertEmptyReminderEditor()
        // Changing only the local fake response scenario leaves this case's
        // retained original untouched and permits a distinct explicit intent.
        app.terminate()
        launch(scenario: "normal")
        enterCapture("reminder", text: "una intención distinta para revisar mañana")
        app.buttons["capture.send"].tap()
        assertStatusContains("Guardado en Todoist")
        assertEmptyReminderEditor()
        relaunch()
        openHistory()
        XCTAssertTrue(app.staticTexts["una intención distinta para revisar mañana"].waitForExistence(timeout: 5))
        assertOldReminderHistory(attempts: 4, error: "network_-1003", retained: true)
    }

    func testUnknownSendingReminderCanBeRetainedWithoutAnotherSend() {
        launch(scenario: "reminder_old_sending")
        openCapture("reminder")
        let field = app.textFields["capture.text"]
        waitForValue("Recordatorio anterior sintético", in: field)
        XCTAssertFalse(field.isEnabled)
        XCTAssertFalse(app.buttons["capture.send"].isEnabled)
        checkConnection()
        assertConnectionContains("Servidor accesible")
        retainReminder()
        assertEmptyReminderEditor()
        relaunch()
        openHistory()
        assertOldReminderHistory(attempts: 1, error: nil, retained: true)
    }

    func testServerPendingReminderCanBeRetainedWithoutRetry() {
        launch(scenario: "reminder_pending")
        enterCapture("reminder", text: "recuérdame revisar mañana")
        app.buttons["capture.send"].tap()
        assertStatusContains("pendiente de verificar")
        retainReminder()
        assertEmptyReminderEditor()
        relaunch()
        openHistory()
        XCTAssertTrue(app.staticTexts["Intentos: 1"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Conservado · sin reintentos"].exists)
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "reminder_pending_verification")).firstMatch.exists)
    }

    func testExpiredReminderNoWriteReleasesEditorAcrossRelaunch() {
        launch(scenario: "reminder_expired")
        enterCapture("reminder", text: "recuérdame una tarea con hora vencida")
        app.buttons["capture.send"].tap()
        assertStatusContains("La hora ya pasó")
        assertEmptyReminderEditor()
        relaunch()
        openCapture("reminder")
        assertEmptyReminderEditor()
        assertStatusContains("La hora ya pasó")
    }

    func testConnectionCheckDoesNotRetryFourFailureReminder() {
        launch(scenario: "reminder_old_dns")
        openCapture("reminder")
        assertRestoredOldReminder()
        checkConnection()
        assertConnectionContains("no resuelve el nombre del servidor")
        assertRestoredOldReminder()
        relaunch()
        openHistory()
        assertOldReminderHistory(attempts: 4, error: "network_-1003", retained: false)
    }

    func testHealthReadyShowsGateEnabled() {
        assertHealth(scenario: "normal", contains: "Servidor accesible · Recordatorio habilitado")
    }

    func testHealthDisabledGateIsNotClaimedReady() {
        assertHealth(scenario: "health_gate_disabled", contains: "Recordatorio no habilitado")
    }

    func testHealthHTTPFailureIsSpecific() {
        assertHealth(scenario: "health_http_503", contains: "HTTP 503")
    }

    func testHealthTLSFailureKeepsSecureConnection() {
        assertHealth(scenario: "health_tls_failure", contains: "TLS o certificado rechazado")
    }

    func testHealthTimeoutIsSpecific() {
        assertHealth(scenario: "health_timeout", contains: "no respondió a tiempo")
    }

    func testRetainedReminderIsExcludedFromBulkRetry() {
        launch(scenario: "reminder_old_dns")
        openCapture("reminder")
        assertRestoredOldReminder()
        checkConnection()
        assertConnectionContains("no resuelve el nombre del servidor")
        retainReminder()
        assertEmptyReminderEditor()
        relaunch()
        let retry = app.buttons["capture.retryPending"]
        reveal(retry)
        XCTAssertTrue(retry.exists)
        retry.tap()
        let result = app.staticTexts["Sin capturas para reintentar"]
        reveal(result)
        XCTAssertTrue(result.waitForExistence(timeout: 5))
        relaunch()
        openHistory()
        assertOldReminderHistory(attempts: 4, error: "network_-1003", retained: true)
    }

    func testEligibleBulkRetryUsesOriginalAndThenBecomesNoOp() {
        launch(scenario: "reminder_network_failure")
        enterCapture("reminder", text: "recuérdame revisar la captura original mañana")
        app.buttons["capture.send"].tap()
        assertStatusContains("No se encuentra el servidor")
        app.terminate()
        launch(scenario: "normal")
        let retry = app.buttons["capture.retryPending"]
        reveal(retry)
        retry.tap()
        let received = app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "1 respuesta(s) recibidas")).firstMatch
        reveal(received)
        XCTAssertTrue(received.waitForExistence(timeout: 5))
        reveal(retry)
        retry.tap()
        let none = app.staticTexts["Sin capturas para reintentar"]
        reveal(none)
        XCTAssertTrue(none.waitForExistence(timeout: 5))
        relaunch()
        openHistory()
        XCTAssertTrue(app.staticTexts["Intentos: 2"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Respuesta recibida"].exists)
    }

    func testExpensePreviewNeedsExplicitConfirmationAndCommitsOnce() {
        launch()
        requestExpensePreview()
        for text in ["$45.000 COP", "Uber", "Efectivo", "Transporte · Movilidad"] {
            let value = app.staticTexts[text]
            reveal(value)
            XCTAssertTrue(value.exists, text)
        }
        XCTAssertFalse(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "Gasto registrado")).firstMatch.exists)
        let confirm = app.buttons["expense.confirm"]
        reveal(confirm)
        confirm.doubleTap()
        assertStatusContains("Gasto registrado")
        XCTAssertFalse(app.buttons["expense.confirm"].exists)
        relaunch()
        openCapture("expense")
        assertNoRestoredConfirmation()
    }

    func testExpenseCancellationPersistsAcrossRelaunch() {
        launch()
        requestExpensePreview()
        cancelExpense()
        assertStatusContains("cancelado")
        relaunch()
        openCapture("expense")
        assertNoRestoredConfirmation()
        assertStatusContains("Gasto cancelado")
        XCTAssertFalse(app.staticTexts.matching(NSPredicate(
            format: "label CONTAINS %@", "verifica si hubo un registro previo"
        )).firstMatch.exists)
        relaunch()
        openHistory()
        let cancellation = app.staticTexts.matching(NSPredicate(
            format: "label CONTAINS %@", "Gasto cancelado"
        )).firstMatch
        reveal(cancellation)
        XCTAssertTrue(cancellation.waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts.matching(NSPredicate(
            format: "label CONTAINS %@", "verifica si hubo un registro previo"
        )).firstMatch.exists)
    }

    func testOfflineExpenseConfirmationCanBeCancelled() {
        launch(scenario: "offline_confirmation")
        requestExpensePreview()
        let confirm = app.buttons["expense.confirm"]
        reveal(confirm)
        confirm.tap()
        assertStatusContains("Sin internet disponible")
        XCTAssertTrue(app.buttons["expense.cancel"].exists)
        cancelExpense()
        assertReceivedUncertainCancellation()
        relaunch()
        openCapture("expense")
        assertNoRestoredConfirmation()
        assertReceivedUncertainCancellation()
        relaunch()
        openHistory()
        assertHistoryUncertainCancellation()
    }

    func testUncertainCancellationBulkRetryKeepsWarningAfterRelaunch() {
        launch(scenario: "offline_cancellation")
        requestExpensePreview()
        let confirm = app.buttons["expense.confirm"]
        reveal(confirm)
        confirm.tap()
        assertStatusContains("Sin internet disponible")
        cancelExpense()
        assertStatusContains("Cancelación solicitada")
        assertStatusContains("verifica en Firefly")
        assertNoRestoredConfirmation()
        assertNoExpenseAbsenceClaim()

        app.terminate()
        launch(scenario: "normal")
        let retry = app.buttons["capture.retryPending"]
        reveal(retry)
        XCTAssertTrue(retry.exists)
        retry.tap()
        let received = app.staticTexts["home.status"]
        reveal(received)
        let summary = XCTNSPredicateExpectation(predicate: NSPredicate(
            format: "exists == true AND label CONTAINS %@ AND label CONTAINS %@ AND label CONTAINS %@",
            "1 respuesta(s) recibidas", "Cancelación recibida", "verifica si hubo un registro previo"
        ), object: received)
        XCTAssertEqual(XCTWaiter.wait(for: [summary], timeout: 5), .completed)
        assertNoExpenseAbsenceClaim()

        relaunch()
        openCapture("expense")
        assertNoRestoredConfirmation()
        assertReceivedUncertainCancellation()
        relaunch()
        openHistory()
        assertHistoryUncertainCancellation()
    }

    func testExpiredExpensePreviewIsTerminal() {
        launch(scenario: "expired")
        requestExpensePreview()
        let confirm = app.buttons["expense.confirm"]
        reveal(confirm)
        confirm.tap()
        assertStatusContains("vencida")
        XCTAssertFalse(app.buttons["expense.confirm"].exists)
        relaunch()
        openCapture("expense")
        assertNoRestoredConfirmation()
    }

    func testRefreshedExpenseCurrentSendingStaysDisabledOnRelaunch() {
        launch(scenario: "expense_refreshed_confirmation_sending")
        openCapture("reminder")
        checkConnection()
        assertConnectionContains("Servidor accesible")
        relaunch()
        openCapture("expense", expectsConfirmation: true)
        let confirm = app.buttons["expense.confirm"]
        reveal(confirm)
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        XCTAssertFalse(confirm.isEnabled)
        XCTAssertTrue(app.buttons["expense.cancel"].isEnabled)
        assertStatusContains("Envío anterior sin resultado")
        relaunch()
        openCapture("expense", expectsConfirmation: true)
        reveal(confirm)
        XCTAssertTrue(confirm.exists)
        XCTAssertFalse(confirm.isEnabled)
        XCTAssertTrue(app.buttons["expense.cancel"].isEnabled)
    }

    func testSendingCancellationRestoresRequestedWarningWithoutRetry() {
        launch(scenario: "expense_cancellation_sending")
        openCapture("reminder")
        checkConnection()
        assertConnectionContains("Servidor accesible")
        relaunch()
        openCapture("expense")
        assertNoRestoredConfirmation()
        assertStatusContains("Cancelación solicitada")
        assertStatusContains("verifica en Firefly")
        assertNoReceivedCancellationClaim()
        assertNoExpenseAbsenceClaim()

        relaunch()
        let retry = app.buttons["capture.retryPending"]
        reveal(retry)
        retry.tap()
        let result = app.staticTexts["home.status"]
        reveal(result)
        let unchanged = XCTNSPredicateExpectation(predicate: NSPredicate(
            format: "exists == true AND label == %@", "Sin capturas para reintentar"
        ), object: result)
        XCTAssertEqual(XCTWaiter.wait(for: [unchanged], timeout: 5), .completed)
        assertNoReceivedCancellationClaim()

        relaunch()
        openHistory()
        let warning = app.staticTexts.matching(NSPredicate(
            format: "label CONTAINS %@ AND label CONTAINS %@",
            "Cancelación solicitada", "verifica en Firefly"
        )).firstMatch
        reveal(warning)
        XCTAssertTrue(warning.waitForExistence(timeout: 5))
        assertNoRestoredConfirmation()
        assertNoReceivedCancellationClaim()
        assertNoExpenseAbsenceClaim()
    }

    // MARK: - Watch-specific navigation and native input

    private func launch(scenario: String = "normal") {
        app.launchArguments = [
            "--personal-watch-synthetic-ui",
            "--personal-watch-ui-case=\(caseID)",
            "--personal-watch-ui-scenario=\(scenario)"
        ]
        app.launch()
    }

    private func relaunch() {
        app.terminate()
        app.launch()
    }

    private func openCapture(_ action: String, expectsConfirmation: Bool = false) {
        let entry = app.buttons["capture.\(action)"]
        reveal(entry)
        XCTAssertTrue(entry.waitForExistence(timeout: 5))
        entry.tap()
        if expectsConfirmation {
            XCTAssertTrue(app.buttons["expense.confirm"].waitForExistence(timeout: 5))
        } else {
            XCTAssertTrue(app.textFields["capture.text"].waitForExistence(timeout: 5))
        }
    }

    private func enterCapture(_ action: String, text: String) {
        openCapture(action)
        let field = app.textFields["capture.text"]
        field.tap()
        // watchOS presents a native TextInputHost whose focused TextView is
        // separate from the original SwiftUI TextField. Exercise that editor.
        let nativeInput = app.textViews.matching(NSPredicate(
            format: "placeholderValue == %@", field.placeholderValue ?? ""
        )).firstMatch
        XCTAssertTrue(nativeInput.waitForExistence(timeout: 5))
        nativeInput.typeText(text)
        let done = app.buttons["Done"].firstMatch
        XCTAssertTrue(done.waitForExistence(timeout: 5))
        done.tap()
        waitForValue(text, in: field)
        let send = app.buttons["capture.send"]
        reveal(send)
        XCTAssertTrue(send.isEnabled)
    }

    private func openHistory() {
        let entry = app.buttons["history.open"]
        reveal(entry)
        XCTAssertTrue(entry.waitForExistence(timeout: 5))
        entry.tap()
    }

    private func retainReminder() {
        let start = app.buttons["capture.newReminder"]
        reveal(start)
        XCTAssertTrue(start.isEnabled)
        start.tap()
        // watchOS renders this native action without the SwiftUI identifier.
        let confirm = app.buttons["Conservar y abrir nuevo"].firstMatch
        reveal(confirm)
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "Watch retention confirmation"
        screenshot.lifetime = .keepAlways
        add(screenshot)
        confirm.tap()
    }

    private func checkConnection() {
        let check = app.buttons["capture.checkConnection"]
        reveal(check)
        XCTAssertTrue(check.isEnabled)
        check.tap()
    }

    private func assertHealth(scenario: String, contains text: String) {
        launch(scenario: scenario)
        openCapture("reminder")
        XCTAssertFalse(app.buttons["capture.send"].isEnabled)
        checkConnection()
        assertConnectionContains(text)
        XCTAssertFalse(app.buttons["capture.send"].isEnabled)
    }

    private func requestExpensePreview() {
        enterCapture("expense", text: "45 mil en Uber")
        app.buttons["capture.send"].tap()
        let confirm = app.buttons["expense.confirm"]
        reveal(confirm)
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["expense.cancel"].exists)
    }

    private func cancelExpense() {
        let cancel = app.buttons["expense.cancel"]
        reveal(cancel)
        XCTAssertTrue(cancel.isEnabled)
        cancel.tap()
    }

    private func assertRestoredOldReminder(file: StaticString = #filePath, line: UInt = #line) {
        let field = app.textFields["capture.text"]
        waitForValue("Recordatorio anterior sintético", in: field, file: file, line: line)
        XCTAssertFalse(field.isEnabled, file: file, line: line)
        XCTAssertEqual(app.buttons["capture.send"].label, "Reintentar", file: file, line: line)
    }

    private func assertOldReminderHistory(attempts: Int, error: String?, retained: Bool,
                                          file: StaticString = #filePath, line: UInt = #line) {
        let original = app.staticTexts["Recordatorio anterior sintético"]
        reveal(original)
        XCTAssertTrue(original.waitForExistence(timeout: 5), file: file, line: line)
        let count = app.staticTexts["Intentos: \(attempts)"]
        reveal(count)
        XCTAssertTrue(count.exists, file: file, line: line)
        if retained {
            let badge = app.staticTexts["Conservado · sin reintentos"]
            reveal(badge)
            XCTAssertTrue(badge.exists, file: file, line: line)
        }
        let details = app.buttons.matching(NSPredicate(
            format: "identifier == %@", "history.reminderDetails.synthetic-old-reminder-\(caseID)"
        )).firstMatch
        reveal(details)
        XCTAssertTrue(details.exists, file: file, line: line)
        details.tap()
        XCTAssertTrue(app.staticTexts["Recordatorio anterior sintético"].waitForExistence(timeout: 5), file: file, line: line)
        let state = app.staticTexts["Estado del envío: Por verificar"]
        reveal(state)
        XCTAssertTrue(state.exists, file: file, line: line)
        let identity = app.staticTexts.matching(NSPredicate(
            format: "label == %@", "ID: synthetic-old-reminder-\(caseID)"
        )).firstMatch
        reveal(identity)
        XCTAssertTrue(identity.exists, file: file, line: line)
        if let error {
            let value = app.staticTexts["Último error: \(error)"]
            reveal(value)
            XCTAssertTrue(value.exists, file: file, line: line)
        }
        if retained {
            let badge = app.staticTexts["Conservado localmente · sin reintentos"]
            reveal(badge)
            XCTAssertTrue(badge.exists, file: file, line: line)
        }
        XCTAssertFalse(app.staticTexts["Estado del envío: Cancelado local"].exists, file: file, line: line)
    }

    private func assertEmptyReminderEditor(file: StaticString = #filePath, line: UInt = #line) {
        let field = app.textFields["capture.text"]
        reveal(field)
        let enabled = XCTNSPredicateExpectation(predicate: NSPredicate(format: "enabled == true"), object: field)
        XCTAssertEqual(XCTWaiter.wait(for: [enabled], timeout: 5), .completed, file: file, line: line)
        XCTAssertTrue(["", "Ej. llamar mañana a las 2 pm"].contains(field.value as? String ?? "<missing>"), file: file, line: line)
        XCTAssertEqual(app.buttons["capture.send"].label, "Enviar", file: file, line: line)
        XCTAssertFalse(app.buttons["capture.send"].isEnabled, file: file, line: line)
    }

    private func assertNoRestoredConfirmation(file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertFalse(app.buttons["expense.confirm"].exists, file: file, line: line)
        XCTAssertFalse(app.buttons["expense.cancel"].exists, file: file, line: line)
    }

    private func assertReceivedUncertainCancellation(file: StaticString = #filePath, line: UInt = #line) {
        assertStatusContains("Cancelación recibida", file: file, line: line)
        assertStatusContains("verifica si hubo un registro previo", file: file, line: line)
        assertNoExpenseAbsenceClaim(file: file, line: line)
    }

    private func assertHistoryUncertainCancellation(file: StaticString = #filePath, line: UInt = #line) {
        let warning = app.staticTexts.matching(NSPredicate(
            format: "label CONTAINS %@ AND label CONTAINS %@",
            "Cancelación recibida", "verifica si hubo un registro previo"
        )).firstMatch
        reveal(warning)
        XCTAssertTrue(warning.waitForExistence(timeout: 5), file: file, line: line)
        XCTAssertFalse(app.buttons["expense.confirm"].exists, file: file, line: line)
        assertNoExpenseAbsenceClaim(file: file, line: line)
    }

    private func assertNoExpenseAbsenceClaim(file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertFalse(app.staticTexts.matching(NSPredicate(
            format: "label CONTAINS %@", "no se registró nada"
        )).firstMatch.exists, file: file, line: line)
    }

    private func assertNoReceivedCancellationClaim(file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertFalse(app.staticTexts.matching(NSPredicate(
            format: "label CONTAINS %@", "Cancelación recibida"
        )).firstMatch.exists, file: file, line: line)
    }

    private func reveal(_ element: XCUIElement) {
        // watchOS cannot always calculate an activation point for a read-only
        // StaticText. Scroll by its observed frame instead of asking it to hit.
        let screen = app.frame
        let navigationBottom = app.navigationBars.firstMatch.exists
            ? app.navigationBars.firstMatch.frame.maxY : screen.minY + 38
        let viewport = CGRect(x: screen.minX, y: navigationBottom,
                              width: screen.width, height: screen.maxY - navigationBottom - 4)
        for _ in 0..<8 {
            if element.exists {
                let frame = element.frame
                if !frame.isEmpty {
                    if (viewport.minY...viewport.maxY).contains(frame.midY)
                        || (frame.height > viewport.height && frame.intersects(viewport)) { return }
                    if frame.midY < viewport.minY { scrollContent(up: false); continue }
                }
            }
            scrollContent(up: true)
        }
    }

    private func scrollContent(up: Bool) {
        // Full-screen fast swipes overshoot short Watch lists and may recycle
        // the desired row. A short, slow drag mirrors a small finger scroll.
        let upper = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.45))
        let lower = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.80))
        let start = up ? lower : upper
        let end = up ? upper : lower
        start.press(forDuration: 0.05, thenDragTo: end, withVelocity: .slow, thenHoldForDuration: 0.1)
    }

    private func waitForValue(_ text: String, in field: XCUIElement,
                              file: StaticString = #filePath, line: UInt = #line) {
        let value = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == %@", text), object: field)
        XCTAssertEqual(XCTWaiter.wait(for: [value], timeout: 5), .completed, file: file, line: line)
    }

    private func assertStatusContains(_ text: String, file: StaticString = #filePath, line: UInt = #line) {
        let status = app.staticTexts["capture.status"]
        reveal(status)
        let value = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == true AND label CONTAINS %@", text), object: status)
        XCTAssertEqual(XCTWaiter.wait(for: [value], timeout: 5), .completed, file: file, line: line)
    }

    private func assertConnectionContains(_ text: String, file: StaticString = #filePath, line: UInt = #line) {
        let status = app.staticTexts["capture.connectionStatus"]
        reveal(status)
        let value = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == true AND label CONTAINS %@", text), object: status)
        XCTAssertEqual(XCTWaiter.wait(for: [value], timeout: 5), .completed, file: file, line: line)
    }
}
#endif
