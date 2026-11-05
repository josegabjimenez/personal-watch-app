# Hermes Siri AI — Arquitectura Watch-first

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

## Decisión base

Una sola app Apple ecosystem, desarrollada Watch-first:

```text
HermesSiriAI.xcodeproj
├── SiriAI iOS App                 # configuración, pairing, historial/debug, fallback relay
├── SiriAI Watch App               # captura rápida principal
└── HermesCore Swift Package       # contratos, networking, seguridad, outbox, intents support
```

## Flujo principal MVP

```text
Apple Watch
  ├─ botón/dictado/App Intent
  ├─ persistir OutboxItem con request_id
  └─ URLSession directo HTTPS + HMAC
        ↓
POST /webhooks/mobile-capture-v1
        ↓
Hermes webhook narrow route
  ├─ verifica HMAC + schema + request_id
  ├─ ledger idempotente
  └─ route.domain / route.agent / route.intent
        ↓
Megan / Aura / Argos / Pipo / Atenea / Horacio
```

`WatchConnectivity` existe como fallback/config sync, no como dependencia obligatoria para capturar.

## Responsabilidades

### Watch App

- Captura rápida por botones y dictado/input del sistema.
- Genera `request_id` antes de enviar.
- Persiste cada captura en outbox local antes de network.
- Envío directo por `URLSession` cuando tenga conectividad.
- Feedback corto: `ok`, `accepted`, `queued`, `needs_confirmation`, `duplicate`, `error`.
- Mini-historial local 3–5 capturas, redacted/abreviado.
- Handoff a iPhone para configuración, errores de auth o confirmaciones complejas.

### iOS App

- Configuración de endpoint, ruta y secreto HMAC.
- Health/test connection.
- Keychain + rotación/revocación de secreto.
- Provisioning explícito al Watch vía WatchConnectivity.
- Historial/debug redacted.
- Personalización de quick actions.
- Fallback relay oportunista cuando Watch no puede enviar directo.
- Base para App Intents/Siri y Shortcuts nativos.

### HermesCore

Módulos sugeridos:

```text
HermesDomain          # Codable contracts, enums, fixtures
HermesNetworking      # WebhookClient, HTTPTransport, retry policy
HermesSecurity        # HMACSigner, SecureTokenStore protocols, redaction
HermesOutbox          # file-backed actor, atomic persistence, drainer
HermesConnectivity    # WatchConnectivity bootstrap/relay/sync
HermesIntentsSupport  # IntentSubmissionService, phrase helpers
HermesObservability   # os.Logger wrappers, local metrics, redacted logs
```

### Hermes backend/gateway

- Validar HMAC/auth y schema.
- Ledger idempotente por `request_id` + payload hash.
- Routing por `route.domain`, `route.agent`, `route.intent`.
- Domain logic real:
  - Megan/Firefly para finanzas.
  - Aura/Notion/Calendar/Home Assistant para vida diaria.
  - Argos/Pipo/Atenea/Horacio para capturas generales/trabajo/contenido.
- Respuestas cortas para Watch y detalles largos a superficies más cómodas.

## Modelos Swift sugeridos

```swift
public enum HermesAgent: String, Codable, CaseIterable {
    case megan, aura, pipo, atenea, horacio, argos
}

public enum HermesIntent: String, Codable, CaseIterable {
    case expense
    case reminder
    case grocery
    case homeAction = "home_action"
    case generalLifeCapture = "general_life_capture"
    case generalCapture = "general_capture"
    case agentStatus = "agent_status"
    case codingTask = "coding_task"
    case research
    case designBrief = "design_brief"
}

public enum HermesDomain: String, Codable, CaseIterable {
    case meganExpenseCapture = "megan.expense_capture"
    case auraReminderCapture = "aura.reminder_capture"
    case auraGroceryCapture = "aura.grocery_capture"
    case auraHomeAction = "aura.home_action"
    case auraGeneralLifeCapture = "aura.general_life_capture"
    case argosGeneralCapture = "argos.general_capture"
    case argosAgentStatus = "argos.agent_status"
    case pipoCodingTaskCapture = "pipo.coding_task_capture"
    case ateneaResearchCapture = "atenea.research_capture"
    case horacioDesignBriefCapture = "horacio.design_brief_capture"
}
```

## Networking

- Primary: `URLSession` direct HTTPS from Watch to `/webhooks/mobile-capture-v1`.
- Fallback: iPhone relay via `WatchConnectivity` with same `request_id`.
- HMAC signs exact JSON bytes.
- Timeout short enough for Watch UX; outbox handles retry/backoff.
- No broad Hermes API Server token on Watch for MVP.
- API Server may be revisited later for `AskHermes` with BFF/scoped mobile token.

## Seguridad

- No secrets hardcoded.
- HMAC secret in Keychain, per target/device.
- No secrets or full financial/personal transcripts in release logs.
- Debug/release environments separated.
- ATS/HTTPS required outside debug.
- `capture.text` is untrusted data even when request is authenticated.

## Estados UI

```text
idle → dictating/input → queued_local → sending → ok/accepted/needs_confirmation/duplicate/error
```

Microcopy examples:

- `Listo ✅`
- `En cola ⏳`
- `Ya estaba enviado ✅`
- `¿Para cuándo?`
- `Abre iPhone`
- `Sesión vencida`

## Estrategia de migración desde Shortcuts

1. Mantener Shortcuts actuales como fallback.
2. Crear `/webhooks/mobile-capture-v1` en paralelo.
3. Empezar en `dry_run` / no-write para capturas reales.
4. Validar HMAC, schema, idempotencia, parser y respuestas.
5. Habilitar write por dominios y etapas:
   - Aura Notes/Tasks/grocery seguros.
   - Megan gastos simples.
   - Aura Calendar/Home actions.
   - Megan FP/TC multi-write.
6. Mantener legacy al menos una semana estable.
7. Retirar o adaptar legacy solo con rollback probado.

## Testing mínimo

### Unit

- Payload encoding/snapshot.
- Response decoding.
- Agent/intent/domain mapping.
- HMAC known vectors.
- Error mapping.
- Outbox stable request_id.

### Integration

- Mock endpoint verifies HMAC.
- Hermes staging signed payload accepted.
- Invalid signature rejected.
- Same `request_id` returns duplicate/no duplicate side effect.
- Firefly/Notion planner dry-run before write.

### Device QA

- Apple Watch real.
- iPhone real.
- Watch with iPhone nearby.
- Watch Wi‑Fi/cellular without reachable iPhone.
- Poor network / mode airplane / wrist lowered mid-send.
- Spanish dictation phrases from Megan/Aura fixtures.
- Real finance write only after no-write gates pass.

## Riesgos iniciales

- watchOS/Siri capabilities vary by version/device.
- Token sync between iPhone/Watch requires explicit validation.
- Duplicates in finance/life systems if backend idempotency is incomplete.
- Home actions can be sensitive; confirm/handoff when ambiguous.
- Glassmorphism/dark aesthetic must not harm legibility on 41mm/45mm screens.
