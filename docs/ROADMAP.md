# Hermes Siri AI — Roadmap iOS + watchOS

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

> Objetivo: construir una app nativa para Apple Watch e iPhone que convierta a Hermes Agents en una interfaz de captura ultrarrápida, priorizando Apple Watch como superficie principal y usando iPhone como configuración, historial y soporte de Siri/App Intents.

## Principio de producto

- **Watch-first, no Watch-only.** La captura principal vive en Apple Watch; iPhone acompaña con configuración, historial, debug y gestión de acciones.
- **Un solo ecosistema/app.** Un proyecto Xcode con target iOS, target watchOS y core compartido.
- **Shortcuts existentes siguen vivos.** La app nativa debe migrar gradualmente desde los webhooks actuales sin romper Megan/Firefly ni Aura/Notion/Calendar.
- **Hermes decide y enruta.** La app no debe tener reglas financieras o de Notion complejas; envía un payload limpio y Hermes/Argos enruta a Megan, Aura, Pipo, Atenea, Horacio o Argos.

## Fase 0 — Preparación y decisiones base

### Entregables

- Crear repo/proyecto base: `HermesSiriAI`.
- Xcode project con:
  - iOS App target.
  - watchOS App target.
  - módulo compartido `HermesCore` o Swift Package local.
- Definir Bundle IDs, signing y grupo de capabilities.
- Decidir endpoint v1: idealmente `POST /webhooks/apple-watch` o endpoint API equivalente.
- Mapear Shortcuts existentes:
  - gasto → Megan/Firefly.
  - recordatorio/captura → Aura/Notion/Calendar.

### Review GPT-5.6-sol Max Thinking requerido

- ¿Un endpoint unificado v1 reduce complejidad sin romper webhooks actuales?
- ¿Qué secreto/token se usará y dónde se almacena?
- ¿El Watch enviará directo por HTTPS o pasará por iPhone cuando convenga?

### Criterio de aceptación

- Proyecto compila vacío en iPhone + Watch.
- Hay un documento de contrato API v1 acordado.
- No se expone ningún secreto en código fuente.

---

## Fase 1 — Core compartido + networking seguro

### Entregables

Módulo `HermesCore`:

- `HermesAPIClient`
- `HermesRequestPayload`
- `HermesResponsePayload`
- `HermesAgent`
- `HermesAction`
- `QuickAction`
- `HermesConfigStore`
- `RequestIdProvider`
- `HermesError`

### Features

- POST JSON a Hermes.
- Header de autenticación configurable.
- `request_id` por evento para idempotencia.
- Timeout corto y errores humanos.
- Soporte de ambientes:
  - local/Tailscale,
  - producción HTTPS,
  - debug.

### Review GPT-5.6-sol Max Thinking requerido

- Seguridad de token/API key.
- Idempotencia y manejo de retries.
- Modelo de errores para no duplicar gastos o recordatorios.

### Criterio de aceptación

- Unit tests de encoding/decoding de payload.
- Test manual desde iOS simulator o preview con endpoint de prueba.
- Respuestas `ok`, `accepted`, `needs_confirmation`, `error` parseadas correctamente.

---

## Fase 2 — Watch MVP: captura ultrarrápida

### Objetivo

Reemplazar los flujos más usados de Shortcuts con una app nativa Watch-first.

### Pantalla inicial

Acciones sugeridas:

1. 💸 Gasto — Megan.
2. ⏰ Recordatorio — Aura.
3. 🛒 Mercado — Aura/lista oficial.
4. 🧠 Captura — Argos enruta.
5. 🏠 Casa — Aura/Home Assistant.
6. 📡 Estado — Argos.

### Flujos MVP

#### Gasto

- Tap en `Gasto`.
- Dictado.
- Enviar `agent=megan`, `action=expense_capture`.
- Confirmación corta: `Enviado a Megan` / `Gasto registrado` / `Necesito confirmar cuenta`.

#### Recordatorio

