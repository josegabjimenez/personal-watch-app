import AppIntents
import Foundation
import PersonalWatchCore

enum PersonalWatchAppIntentCaptureRunner {
    static func capture(kind: QuickActionKind, text: String) async -> String {
        guard kind != .general else { return "La captura general está fuera de este MVP" }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return "No recibí ningún texto"
        }

        let defaults = UserDefaults.standard
        let deviceID = stableDeviceID(defaults: defaults)
        let payload = CaptureFactory(
            appVersion: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.1.0",
            platform: platform,
            osVersion: ProcessInfo.processInfo.operatingSystemVersionString,
            deviceID: deviceID,
            surface: surface,
            nowISO8601: {
                ISO8601DateFormatter().string(from: Date())
            },
            makeRequestID: {
                UUID().uuidString.lowercased()
            }
        ).makePayload(
            kind: kind,
            text: trimmed,
            rawText: text,
            modality: "app_intent",
            requestWrite: kind.requestsLiveWrite
        )

        let outbox = FileOutboxStore(fileURL: PersonalWatchAppRuntime.outboxURL)
        do {
            _ = try await outbox.enqueue(payload, now: payload.createdAt)
        } catch {
            return "No se pudo guardar la captura"
        }

        guard
            let credentials = try? PersonalWatchAppRuntime.credentials()
        else {
            return "Guardado. Configura Personal Watch App desde el iPhone"
        }

        do {
            let response: CaptureResponseV1
            #if os(watchOS)
            response = try await WatchCaptureDeliveryCoordinator.deliver(
                payload: payload,
                secret: credentials.secret,
                baseURL: credentials.baseURL,
                outbox: outbox
            )
            #else
            let delivery = OutboxDeliveryService(
                store: outbox,
                client: PersonalWatchAppRuntime.makeClient(baseURL: credentials.baseURL)
            )
            response = try await delivery.deliver(payload: payload, secret: credentials.secret)
            #endif
            if kind == .expense, response.status == "needs_confirmation" {
                defaults.set(true, forKey: "hermes.pendingExpensePreview")
                return "Gasto preparado. Abre Gasto en Personal Watch App, revisa la cuenta y el importe y toca Registrar para confirmarlo"
            }
            return response.displayMessage ?? response.question ?? "Respuesta recibida · revisa el resultado en Personal Watch App"
        } catch let failure as OutboxDeliveryFailure {
            return failure.localizedDescription
        } catch {
            return "Guardado para reintento"
        }
    }

    private static func stableDeviceID(defaults: UserDefaults) -> String {
        if PersonalWatchAppRuntime.isSynthetic { return "synthetic-ui-device" }
        if let existing = defaults.string(forKey: "hermes.pseudonymousDeviceID"), !existing.isEmpty {
            return existing
        }
        let prefix = platform == "watchOS" ? "watch" : "iphone"
        let generated = "\(prefix)-\(UUID().uuidString.lowercased())"
        defaults.set(generated, forKey: "hermes.pseudonymousDeviceID")
        return generated
    }

    private static var platform: String {
        #if os(watchOS)
        "watchOS"
        #else
        "iOS"
        #endif
    }

    private static var surface: String {
        #if os(watchOS)
        "app_intent_watch"
        #else
        "app_intent_iphone"
        #endif
    }
}

struct CaptureExpenseIntent: AppIntent {
    static var title: LocalizedStringResource = "Registrar gasto en Personal Watch App"
    static var description = IntentDescription("Prepara un gasto COP para revisarlo y confirmarlo en Personal Watch App.")
    static var openAppWhenRun = true
    static var authenticationPolicy: IntentAuthenticationPolicy = .requiresLocalDeviceAuthentication

    @Parameter(
        title: "Gasto",
        requestValueDialog: IntentDialog("¿Qué gasto quieres registrar?")
    )
    var text: String

    func perform() async throws -> some IntentResult & ProvidesDialog {
        let message = await PersonalWatchAppIntentCaptureRunner.capture(kind: .expense, text: text)
        return .result(dialog: IntentDialog(stringLiteral: message))
    }
}

struct CaptureReminderIntent: AppIntent {
    static var title: LocalizedStringResource = "Crear recordatorio en Personal Watch App"
    static var description = IntentDescription("Crea un recordatorio con fecha y hora de Bogotá y muestra el estado del aviso.")
    static var openAppWhenRun = false
    static var authenticationPolicy: IntentAuthenticationPolicy = .requiresLocalDeviceAuthentication

    @Parameter(
        title: "Recordatorio",
        requestValueDialog: IntentDialog("¿Qué quieres recordar?")
    )
    var text: String

    func perform() async throws -> some IntentResult & ProvidesDialog {
        let message = await PersonalWatchAppIntentCaptureRunner.capture(kind: .reminder, text: text)
        return .result(dialog: IntentDialog(stringLiteral: message))
    }
}

struct CaptureGroceryIntent: AppIntent {
    static var title: LocalizedStringResource = "Agregar al mercado en Personal Watch App"
    static var description = IntentDescription("Agrega un artículo a la lista de mercado con Personal Watch App.")
    static var openAppWhenRun = false
    static var authenticationPolicy: IntentAuthenticationPolicy = .requiresLocalDeviceAuthentication

    @Parameter(
        title: "Artículo",
        requestValueDialog: IntentDialog("¿Qué quieres agregar al mercado?")
    )
    var text: String

    func perform() async throws -> some IntentResult & ProvidesDialog {
        let message = await PersonalWatchAppIntentCaptureRunner.capture(kind: .grocery, text: text)
        return .result(dialog: IntentDialog(stringLiteral: message))
    }
}

// This legacy type identity is retained so saved shortcuts still resolve.
struct GeneralHermesCaptureIntent: AppIntent {
    static var title: LocalizedStringResource = "Captura heredada de Personal Watch App"
    static var description = IntentDescription("Acción heredada no disponible en el MVP de Personal Watch App.")
    static var openAppWhenRun = false
    static var authenticationPolicy: IntentAuthenticationPolicy = .requiresLocalDeviceAuthentication

    @Parameter(
        title: "Captura",
        requestValueDialog: IntentDialog("¿Qué quieres capturar?")
    )
    var text: String

    func perform() async throws -> some IntentResult & ProvidesDialog {
        let message = await PersonalWatchAppIntentCaptureRunner.capture(kind: .general, text: text)
        return .result(dialog: IntentDialog(stringLiteral: message))
    }
}

// Preserve the provider's registered Swift identity; its visible name comes from the app.
struct HermesCaptureAppShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: CaptureExpenseIntent(),
            phrases: [
                "Registrar gasto con \(.applicationName)",
                "Nuevo gasto en \(.applicationName)"
            ],
            shortTitle: "Registrar gasto",
            systemImageName: "creditcard"
        )
        AppShortcut(
            intent: CaptureReminderIntent(),
            phrases: [
                "Crear recordatorio con \(.applicationName)",
                "Recuérdame algo con \(.applicationName)"
            ],
            shortTitle: "Crear recordatorio",
            systemImageName: "bell"
        )
        AppShortcut(
            intent: CaptureGroceryIntent(),
            phrases: [
                "Agregar al mercado con \(.applicationName)",
                "Mercado en \(.applicationName)"
            ],
            shortTitle: "Agregar al mercado",
            systemImageName: "cart"
        )
    }

    static var shortcutTileColor: ShortcutTileColor = .red
}
