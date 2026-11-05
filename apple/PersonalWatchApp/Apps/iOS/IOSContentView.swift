import SwiftUI
import PersonalWatchCore

struct IOSContentView: View {
    @StateObject private var watchBootstrap = IPhoneWatchBootstrapCoordinator()

    @AppStorage("hermes.baseURL") private var savedBaseURL = ""
    @AppStorage("hermes.pseudonymousDeviceID") private var deviceID = ""
    @State private var endpointInput = ""
    @State private var secretInput = ""
    @State private var secretConfigured = false
    @State private var statusMessage: String?
    @State private var isSaving = false
    @State private var isTesting = false
    @State private var isTestingHMAC = false
    @State private var isSendingToWatch = false
    @State private var isLoadingWatchDiagnostics = false
    @State private var watchDiagnostics: WatchOutboxDiagnostics?
    @State private var localOutboxDiagnostics = LocalOutboxDiagnostics.empty
    @State private var isRetryingLocalOutbox = false
    @AppStorage("hermes.pendingExpensePreview") private var showExpensePreview = false

    private let secretStore = KeychainRouteSecretStore()
    private let accent = Color(red: 187 / 255, green: 0, blue: 14 / 255)

    var body: some View {
        NavigationStack {
            Form {
                Section("Acciones") {
                    ForEach([QuickActionKind.grocery, .reminder, .expense]) { action in
                        NavigationLink {
                            IOSCaptureView(action: action)
                        } label: {
                            Label(action.title, systemImage: action.symbolName)
                        }
                        .accessibilityIdentifier("capture.\(action.rawValue)")
                    }
                }

                Section("BFF por Tailscale") {
                    TextField("https://<TAILSCALE_DNS_NAME>:8650", text: $endpointInput)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)

                    Button {
                        Task { @MainActor in
                            await testConnection()
                        }
                    } label: {
                        if isTesting {
                            ProgressView()
                        } else {
                            Label("Probar conexión", systemImage: "network")
                        }
                    }
                    .disabled(endpointInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isTesting)
                }

                Section("Autenticación") {
                    SecureField("Secreto HMAC de la ruta", text: $secretInput)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()

                    HStack {
                        Label(
                            secretConfigured ? "Secreto configurado" : "Secreto pendiente",
                            systemImage: secretConfigured ? "checkmark.shield" : "exclamationmark.shield"
                        )
                        .foregroundStyle(secretConfigured ? .green : .secondary)
                        Spacer()
                    }

                    Button {
                        Task { @MainActor in
                            await saveConfiguration()
                        }
                    } label: {
                        if isSaving {
                            ProgressView()
                        } else {
                            Label("Guardar configuración", systemImage: "lock.shield")
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(accent)
                    .disabled(isSaving)

                    Button {
                        Task { @MainActor in
                            await testHMAC()
                        }
                    } label: {
                        if isTestingHMAC {
                            ProgressView()
                        } else {
                            Label("Probar HMAC", systemImage: "checkmark.shield")
                        }
                    }
                    .disabled(!secretConfigured || savedBaseURL.isEmpty || isTestingHMAC)
                }

                Section("Apple Watch") {
                    Label(
                        watchBootstrap.activationMessage,
                        systemImage: watchBootstrap.isReachable ? "applewatch.radiowaves.left.and.right" : "applewatch.slash"
                    )
                    .foregroundStyle(watchBootstrap.isReachable ? .green : .secondary)

                    Button {
                        Task { @MainActor in
                            await sendConfigurationToWatch()
                        }
                    } label: {
                        if isSendingToWatch {
                            ProgressView()
                        } else {
                            Label("Enviar configuración al Watch", systemImage: "iphone.and.arrow.forward")
                        }
                    }
                    .disabled(!secretConfigured || !watchBootstrap.isReachable || isSendingToWatch)

                    Button {
                        requestWatchDiagnostics()
                    } label: {
                        if isLoadingWatchDiagnostics {
                            ProgressView()
                        } else {
                            Label("Actualizar diagnóstico", systemImage: "waveform.path.ecg")
                        }
                    }
                    .disabled(!watchBootstrap.isReachable || isLoadingWatchDiagnostics)

                    if let diagnostics = watchDiagnostics {
                        LabeledContent("Watch configurado", value: diagnostics.configured ? "Sí" : "No")
                        LabeledContent("Outbox legible", value: diagnostics.outboxReadable ? "Sí" : "No")
                        LabeledContent("Total", value: "\(diagnostics.total)")
                        LabeledContent("Enviados", value: "\(diagnostics.sent)")
                        LabeledContent("Pendientes", value: "\(diagnostics.pending + diagnostics.sending)")
                        LabeledContent("Fallidos", value: "\(diagnostics.failed)")
                        if let path = diagnostics.lastDeliveryPath {
                            LabeledContent("Última ruta", value: watchDeliveryPathTitle(path))
                        }
                    }

                    Text("Abre Personal Watch App en el iPhone y el Apple Watch. El secreto viaja solo en un mensaje interactivo y se guarda inmediatamente en Keychain del Watch.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("iPhone y Siri") {
                    LabeledContent("Total local", value: "\(localOutboxDiagnostics.total)")
                    LabeledContent("Enviados", value: "\(localOutboxDiagnostics.sent)")
                    LabeledContent("Pendientes", value: "\(localOutboxDiagnostics.pending)")
                    LabeledContent("Fallidos", value: "\(localOutboxDiagnostics.failed)")

                    Button {
                        Task { @MainActor in
                            await retryLocalOutbox()
                        }
                    } label: {
                        if isRetryingLocalOutbox {
                            ProgressView()
                        } else {
                            Label("Reintentar capturas del iPhone", systemImage: "arrow.clockwise")
                        }
                    }
                    .disabled(
                        !secretConfigured ||
                        localOutboxDiagnostics.deliverable == 0 ||
                        isRetryingLocalOutbox
                    )

                    Text("Incluye capturas iniciadas por Siri o Shortcuts en el iPhone. El contenido permanece local; aquí solo se muestran contadores.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                if let statusMessage {
                    Section("Estado") {
                        Text(statusMessage)
                    }
                }
            }
            .navigationTitle("Personal Watch App")
            .navigationDestination(isPresented: $showExpensePreview) {
                IOSCaptureView(action: .expense)
            }
            .onAppear {
                ensureDeviceIdentity()
                endpointInput = PersonalWatchAppRuntime.isSynthetic ? "" : savedBaseURL
                Task { @MainActor in
                    await refreshSecretStatus()
                    await refreshLocalOutbox()
                }
            }
        }
    }

    @MainActor
    private func saveConfiguration() async {
        guard !PersonalWatchAppRuntime.isSynthetic else { return }
        isSaving = true
        defer { isSaving = false }

        do {
            let baseURL = try EndpointValidator.normalizedBaseURL(from: endpointInput)
            let secret = secretInput.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !secret.isEmpty else {
                statusMessage = "Ingresa el secreto HMAC"
                return
            }

            try await secretStore.saveRouteSecret(secret)
            savedBaseURL = baseURL.absoluteString
            endpointInput = baseURL.absoluteString
            secretInput = ""
            secretConfigured = true
            statusMessage = "Configuración guardada localmente"
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    @MainActor
    private func testConnection() async {
        guard !PersonalWatchAppRuntime.isSynthetic else { return }
        isTesting = true
        defer { isTesting = false }

        do {
            let baseURL = try EndpointValidator.normalizedBaseURL(from: endpointInput)
            let healthURL = EndpointValidator.healthURL(from: baseURL)
            let (data, response) = try await URLSession.shared.data(from: healthURL)
            guard let httpResponse = response as? HTTPURLResponse else {
                statusMessage = "Respuesta HTTP inválida"
                return
            }
            guard (200..<300).contains(httpResponse.statusCode) else {
                statusMessage = "Health respondió HTTP \(httpResponse.statusCode)"
                return
            }

            let health = try? JSONDecoder().decode(BFFHealthResponse.self, from: data)
            statusMessage = health.map { "Conectado · \($0.mode ?? $0.status)" } ?? "Conectado · HTTP \(httpResponse.statusCode)"
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    @MainActor
    private func requestWatchDiagnostics() {
        guard !PersonalWatchAppRuntime.isSynthetic else { return }
        isLoadingWatchDiagnostics = true
        watchBootstrap.requestDiagnostics { result in
            isLoadingWatchDiagnostics = false
            switch result {
            case .success(let diagnostics):
                watchDiagnostics = diagnostics
                statusMessage = "Diagnóstico del Watch actualizado"
            case .failure(let error):
                statusMessage = error.localizedDescription
            }
        }
    }

    @MainActor
    private func sendConfigurationToWatch() async {
        guard !PersonalWatchAppRuntime.isSynthetic else { return }
        isSendingToWatch = true

        do {
            guard let secret = try await secretStore.loadRouteSecret(), !secret.isEmpty else {
                secretConfigured = false
                statusMessage = "Guarda primero el secreto HMAC"
                isSendingToWatch = false
                return
            }
            let baseURL = try EndpointValidator.normalizedBaseURL(from: savedBaseURL)
            watchBootstrap.sendConfiguration(baseURL: baseURL, routeSecret: secret) { result in
                isSendingToWatch = false
                switch result {
                case .success:
                    statusMessage = "Configuración segura enviada al Watch"
                case .failure(let error):
                    statusMessage = error.localizedDescription
                }
            }
        } catch {
            isSendingToWatch = false
            statusMessage = error.localizedDescription
        }
    }

    @MainActor
    private func testHMAC() async {
        guard !PersonalWatchAppRuntime.isSynthetic else { return }
        isTestingHMAC = true
        defer { isTestingHMAC = false }

        do {
            guard let secret = try await secretStore.loadRouteSecret(), !secret.isEmpty else {
                secretConfigured = false
                statusMessage = "Guarda primero el secreto HMAC"
                return
            }

            ensureDeviceIdentity()
            let baseURL = try EndpointValidator.normalizedBaseURL(from: savedBaseURL)
            let factory = CaptureFactory(
                appVersion: appVersion,
                platform: "iOS",
                osVersion: ProcessInfo.processInfo.operatingSystemVersionString,
                deviceID: deviceID,
                surface: "iphone_app",
                nowISO8601: { ISO8601DateFormatter().string(from: Date()) },
                makeRequestID: { UUID().uuidString.lowercased() }
            )
            let payload = factory.makePayload(
                kind: .general,
                text: "Personal Watch App HMAC connectivity check"
            )
            let client = WebhookClient(endpoint: EndpointValidator.captureURL(from: baseURL))
            let response = try await client.submit(payload: payload, secret: secret)

            guard
                response.dryRun == true,
                response.plan?.wouldWrite == false,
                response.requestID == payload.requestID
            else {
                statusMessage = "Respuesta insegura: se esperaba dry-run"
                return
            }
            statusMessage = "HMAC válido · dry-run"
        } catch let error as WebhookClientError {
            switch error {
            case .unacceptableStatus(let statusCode, _) where statusCode == 401:
                statusMessage = "HMAC rechazado · verifica que coincida con el servidor"
            case .unacceptableStatus(let statusCode, _):
                statusMessage = "Prueba HMAC respondió HTTP \(statusCode)"
            case .invalidHTTPResponse:
                statusMessage = "Respuesta HTTP inválida"
            }
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    @MainActor
    private func refreshLocalOutbox() async {
        do {
            let items = try await FileOutboxStore(fileURL: localOutboxURL).loadAll()
            localOutboxDiagnostics = LocalOutboxDiagnostics(items: items)
        } catch {
            statusMessage = "No se pudo leer el outbox del iPhone"
        }
    }

    @MainActor
    private func retryLocalOutbox() async {
        isRetryingLocalOutbox = true
        defer { isRetryingLocalOutbox = false }

        do {
            guard let credentials = try PersonalWatchAppRuntime.credentials() else {
                secretConfigured = false
                statusMessage = "Guarda primero el secreto HMAC"
                return
            }
            let store = FileOutboxStore(fileURL: localOutboxURL)
            let items = try await store.loadDeliverable()
            guard !items.isEmpty else {
                await refreshLocalOutbox()
                statusMessage = "Sin capturas del iPhone para reintentar"
                return
            }

            let delivery = OutboxDeliveryService(
                store: store,
                client: PersonalWatchAppRuntime.makeClient(baseURL: credentials.baseURL)
            )
            var sent = 0
            var failed = 0
            var results: [String] = []
            for item in items {
                do {
                    let response = try await delivery.deliver(payload: item.payload, secret: credentials.secret)
                    if let message = response.displayMessage ?? response.question { results.append(message) }
                    sent += 1
                } catch {
                    failed += 1
                }
            }
            await refreshLocalOutbox()
            let summary = failed == 0
                ? "\(sent) respuesta(s) recibidas · revisa el resultado de cada captura"
                : "\(sent) enviadas · \(failed) aún pendientes"
            statusMessage = ([summary] + results).joined(separator: "\n")
        } catch {
            await refreshLocalOutbox()
            statusMessage = error.localizedDescription
        }
    }

    @MainActor
    private func refreshSecretStatus() async {
        guard !PersonalWatchAppRuntime.isSynthetic else {
            secretConfigured = true
            return
        }
        do {
            secretConfigured = try await secretStore.loadRouteSecret() != nil
        } catch {
            secretConfigured = false
            statusMessage = "No se pudo leer Keychain"
        }
    }

    private var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.1.0"
    }

    private var localOutboxURL: URL {
        PersonalWatchAppRuntime.outboxURL
    }

    private func watchDeliveryPathTitle(_ path: String) -> String {
        switch path {
        case "direct_https":
            return "Directo HTTPS"
        case "iphone_fallback":
            return "vía iPhone"
        default:
            return "Desconocida"
        }
    }

    private func ensureDeviceIdentity() {
        guard !PersonalWatchAppRuntime.isSynthetic else { return }
        if deviceID.isEmpty {
            deviceID = "iphone-\(UUID().uuidString.lowercased())"
        }
    }
}

private struct LocalOutboxDiagnostics {
    let total: Int
    let sent: Int
    let pending: Int
    let failed: Int
    let deliverable: Int

    static let empty = LocalOutboxDiagnostics(
        total: 0,
        sent: 0,
        pending: 0,
        failed: 0,
        deliverable: 0
    )

    init(total: Int, sent: Int, pending: Int, failed: Int, deliverable: Int) {
        self.total = total
        self.sent = sent
        self.pending = pending
        self.failed = failed
        self.deliverable = deliverable
    }

    init(items: [OutboxItem]) {
        total = items.count
        sent = items.filter { $0.status == .sent }.count
        pending = items.filter { $0.status == .pending || $0.status == .sending }.count
        failed = items.filter { $0.status == .failed }.count
        deliverable = items.filter { [.pending, .failed].contains($0.status) && $0.attempts < 5 }.count
    }
}

private struct BFFHealthResponse: Decodable {
    let status: String
    let service: String?
    let mode: String?
}

#Preview {
    IOSContentView()
}