- Tap en `Recordatorio`.
- Dictado.
- Enviar `agent=aura`, `action=reminder_capture`.
- Confirmación con fecha interpretada cuando Hermes la devuelva.

#### Captura general

- Tap en `Captura`.
- Dictado libre.
- Enviar `agent=argos`, `action=general_capture`.
- Hermes enruta.

### UX constraints

- Máximo 1–2 taps antes de dictar.
- Textos de respuesta de 1–2 líneas.
- Botón de reintentar.
- Historial local de últimas 5 capturas.
- Haptic feedback para enviado/error.

### Review GPT-5.6-sol Max Thinking requerido

- ¿El flujo más usado se completa en menos de 5 segundos después de abrir la app?
- ¿La confirmación reduce el riesgo de errores financieros o de fechas?
- ¿La pantalla inicial no está sobrecargada para el reloj?

### Criterio de aceptación

- En dispositivo real, enviar payload de gasto y recordatorio al endpoint de test.
- Ver respuesta corta en Watch.
- No se duplican eventos al reintentar.

---

## Fase 3 — iPhone companion mínimo

### Objetivo

No competir con el Watch; servir como panel de configuración y debug.

### Features

- Configurar endpoint base.
- Configurar token/secret.
- Test connection.
- Ver historial de últimas capturas.
- Personalizar quick actions del Watch.
- Cambiar orden de acciones.
- Mostrar logs/respuestas detalladas.

### Review GPT-5.6-sol Max Thinking requerido

- ¿Qué debe quedarse simple en iPhone para no retrasar el MVP Watch?
- ¿Qué configuración es obligatoria para que el Watch sea confiable?

### Criterio de aceptación

- Jose puede cambiar endpoint/token sin tocar Xcode.
- Watch lee la configuración necesaria o usa configuración sincronizada.

---

## Fase 4 — Siri/App Intents nativo

### Objetivo

Llevar los flujos a Siri sin depender solamente de Shortcuts manuales.

### Intents candidatos

- `CaptureWithHermesIntent`.
- `RegisterExpenseIntent`.
- `CreateReminderIntent`.
- `AddGroceryItemIntent`.
- `AskAgentIntent`.

### App Shortcuts sugeridos

- “Capturar con Hermes”.
- “Registrar gasto con Megan”.
- “Recordatorio con Aura”.
- “Agregar al mercado”.
- “Preguntar a Argos”.

### Review GPT-5.6-sol Max Thinking requerido

- Validar disponibilidad real de App Intents/Siri en Watch en Xcode/dispositivo.
- Definir frases simples y robustas en español.
- Evitar intents que requieran demasiada conversación en Siri si el Watch UI es más rápido.

### Criterio de aceptación

- Siri ejecuta al menos 2 acciones desde Apple Watch.
- Intents usan el mismo `HermesAPIClient`.
- Errores son entendibles por voz y pantalla.

---

## Fase 5 — Notificaciones, complications y estado

### Features futuras

- Push/local notifications para resultados largos.
- Complication/Smart Stack con acción rápida principal.
- Estado de agentes: última captura, pendientes, errores.
- Cola offline si no hay conexión.

### Review GPT-5.6-sol Max Thinking requerido

- No prometer notificaciones a Laura/amorcito u otros canales sin delivery verificado.
- Separar `accepted` de `completed` para tareas largas.

---

## Fase 6 — Hardening, TestFlight y release personal

### Checklist

- Device QA en Apple Watch real.
- Revisión de secretos.
- Logs sin PII sensible.
- Manejo de HTTP errors, timeouts, duplicados.
- TestFlight interno.
- Documentación de setup.
- Playbook de recuperación si endpoint cambia.

### Criterio de aceptación final

- Jose puede usar el Watch durante una semana para gastos y recordatorios sin volver a Shortcuts salvo fallback.
- Firefly/Notion/Calendar no reciben duplicados.
- La app permite configurar y probar endpoint/token desde iPhone.
