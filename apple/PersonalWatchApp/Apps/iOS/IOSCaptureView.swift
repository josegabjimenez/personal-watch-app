import SwiftUI
import PersonalWatchCore

/// The iPhone uses the same durable payloads and explicit expense confirmation
/// as the Watch. Navigating away never grants permission to record a preview.
struct IOSCaptureView: View {
    let action: QuickActionKind

    @AppStorage("hermes.pseudonymousDeviceID") private var deviceID = ""
    @State private var text = ""
    @State private var statusMessage: String?
    @State private var isBusy = false
    @State private var pending: PendingExpense?
    @State private var queuedPayload: CapturePayloadV1?
    @State private var deliveryInFlight = false
    @State private var showsReminderRetention = false
    @State private var retainedReminders: [OutboxItem] = []
    @State private var isCheckingConnection = false
    @State private var connectionMessage: String?
    @State private var connectionDiagnostic: String?

    private struct PendingExpense {
        let text: String
        let operationID: String
        let token: String
        let summary: ExpenseConfirmationSummary
    }

    var body: some View {
        Form {
            if let pending {
                Section("Revisa antes de registrar") {
                    LabeledContent("Importe", value: "$\(pending.summary.amountCOP.formatted(.number.locale(Locale(identifier: "es_CO")))) COP")
                    LabeledContent("Concepto", value: pending.summary.description)
                    LabeledContent("Cuenta", value: pending.summary.sourceName)
                    LabeledContent("Categoría", value: pending.summary.categoryName)
                    LabeledContent("Presupuesto", value: pending.summary.budgetName)

                    Button("Registrar gasto") {
                        Task { await confirm(pending) }
                    }
                    .accessibilityIdentifier("expense.confirm")
                    .disabled(isBusy || deliveryInFlight)

                    Button("Cancelar", role: .destructive) {
                        Task { await cancel(pending) }
                    }
                    .accessibilityIdentifier("expense.cancel")
                    .disabled(isBusy)
                }
            } else {
                Section(action.title) {
                    TextField(prompt, text: $text)
                        .accessibilityIdentifier("capture.text")
                        .disabled(queuedPayload != nil || isBusy || deliveryInFlight)
                    Button(queuedPayload == nil ? (action == .expense ? "Revisar gasto" : "Enviar") : "Reintentar") {
                        Task { await capture() }
                    }
                    .accessibilityIdentifier("capture.send")
                    .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isBusy || deliveryInFlight)

                    if action == .reminder, queuedPayload != nil {
                        Button("Nuevo recordatorio") { showsReminderRetention = true }
                            .disabled(isBusy)
                            .accessibilityIdentifier("capture.newReminder")
                        Text("Conserva el anterior en Historial antes de escribir una intención distinta.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }

                    if canStartAnotherGrocery {
                        Button("Otro artículo") {
                            startAnotherGrocery()
                        }
                        .accessibilityIdentifier("capture.anotherGrocery")
                        Text("El artículo anterior queda guardado. Usa esta opción para un artículo distinto.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            if let statusMessage {
                Section("Estado") {
                    Text(statusMessage)
                        .accessibilityIdentifier("capture.status")
                }
            }

            if action == .reminder, !retainedReminders.isEmpty {
                Section("Conservados en Historial de este iPhone") {
                    ForEach(retainedReminders) { item in
                        DisclosureGroup(item.payload.capture.text) {
                            Text("Sin reintentos en este dispositivo · no cancela un posible registro en Todoist")
                            Text("Capturado: \(item.payload.createdAt)")
                            Text("ID: \(item.id)")
                                .font(.caption.monospaced())
                            Text("Estado del envío: \(retainedStatus(item.status))")
                            Text("Intentos: \(item.attempts)")
                            if let error = item.lastError {
                                Text("Último error: \(error)")
                                    .font(.caption.monospaced())
                            }
                        }
                        .accessibilityIdentifier("history.retainedReminder.\(item.id)")
                    }
                }
            }

            if action == .reminder {
                Section("Conexión de este dispositivo") {
                    Button {
                        Task { await checkConnection() }
                    } label: {
                        if isCheckingConnection { ProgressView() }
                        else { Label("Comprobar conexión", systemImage: "network") }
                    }
                    .disabled(isBusy || isCheckingConnection)
                    .accessibilityIdentifier("capture.checkConnection")
                    Text("Solo comprueba conexión; no envía ni reintenta capturas.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    if let connectionMessage {
                        Text(connectionMessage)
                            .accessibilityIdentifier("capture.connectionStatus")
                    }
                    if let connectionDiagnostic {
                        Text(connectionDiagnostic)
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                    }
                }
                Section {
                    Text("Indica fecha y hora, por ejemplo: mañana a las 2 pm. Personal Watch App confirma la hora de Bogotá y si el aviso está verificado.")
                        .font(.footnote)
                }
            }
        }
        .navigationTitle(action.title)
        .confirmationDialog("Conservar el recordatorio anterior", isPresented: $showsReminderRetention, titleVisibility: .visible) {
            Button("Conservar y abrir nuevo") {
                Task { await retainReminderAndOpenDraft() }
            }
            .accessibilityIdentifier("capture.retainReminder.confirm")
            Button("Volver", role: .cancel) {}
        } message: {
            Text("Conserva su texto, fecha e identificador y detiene los reintentos de este dispositivo. No cancela un posible registro en Todoist. El nuevo borrador es para una intención distinta.")
        }
        .task {
            await PersonalWatchAppRuntime.prepareSyntheticStateIfNeeded()
            await restore()
        }
    }

    private var prompt: String {
        switch action {
        case .grocery: return "Ej. arroz"
        case .reminder: return "Ej. llamar mañana a las 2 pm"
        case .expense: return "Ej. 45 mil almuerzo por Nequi"
        case .general: return "Acción fuera del MVP"
        }
    }

    private var canStartAnotherGrocery: Bool {
        action == .grocery && queuedPayload != nil && !isBusy && !deliveryInFlight
    }

    @MainActor
    private func startAnotherGrocery() {
        guard canStartAnotherGrocery else { return }
        // Detach only this editor. The original outbox item remains unchanged.
        queuedPayload = nil
        text = ""
        statusMessage = "El artículo anterior queda guardado · escribe un artículo distinto"
    }

    @MainActor
    private func retainReminderAndOpenDraft() async {
        guard action == .reminder, !isBusy, let original = queuedPayload else { return }
        isBusy = true
        defer { isBusy = false }
        let store = FileOutboxStore(fileURL: PersonalWatchAppRuntime.outboxURL)
        do {
            let retained = try await store.retainReminderForReview(
                requestID: original.requestID, now: ISO8601DateFormatter().string(from: Date())
            )
            retainedReminders = (try? await store.loadAll().filter { $0.payload.route == .reminder && $0.isRetrySuppressed }) ?? [retained]
            openDistinctReminderDraft()
        } catch {
            // A late terminal receipt may win, but an unverified result or
            // persistence failure must leave the original editor attached.
            if let item = try? await store.loadAll().first(where: { $0.id == original.requestID }),
               item.payload == original, item.status == .sent,
               let response = item.lastResponse,
               OutboxDeliveryService.isSafe(response: response, for: original) {
                openDistinctReminderDraft()
            } else {
                statusMessage = "No pude conservar el recordatorio · el texto y su estado siguen aquí"
            }
        }
    }

    @MainActor
    private func openDistinctReminderDraft() {
        queuedPayload = nil
        deliveryInFlight = false
        text = ""
        statusMessage = "Anterior conservado en Historial · escribe un recordatorio distinto"
    }

    private func retainedStatus(_ status: OutboxStatus) -> String {
        switch status {
        case .pending: return "Pendiente"
        case .sending, .failed: return "Por verificar"
        case .sent: return "Respuesta recibida"
        case .cancelled: return "Cancelado local"
        }
    }

    @MainActor
    private func checkConnection() async {
        guard action == .reminder, !isBusy, !isCheckingConnection else { return }
        isCheckingConnection = true
        connectionMessage = nil
        connectionDiagnostic = nil
        defer { isCheckingConnection = false }
        do {
            guard let credentials = try PersonalWatchAppRuntime.credentials() else {
                connectionMessage = "Configuración pendiente · comprueba la app del iPhone"
                return
            }
            let result = await PersonalWatchAppRuntime.checkServerConnection(baseURL: credentials.baseURL)
            connectionMessage = result.message
            connectionDiagnostic = result.diagnosticCode
        } catch {
            connectionMessage = "Configuración no disponible · comprueba la app del iPhone"
            connectionDiagnostic = "configuration_unavailable"
        }
    }

    @MainActor
    private func capture() async {
        guard !isBusy, !deliveryInFlight, action != .general else { return }
        let capturedText = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !capturedText.isEmpty else { return }
        let retryMode: CaptureRetryMode = action == .grocery && queuedPayload != nil
            ? .manualGroceryRecovery : .standard
        let payload = queuedPayload ?? makePayload(text: capturedText)
        await submit(payload, retryMode: retryMode)
    }

    @MainActor
    private func confirm(_ pending: PendingExpense) async {
        guard !isBusy, !deliveryInFlight else { return }
        await submit(makePayload(text: pending.text, confirmed: true, token: pending.token, operationID: pending.operationID))
    }

    @MainActor
    private func cancel(_ pending: PendingExpense) async {
        guard !isBusy else { return }
        await submit(makePayload(text: pending.text, cancelled: true, operationID: pending.operationID))
    }

    @MainActor
    private func submit(_ payload: CapturePayloadV1, retryMode: CaptureRetryMode = .standard) async {
        guard !isBusy else { return }
        isBusy = true
        statusMessage = nil
        defer { isBusy = false }
        let store = FileOutboxStore(fileURL: PersonalWatchAppRuntime.outboxURL)
        var cancellationMessage: String?
        var enqueuedRequestID: String?
        do {
            // enqueue serializes cancellation with retry claims and returns the
            // original confirmation request after repeated taps or a relaunch.
            let item = try await store.enqueue(payload, now: payload.createdAt)
            let canonical = item.payload
            enqueuedRequestID = canonical.requestID
            if canonical.context.userCancellation == true {
                pending = nil
                queuedPayload = nil
                text = ""
                cancellationMessage = OutboxPresentation.message(for: item)
                    ?? "Cancelado en este dispositivo · pendiente de confirmar con el servidor"
                statusMessage = cancellationMessage
            } else if !canonical.context.userConfirmation {
                queuedPayload = canonical
            }
            guard let credentials = try PersonalWatchAppRuntime.credentials() else {
                statusMessage = cancellationMessage ?? "Guardado para reintento · configura Personal Watch App desde el iPhone"
                return
            }
            let delivery = OutboxDeliveryService(store: store, client: PersonalWatchAppRuntime.makeClient(baseURL: credentials.baseURL))
            let response = try await delivery.deliver(payload: canonical, secret: credentials.secret, retryMode: retryMode)
            queuedPayload = nil
            deliveryInFlight = false
            if response.status == "needs_confirmation",
               let operationID = response.operationID,
               let token = response.confirmationToken,
               let summary = response.confirmation {
                pending = PendingExpense(text: canonical.capture.text, operationID: operationID, token: token, summary: summary)
                text = canonical.capture.text
            } else {
                pending = nil
                if response.writeCommitted == true || canonical.context.userCancellation == true || action == .reminder {
                    text = ""
                }
            }
            statusMessage = OutboxPresentation.message(for: item, response: response) ?? "Respuesta recibida · revisa el resultado"
        } catch let failure as OutboxDeliveryFailure {
            await refreshDeliveryState(store: store, requestID: enqueuedRequestID)
            statusMessage = cancellationMessage ?? failure.localizedDescription
        } catch let failure as OutboxStoreError {
            await refreshDeliveryState(store: store, requestID: enqueuedRequestID)
            if failure == .deliveryInProgress { deliveryInFlight = true }
            statusMessage = cancellationMessage ?? failure.localizedDescription
        } catch {
            await refreshDeliveryState(store: store, requestID: enqueuedRequestID)
            statusMessage = cancellationMessage ?? "No se pudo guardar la captura · inténtalo de nuevo"
        }
    }

    @MainActor
    private func refreshDeliveryState(store: FileOutboxStore, requestID: String?) async {
        guard let requestID else { return }
        do {
            let status = try await store.loadAll().first(where: { $0.id == requestID })?.status
            deliveryInFlight = status == nil || status == .sending
        } catch {
            deliveryInFlight = true
        }
    }

    @MainActor
    private func restore() async {
        guard !isBusy else { return }
        do {
            let store = FileOutboxStore(fileURL: PersonalWatchAppRuntime.outboxURL)
            let items = try await store.loadAll()
            retainedReminders = items.filter { $0.payload.route == .reminder && $0.isRetrySuppressed }
            if action == .expense,
               let item = try await store.loadPendingExpenseConfirmations().first,
               let response = item.lastResponse,
               let operationID = response.operationID,
               let token = response.confirmationToken,
               let summary = response.confirmation {
                pending = PendingExpense(text: item.payload.capture.text, operationID: operationID, token: token, summary: summary)
                deliveryInFlight = items.contains {
                    $0.payload.context.operationID == operationID && $0.payload.context.userConfirmation && $0.status == .sending
                }
                statusMessage = deliveryInFlight
                    ? "Envío anterior sin resultado · verifica el gasto antes de volver a registrarlo"
                    : "Revisa el gasto antes de registrarlo"
            } else if let item = items.last(where: {
                $0.payload.route == action.route && !$0.isRetrySuppressed
                    && [.pending, .failed, .sending].contains($0.status)
                    && !$0.payload.context.userConfirmation && $0.payload.context.userCancellation != true
            }) {
                queuedPayload = item.payload
                text = item.payload.capture.text
                deliveryInFlight = item.status == .sending
                statusMessage = deliveryInFlight
                    ? "Envío anterior sin resultado · verifica el resultado antes de repetirlo"
                    : "Captura pendiente · el reintento conserva su identificador"
            } else if let cancellation = items.last(where: {
                $0.payload.route == action.route && $0.payload.context.userCancellation == true
                    && $0.cancellationMayHaveBeenSent == true && [.pending, .failed, .sending].contains($0.status)
            }) {
                statusMessage = OutboxPresentation.message(for: cancellation)
            } else if let latest = items.last(where: {
                $0.payload.route == action.route && !$0.isRetrySuppressed && $0.status == .sent
                    && $0.lastResponse?.status != "needs_confirmation"
            }), let message = OutboxPresentation.message(for: latest) {
                statusMessage = message
            } else if action == .reminder, !retainedReminders.isEmpty {
                statusMessage = "Anterior conservado en Historial · escribe un recordatorio distinto"
            }
        } catch {
            statusMessage = "No se pudo recuperar la captura pendiente"
        }
    }

    private func makePayload(text: String, confirmed: Bool = false, cancelled: Bool = false,
                             token: String? = nil, operationID: String? = nil) -> CapturePayloadV1 {
        if deviceID.isEmpty && !PersonalWatchAppRuntime.isSynthetic { deviceID = "iphone-\(UUID().uuidString.lowercased())" }
        return CaptureFactory(
            appVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.3.4",
            platform: "iOS", osVersion: ProcessInfo.processInfo.operatingSystemVersionString,
            deviceID: PersonalWatchAppRuntime.isSynthetic ? "synthetic-ui-device" : deviceID, surface: "iphone_app",
            nowISO8601: { ISO8601DateFormatter().string(from: Date()) },
            makeRequestID: { UUID().uuidString.lowercased() }
        ).makePayload(kind: action, text: text, modality: "iphone_text", requestWrite: action.requestsLiveWrite,
                      userConfirmation: confirmed, userCancellation: cancelled,
                      confirmationToken: token, operationID: operationID)
    }
}
