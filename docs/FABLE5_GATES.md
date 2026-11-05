# Historical Fable 5 Review Gates — Hermes Siri AI

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

> **Superseded for new reviews on 2026-07-17.** Jose requested that new reviews use the current GPT-5.6-sol model with Max Thinking instead of Fable 5. This document and prior Fable reviews remain only as historical decision records. Domain safety conditions recorded here are still requirements unless explicitly replaced.

This project previously used “Fable 5” as a protocol for important decisions/reviews/audits.

## Los 5 lentes

### 1. Producto y velocidad de captura

Preguntas:

- ¿Reduce taps/tiempo respecto a Shortcuts?
- ¿Se puede usar caminando o con una mano ocupada?
- ¿El feedback es inmediato y claro?
- ¿El flujo principal cabe en la pantalla del Watch sin fricción?

No pasa el gate si:

- Requiere demasiada navegación.
- El Watch se siente como una app de iPhone comprimida.
- El usuario no sabe si Hermes recibió la captura.

---

### 2. Plataforma Apple nativa

Preguntas:

- ¿Respeta las capacidades reales de watchOS/iOS?
- ¿Usa SwiftUI/App Intents/URLSession/WatchConnectivity de forma apropiada?
- ¿Está probado en dispositivo real, no solo previews?
- ¿Evita depender de background execution no garantizado?

No pasa el gate si:

- Asume capacidades no verificadas en Watch.
- Mezcla demasiado estado crítico entre Watch/iPhone.
- Depende de APIs frágiles sin fallback.

---

### 3. Seguridad y privacidad

Preguntas:

- ¿El token está fuera del código fuente?
- ¿Se guarda en Keychain o mecanismo equivalente?
- ¿No se loguean gastos, tokens ni datos sensibles innecesarios?
- ¿Hay HTTPS/Tailscale y headers seguros?

No pasa el gate si:

- El secreto queda hardcoded.
- Los logs exponen información financiera privada.
- No hay separación entre debug y producción.

---

### 4. Confiabilidad e idempotencia

Preguntas:

- ¿Hay `request_id` por captura?
- ¿Los retries no duplican gastos ni recordatorios?
- ¿Timeouts y errores tienen mensajes accionables?
- ¿Hermes puede devolver `accepted` para tareas largas?

No pasa el gate si:

- Un doble tap o retry crea doble gasto.
- El Watch dice “listo” cuando solo se aceptó una tarea larga.
- Fallas de Calendar/Firefly rompen todo sin fallback claro.

---

### 5. Mantenibilidad y evolución de agentes

Preguntas:

- ¿Agregar un agente nuevo requiere solo agregar una action/config?
- ¿El contrato API está versionado?
- ¿La lógica compleja vive en Hermes, no duplicada en Swift?
- ¿Hay tests de payload y documentación suficiente?

No pasa el gate si:

- Cada acción crea código duplicado.
- El frontend conoce detalles internos de Firefly/Notion.
- No hay forma clara de migrar desde Shortcuts.

## Cuándo aplicar Fable 5

Obligatorio para:

- Endpoint unificado vs webhooks separados.
- Almacenamiento de token.
- URLSession directo desde Watch vs bridge iPhone.
- App Intents/Siri strategy.
- Push notifications/complications.
- Cualquier acción financiera write-enabled.
- Cualquier cambio al parser de fechas/recordatorios.

## Plantilla de review

```md
# Fable 5 Review — <decisión>

## Decisión

## Alternativas consideradas

## Evaluación

1. Producto/captura:
2. Plataforma Apple:
3. Seguridad/privacidad:
4. Confiabilidad/idempotencia:
5. Mantenibilidad/evolución:

## Riesgos

## Verificación requerida

## Veredicto

- APPROVED / APPROVED WITH CONDITIONS / REJECTED
```
