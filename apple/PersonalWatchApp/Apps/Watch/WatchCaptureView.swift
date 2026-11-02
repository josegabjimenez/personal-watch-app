import SwiftUI
import WatchKit
import PersonalWatchCore

private struct PendingExpenseConfirmation {
    let text: String
    let operationID: String
    let token: String
    let summary: ExpenseConfirmationSummary
}

struct WatchCaptureView: View {
    let action: QuickActionKind

    @AppStorage("hermes.pseudonymousDeviceID") private var deviceID = ""
    @State private var text = ""
    @State private var statusMessage: String?
    @State private var isSaving = false
    @State private var confirmationIsSending = false
    @State private var queuedPayload: CapturePayloadV1?
    @State private var queuedIsSending = false
    @State private var durableCancellationMessage: String?
    @State private var pendingExpenseConfirmation: PendingExpenseConfirmation?

    private let accent = Color(red: 187 / 255, green: 0, blue: 14 / 255)

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Label(action.title, systemImage: action.symbolName)
                    .font(.headline)
                    .foregroundStyle(accent)

                if let pending = pendingExpenseConfirmation {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(formattedCOP(pending.summary.amountCOP))
                            .font(.title3.bold())
                        Text(pending.summary.description)
                        Text(pending.summary.sourceName)
                            .foregroundStyle(.secondary)
                        Text("\(pending.summary.categoryName) · \(pending.summary.budgetName)")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }

