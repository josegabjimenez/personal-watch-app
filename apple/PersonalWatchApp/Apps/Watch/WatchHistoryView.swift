import SwiftUI
import PersonalWatchCore

struct WatchHistoryView: View {
    @State private var items: [OutboxItem] = []
    @State private var errorMessage: String?
    @State private var isLoading = false

    var body: some View {
        List {
            if isLoading && items.isEmpty {
                ProgressView()
            } else if let errorMessage {
                Text(errorMessage)
                    .foregroundStyle(.secondary)
            } else if items.isEmpty {
                VStack(spacing: 6) {
                    Label("Sin capturas", systemImage: "tray")
                    Text("Tus envíos aparecerán aquí")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            } else {
                ForEach(items) { item in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Label(routeTitle(item), systemImage: statusIcon(item.status))
                                .foregroundStyle(statusColor(item.status))
                            Spacer()
                            Text(statusTitle(item.status))
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }

                        Text(item.payload.capture.text)
                            .font(.footnote)
                            .lineLimit(2)

                        if let message = item.lastResponse?.displayMessage ?? item.lastResponse?.question {
                            Text(message)
                                .font(.footnote)
                        }

                        HStack {
                            Text("Intentos: \(item.attempts)")
                            if let path = item.lastDeliveryPath {
                                Text("· \(deliveryPathTitle(path))")
                            }
                            if let lastError = item.lastError {
                                Text("· \(lastError)")
                            }
                        }
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 2)
                }
            }
        }
        .navigationTitle("Historial")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { @MainActor in
                        await loadHistory()
                    }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
            }
        }
        .task {
            await loadHistory()
        }
    }

    @MainActor
    private func loadHistory() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let loaded = try await FileOutboxStore(fileURL: outboxURL).loadAll()
            items = loaded.sorted { $0.updatedAt > $1.updatedAt }
            errorMessage = nil
        } catch {
            errorMessage = "No se pudo leer el historial"
        }
    }

    private func routeTitle(_ item: OutboxItem) -> String {
        switch item.payload.route.domain {
        case .meganExpenseCapture:
            return "Gasto"
        case .auraReminderCapture:
            return "Recordatorio"
        case .auraGroceryCapture:
            return "Mercado"
        case .auraHomeAction:
            return "Casa"
        case .auraGeneralLifeCapture:
            return "Vida"
        case .argosGeneralCapture:
            return "Captura"
        case .pipoCodingTaskCapture:
            return "Pipo"
        case .ateneaResearchCapture:
            return "Atenea"
        case .horacioDesignBriefCapture:
            return "Horacio"
        }
    }

    private func statusTitle(_ status: OutboxStatus) -> String {
        switch status {
        case .pending:
            return "Pendiente"
        case .sending:
            return "Por verificar"
        case .sent:
            return "Respuesta recibida"
        case .failed:
            return "Por verificar"
        case .cancelled:
            return "Cancelado local"
        }
    }

    private func deliveryPathTitle(_ path: CaptureDeliveryPath) -> String {
        switch path {
        case .directHTTPS:
            return "Directo"
        case .iPhoneFallback:
            return "vía iPhone"
        }
    }

    private func statusIcon(_ status: OutboxStatus) -> String {
        switch status {
        case .pending:
            return "clock"
        case .sending:
            return "arrow.up.circle"
        case .sent:
            return "checkmark.circle.fill"
        case .failed:
            return "exclamationmark.triangle.fill"
        case .cancelled:
            return "xmark.circle"
        }
    }

    private func statusColor(_ status: OutboxStatus) -> Color {
        switch status {
        case .pending:
            return .orange
        case .sending:
            return .blue
        case .sent:
            return .green
        case .failed:
            return .red
        case .cancelled:
            return .secondary
        }
    }

    private var outboxURL: URL { PersonalWatchAppRuntime.outboxURL }
}

#Preview {
    NavigationStack {
        WatchHistoryView()
    }
}