                    Button {
                        Task { @MainActor in
                            await confirmExpense(pending)
                        }
                    } label: {
                        Label("Registrar", systemImage: "checkmark.circle.fill")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(accent)
                    .disabled(isSaving || confirmationIsSending)
                    .accessibilityIdentifier("expense.confirm")

                    Button("Cancelar") {
                        Task { @MainActor in
                            await cancelExpense(pending)
                        }
                    }
                    .buttonStyle(.bordered)
                    .disabled(isSaving)
                    .accessibilityIdentifier("expense.cancel")
                } else {
                    TextField(prompt, text: $text)
                        .textInputAutocapitalization(.sentences)
                        .submitLabel(.done)
                        .disabled(queuedPayload != nil || isSaving || queuedIsSending)
                        .accessibilityIdentifier("capture.text")

                    Button {
                        Task { @MainActor in
                            await saveAndSend()
                        }
                    } label: {
                        if isSaving {
                            ProgressView()
                                .frame(maxWidth: .infinity)
                        } else {
                            Label(queuedPayload == nil ? "Enviar" : "Reintentar", systemImage: "paperplane.fill")
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(accent)
                    .disabled(trimmedText.isEmpty || isSaving || queuedIsSending)
                    .accessibilityIdentifier("capture.send")

                    if canStartAnotherGrocery {
                        Button("Otro artículo") {
                            startAnotherGrocery()
                        }
                        .buttonStyle(.bordered)
                        .accessibilityIdentifier("capture.anotherGrocery")
                        Text("El artículo anterior permanece en Historial. Usa esta opción para un artículo distinto.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }

                if let statusMessage {
                    Text(statusMessage)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .accessibilityIdentifier("capture.status")
                }
            }
            .padding(.horizontal, 4)
        }
        .navigationTitle(action.title)
        .onAppear {
            ensureDeviceIdentity()
        }
        .task {
            if action == .expense { await restorePendingExpenseConfirmation() }
            if pendingExpenseConfirmation == nil { await restoreQueuedCapture() }
        }
    }

    private var trimmedText: String {
        text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var canStartAnotherGrocery: Bool {
        action == .grocery && queuedPayload != nil && !isSaving && !queuedIsSending
    }

    @MainActor
    private func startAnotherGrocery() {
        guard canStartAnotherGrocery else { return }
        // Detach only this editor. The original outbox item remains unchanged.
        queuedPayload = nil
        text = ""
        statusMessage = "El artículo anterior permanece en Historial · escribe un artículo distinto"
    }

    private var prompt: String {
        switch action {
        case .expense:
            return "Ej. 45 mil en Uber"
        case .reminder:
            return "Ej. llamar mañana a las 2 pm"
        case .grocery:
            return "¿Qué falta?"
        case .general:
            return "Dile algo a Personal Watch App"
        }
    }

    @MainActor
    private func saveAndSend() async {
        guard !isSaving, !queuedIsSending, !trimmedText.isEmpty, action != .general else { return }
        await sendCapture(
            capturedText: trimmedText,
            userConfirmation: false,
            userCancellation: false,
            confirmationToken: nil,
            operationID: nil
        )
    }

    @MainActor
    private func confirmExpense(_ pending: PendingExpenseConfirmation) async {
        guard !isSaving, !confirmationIsSending else { return }
        await sendCapture(
            capturedText: pending.text,
            userConfirmation: true,
            userCancellation: false,
            confirmationToken: pending.token,
            operationID: pending.operationID
        )
    }

    @MainActor
    private func cancelExpense(_ pending: PendingExpenseConfirmation) async {
        guard !isSaving else { return }
        await sendCapture(
            capturedText: pending.text,
            userConfirmation: false,
            userCancellation: true,
            confirmationToken: nil,
            operationID: pending.operationID
        )
    }

    @MainActor
    private func sendCapture(
        capturedText: String,
        userConfirmation: Bool,
        userCancellation: Bool,
        confirmationToken: String?,
        operationID: String?
    ) async {
        guard !isSaving else { return }
        ensureDeviceIdentity()
        isSaving = true
        durableCancellationMessage = nil
        statusMessage = nil
        defer { isSaving = false }

        let factory = CaptureFactory(
            appVersion: appVersion,
            platform: "watchOS",
            osVersion: ProcessInfo.processInfo.operatingSystemVersionString,
            deviceID: PersonalWatchAppRuntime.isSynthetic ? "synthetic-ui-device" : deviceID,
            surface: "watch_app",
            nowISO8601: {
                ISO8601DateFormatter().string(from: Date())
            },
            makeRequestID: {
                UUID().uuidString.lowercased()
            }
        )
        let isInitialCapture = !userConfirmation && !userCancellation
        let retryMode: CaptureRetryMode = action == .grocery && isInitialCapture && queuedPayload != nil
            ? .manualGroceryRecovery : .standard
        let newPayload = factory.makePayload(
            kind: action,
            text: capturedText,
            requestWrite: action.requestsLiveWrite,
            userConfirmation: userConfirmation,
            userCancellation: userCancellation,
            confirmationToken: confirmationToken,
            operationID: operationID
        )
        var payload = isInitialCapture ? (queuedPayload ?? newPayload) : newPayload
        let outbox = FileOutboxStore(fileURL: outboxURL)

        do {
            let enqueued = try await outbox.enqueue(payload, now: payload.createdAt)
            payload = enqueued.payload
            if isInitialCapture {
                queuedPayload = payload
                queuedIsSending = enqueued.status == .sending
            }
            if userCancellation {
                // Local success is shown only after the cancellation and retry
                // suppression have been persisted in one locked transaction.
                pendingExpenseConfirmation = nil
                confirmationIsSending = false
                queuedPayload = nil
                queuedIsSending = false
                text = ""
                durableCancellationMessage = enqueued.cancellationMayHaveBeenSent == true
                    ? "Cancelación solicitada · el envío anterior puede haberse registrado; verifica en Firefly"
                    : "Cancelado en este dispositivo · aviso al servidor pendiente"
            }
            statusMessage = durableCancellationMessage ?? "Enviando…"

            guard
                let credentials = try PersonalWatchAppRuntime.credentials()
            else {
                statusMessage = durableCancellationMessage ?? "Guardado · configura desde iPhone"
                WKInterfaceDevice.current().play(.notification)
                return
            }

            let response = try await WatchCaptureDeliveryCoordinator.deliver(
                payload: payload,
                secret: credentials.secret,
                baseURL: credentials.baseURL,
                outbox: outbox,
                retryMode: retryMode
            )

            queuedPayload = nil
            queuedIsSending = false
            if userCancellation {
                guard
                    let operationID,
                    ["cancelled", "duplicate"].contains(response.status),
                    response.operationID == operationID,
                    OutboxDeliveryService.isSafe(response: response, for: payload)
                else {
                    throw OutboxDeliveryFailure.unsafeResponse
                }
                pendingExpenseConfirmation = nil
                text = ""
                statusMessage = enqueued.cancellationMayHaveBeenSent == true
                    ? "Cancelación recibida · verifica si hubo un registro previo en Firefly"
                    : (response.displayMessage ?? "Gasto cancelado")
                WKInterfaceDevice.current().play(.success)
                return
            }

            if
                action == .expense,
                response.status == "needs_confirmation",
                let operationID = response.operationID,
                let token = response.confirmationToken,
                let summary = response.confirmation
            {
                confirmationIsSending = false
                pendingExpenseConfirmation = PendingExpenseConfirmation(
                    text: capturedText,
                    operationID: operationID,
                    token: token,
                    summary: summary
                )
                statusMessage = "Revisa el gasto antes de registrarlo"
                WKInterfaceDevice.current().play(.notification)
                return
            }

            pendingExpenseConfirmation = nil
            if response.status != "needs_confirmation" {
                text = ""
            }
            statusMessage = response.displayMessage ?? "Enviado"
            WKInterfaceDevice.current().play(response.writeCommitted == true && response.status == "committed" ? .success : .notification)
        } catch let failure as OutboxDeliveryFailure {
            await refreshDeliveryState(payload, in: outbox)
            statusMessage = durableCancellationMessage ?? failure.localizedDescription
            WKInterfaceDevice.current().play(.failure)
        } catch let error as OutboxStoreError {
            statusMessage = durableCancellationMessage ?? error.localizedDescription
            if error == .deliveryInProgress {
                if isInitialCapture { queuedIsSending = true }
                else { confirmationIsSending = true }
            }
            WKInterfaceDevice.current().play(.failure)
        } catch {
            statusMessage = durableCancellationMessage ?? "No se pudo guardar"
            WKInterfaceDevice.current().play(.failure)
        }
    }

    @MainActor
    private func restorePendingExpenseConfirmation() async {
        guard pendingExpenseConfirmation == nil else { return }
        do {
            let outbox = FileOutboxStore(fileURL: outboxURL)
            let confirmations = try await outbox.loadPendingExpenseConfirmations()
            guard
                let item = confirmations.first,
                let response = item.lastResponse,
                let operationID = response.operationID,
                let token = response.confirmationToken,
                let summary = response.confirmation
            else { return }
            pendingExpenseConfirmation = PendingExpenseConfirmation(
                text: item.payload.capture.text,
                operationID: operationID,
                token: token,
                summary: summary
            )
            text = item.payload.capture.text
            let attempt = try await outbox.loadAll().first {
                $0.payload.route == .expense && $0.payload.context.userConfirmation
                    && $0.payload.context.operationID == operationID
            }
            confirmationIsSending = attempt?.status == .sending
            statusMessage = confirmationIsSending
                ? "Envío anterior sin resultado · verifica el registro; puedes solicitar cancelación"
                : (response.displayMessage ?? "Revisa el gasto antes de registrarlo")
        } catch {
            statusMessage = "No pude recuperar la confirmación pendiente"
        }
    }

    @MainActor
    private func refreshDeliveryState(_ payload: CapturePayloadV1, in store: FileOutboxStore) async {
        guard payload.context.userCancellation != true else { return }
        // If a response could not be persisted, the sending claim remains an
        // uncertain outcome. Do not offer another send until it is reconciled.
        let items = try? await store.loadAll()
        let status = items?.first(where: { $0.id == payload.requestID })?.status
        let isUncertain = status == nil || status == .sending
        if payload.context.userConfirmation { confirmationIsSending = isUncertain }
        else { queuedIsSending = isUncertain }
    }

    @MainActor
    private func restoreQueuedCapture() async {
        guard !isSaving, queuedPayload == nil else { return }
        do {
            let items = try await FileOutboxStore(fileURL: outboxURL).loadAll()
            guard let item = items.last(where: {
                $0.payload.route == action.route && [.pending, .failed, .sending].contains($0.status)
                    && !$0.payload.context.userConfirmation && $0.payload.context.userCancellation != true
            }) else {
                if pendingExpenseConfirmation == nil,
                   let response = items.last(where: {
                       $0.payload.route == action.route && $0.status == .sent
                           && $0.lastResponse?.status != "needs_confirmation"
                   })?.lastResponse {
                    statusMessage = response.displayMessage ?? response.question
                }
                return
            }
            queuedPayload = item.payload
            text = item.payload.capture.text
            queuedIsSending = item.status == .sending
            statusMessage = queuedIsSending
                ? "Envío anterior sin resultado · verifica el resultado antes de repetirlo"
                : "Captura pendiente · el reintento conserva su identificador"
        } catch {
            statusMessage = "No pude recuperar la captura pendiente"
        }
    }

    private func formattedCOP(_ amount: Int) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.groupingSeparator = "."
        formatter.decimalSeparator = ","
        formatter.maximumFractionDigits = 0
        let value = formatter.string(from: NSNumber(value: amount)) ?? String(amount)
        return "$\(value) COP"
    }

    private var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.1.0"
    }

    private var outboxURL: URL { PersonalWatchAppRuntime.outboxURL }

    private func ensureDeviceIdentity() {
        guard !PersonalWatchAppRuntime.isSynthetic else { return }
        if deviceID.isEmpty {
            deviceID = "watch-\(UUID().uuidString.lowercased())"
        }
    }
}

#Preview {
    NavigationStack {
        WatchCaptureView(action: .expense)
    }
}
