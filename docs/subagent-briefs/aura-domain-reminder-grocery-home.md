# Aura domain brief — Recordatorios, mercado, hogar y captura de vida

> **Historical research brief.** Names, sample payloads and proposed actions below belong to the earlier planning stage. The current product is **Personal Watch App** with [three-action MVP scope](../PERSONAL_WATCH_APP_MVP.md); these proposals do not authorize additional actions or backend changes.

**Objetivo:** definir el contrato funcional para que la app nativa Watch-first envíe capturas de Aura a Hermes sin romper el flujo Apple Watch/Shortcuts existente que ya escribe en Notion Tasks/Notes, la lista oficial de mercado y Google Calendar.

**Alcance:** diseño/auditoría de dominio. Este documento **no ejecuta escrituras** en Notion, Calendar ni Home Assistant. La app Watch/iOS solo captura intención + contexto; Hermes/Aura conserva la lógica real de routing, escritura, calendario, idempotencia y resolución de ambigüedades.

---

## 1) Decisión ejecutiva

Aura debe exponer cuatro acciones declarativas en el contrato v1:

```text
aura.reminder_capture
aura.grocery_capture
aura.home_action
aura.general_life_capture
```

- `argos.general_capture` sigue siendo inbox universal entre agentes.
- `aura.general_life_capture` es más estrecho: vida diaria de Jose, casa, Laura/amorcito, puppy/pet-care, notas personales, errands y cosas para el Second Brain.
- El cliente Watch **no debe parsear Notion/Calendar** ni conocer IDs internos salvo hints no sensibles. Debe enviar texto, `request_id`, timezone y superficie.
- Toda respuesta al Watch debe ser corta y accionable; detalles largos van a iPhone/Discord/Telegram si aplica.

---

## 2) Payload contract compatible con `API_CONTRACT_V1`

En el contrato móvil canónico, las acciones dotted de Aura se transportan como `route.agent = "aura"` + `route.intent`:

| Acción lógica | `route.agent` | `route.intent` |
|---|---|---|
| `aura.reminder_capture` | `aura` | `reminder` |
| `aura.grocery_capture` | `aura` | `grocery` |
| `aura.home_action` | `aura` | `home_action` |
| `aura.general_life_capture` | `aura` | `general_life_capture` |

### Request mínimo

```json
{
  "event_type": "mobile_capture.v1",
  "schema": "com.jose.hermes.mobile_capture",
  "schema_version": 1,
  "request_id": "watch-uuid-v4",
  "created_at": "2026-07-06T14:03:00-05:00",
  "source": {
    "app": "HermesCapture",
    "app_version": "0.1.0",
    "platform": "watchOS",
    "os_version": "10.x",
    "device_id": "pseudonymous-local-uuid-or-hash",
    "locale": "es_CO",
    "timezone": "America/Bogota",
    "surface": "watch_app"
  },
  "route": {
    "agent": "aura",
    "intent": "reminder"
  },
  "capture": {
    "modality": "watch_dictation",
    "language": "es",
    "text": "recuérdame pagar la luz mañana a las 4 p.m.",
    "raw_text": "recuérdame pagar la luz mañana a las 4 p.m."
  },
  "entities": {
    "due_at": null,
    "calendar_at": null,
    "tags": []
  },
  "context": {
    "requires_confirmation": false,
    "shortcut_compatibility": false
  },
  "delivery": {
    "expect_response": true,
    "response_preference": "short"
  }
}
```

### Campos obligatorios de Aura

| Campo | Regla |
|---|---|
| `route.agent` | Debe ser `aura`. |
| `route.intent` | Una de `reminder`, `grocery`, `home_action`, `general_life_capture`. |
| `capture.text` | Dictado/texto original. Preservarlo en descripción/body para auditoría. |
| `source.timezone` | Para Jose debe ser `America/Bogota`; si falta o difiere, backend debe normalizar o pedir confirmación en acciones fechadas. |
| `request_id` | Obligatorio. Estable para retries del mismo evento; nuevo para una intención nueva. |
| `created_at` | Base para fechas relativas como `hoy`, `mañana`, `en dos minutos`. |

### Response mínimo

```json
{
  "status": "ok",
  "message": "Recordatorio creado para mañana 4:00 p.m. America/Bogota.",
  "display_message": "Mañana 4:00 p.m. ✅",
  "request_id": "watch-uuid-v4",
  "result_id": "notion-page-or-calendar-id",
  "needs_confirmation": false,
  "next_actions": []
}
```

### Status y microcopy Watch

| Status | Uso | `display_message` sugerido |
|---|---|---|
| `ok` | Acción completada y verificada. | `Listo ✅` / `Mañana 4:00 p.m. ✅` |
| `accepted` | Captura guardada, trabajo secundario pendiente. | `Guardado · te aviso` |
| `needs_confirmation` | Falta dato crítico o match ambiguo. | `¿Para cuándo?` / `¿Cuál tarea?` |
| `duplicate` | `request_id` ya procesado. | `Ya estaba enviado ✅` |
| `error` | Fallo recuperable. | `Falló · reintentar` |

Regla de muñeca: `display_message` debe caber en 1–2 líneas, idealmente <55 caracteres, y no debe decir “listo” si solo se encoló o falló Calendar.

---

## 3) Routing a Jose Second Brain / sistemas oficiales

### Destinos canónicos

| Tipo | Destino | Reglas |
|---|---|---|
| Recordatorio/tarea con fecha | Notion **Tasks** + Calendar best-effort | `Status = To do`, `Due date`, descripción con `Aura Capture`, texto original y metadata. |
| Tarea sin fecha / “cuando salga” | Notion **Tasks** sin due | No inventar fecha. Incluir contexto `recado`, `al salir`, `sin fecha`. |
| Nota/idea/reflexión | Notion **Notes** | `Status = Inbox`; `Type = Idea` o `Note`; cuerpo con dictado original. |
| Daily check-in/reflexión diaria | **Daily Journal** si el texto claramente es diario/mood | Si no es claro, usar Notes Inbox. |
| Mercado/faltantes | Página oficial **Primera lista de mercado / Lista de Compras** `304fcdfc-b7b4-804b-ae31-d1460114e6bd` | To-do unchecked = falta/comprar; checked = comprado/disponible. No borrar salvo solicitud explícita. |
| Errands | Tasks | Sin due duro salvo que el dictado lo diga. |
| Hogar/smart home | Home Assistant o Tasks según intención | Comandos directos solo si target/acción son claros y seguros; si no, crear tarea/needs_confirmation. |

### Regla anti-duplicados semántica

- `request_id` evita duplicar el mismo evento técnico.
- Adicionalmente, Aura debe buscar duplicados razonables antes de crear:
  - Tasks: título normalizado + estado activo + due date/time similar.
  - Mercado: ítem normalizado ya unchecked = ya falta; responder `ok/duplicate` sin agregar otra línea.
  - Calendar: si Task ya tiene Google Event ID, actualizarlo; no crear otro.

---

## 4) Reglas por acción

### 4.1 `aura.reminder_capture`

**Intenciones cubiertas:** crear recordatorio/tarea, reprogramar, marcar hecho, cancelar, listar/consultar.

#### Crear

Ejemplos:

- `recuérdame pagar la luz mañana a las cuatro p.m.`
- `en dos minutos revisar el arroz`
- `pasado mañana llamar a mamá`
- `anota comprar arena del perro cuando salga`

Reglas:

1. Parsear fechas relativas con base en `created_at` + `America/Bogota`.
2. Si hay fecha/hora explícita, confirmar **fecha y hora interpretada** en la respuesta: `Mañana 4:00 p.m. Bogotá ✅`.
3. Si hay fecha sin hora, crear con fecha de día completo o pedir hora solo si la frase dice “recordatorio” y el producto necesita notificación exacta. Responder `Sin hora · <fecha>` si se acepta como tarea dated all-day.
4. Si no hay fecha, crear Task sin `Due date` y responder `Tarea sin fecha ✅`, no inventar `hoy`.
5. Preservar el texto original en Description.
6. Calendar es secundario: un fallo OAuth no bloquea la Task.

#### Reprogramar

Ejemplos:

- `cambia pagar la luz para mañana a las 8`
- `mueve mi recordatorio de llamar a mamá al viernes`

Reglas:

1. Resolver match por título + estado activo + fecha si se menciona.
2. Si hay match único, actualizar Task `Due date` y patch del evento Calendar asociado.
3. Si no hay Calendar Event ID, crear evento best-effort si la nueva due date tiene hora/notificación.
4. Si el match es ambiguo, `needs_confirmation` con máximo 2 opciones cortas en Watch o handoff a iPhone.

#### Done / cancel / list

- Frases como `qué tareas tengo pendientes`, `lista mis recordatorios`, `marca como hecha la última tarea`, `cancela el recordatorio de pagar luz` **nunca deben crear una tarea literal**.
- Prioridad de intención:
  1. `list/query/status`
  2. `done/complete`
  3. `cancel/delete`
  4. `reschedule`
  5. `create`
- Excepción: si una frase contiene un due explícito claro y no empieza como cancel/done/list, tratarla como creación/reprogramación, no como status.
- `última` solo se resuelve si hay un último record activo inequívoco de Aura/Tasks; si no, pedir aclaración.
- Completion/cancel son idempotentes: repetir `marca X como hecho` sobre una Task ya Done responde `Ya estaba hecho ✅`.

### 4.2 `aura.grocery_capture`

**Fuente de verdad:** lista oficial de mercado en Notion, page ID `304fcdfc-b7b4-804b-ae31-d1460114e6bd`.

Intenciones:

| Intent | Ejemplos | Acción |
|---|---|---|
| Add missing | `agrega leche y huevos al mercado`, `falta detergente` | Añadir unchecked si no existe unchecked equivalente. |
| Mark bought/available | `marca leche como comprado`, `ya compré huevos` | Marcar checked el ítem activo. |
| List missing | `qué falta del mercado`, `muéstrame lista de compras` | Devolver resumen corto; detalle largo al iPhone/Discord. |
| Remove/delete | `borra leche de la lista` | Solo si verbo de borrado es explícito; preferir checked si dice comprado. |

Reglas:

1. Normalizar plurales, acentos y unidades (`leche deslactosada`, `2 litros de leche`).
2. No duplicar ítems unchecked ya existentes; responder `Leche ya estaba ✅`.
3. Si el dictado contiene varios ítems, procesar lote y responder corto: `Mercado: +3 ✅`.
4. Si un ítem tiene cantidad/unidad, conservarla en el texto del to-do.
5. No mezclar mercado con Tasks salvo errands explícitos (`comprar en D1 mañana`) que pueden crear Task + grocery items si el usuario lo pide.

### 4.3 `aura.home_action`

**Intenciones cubiertas:** smart home, tareas del hogar, rutinas, errands y consultas de estado.

Clasificación interna recomendada:

| Subtipo | Ejemplos | Regla |
|---|---|---|
| `smart_home_command` | `apaga las luces de la sala`, `enciende ventilador` | Ejecutar vía Home Assistant solo si entidad/área/acción son claras y seguras. |
| `home_task` | `recuérdame limpiar cocina el sábado`, `anota cambiar filtro` | Route a Tasks con área hogar. |
| `home_status_query` | `qué luces están prendidas`, `estado de la casa` | Consultar Home Assistant; no escribir. |
| `sensitive_home_action` | cerraduras, puertas, seguridad, alarmas, electrodomésticos riesgosos | Requiere confirmación explícita o handoff a iPhone. |

Reglas:

1. Watch puede ejecutar acciones reversibles de bajo riesgo con confirmación ligera si la intención es clara.
2. Acciones sensibles no se ejecutan solo por dictado ambiguo: `needs_confirmation` o `Confirma en iPhone`.
3. Si Home Assistant está no disponible, fail-soft:
   - comando directo: `Casa no disponible` + retry;
   - tarea del hogar: crear Task si la intención era tarea, no comando.
4. No prometer notificaciones a Laura/amorcito ni otros canales sin delivery configurado y verificado.

### 4.4 `aura.general_life_capture`

**Uso:** captura rápida de vida diaria que no es claramente gasto/código/research/diseño ni una pregunta universal a Argos.

Ejemplos:

- `idea para cita con Laura: picnic nocturno con sushi`
- `anota que el perrito comió a las 7 y vomitó un poco`
- `recordar revisar vacunas del perro este mes`
- `pensamiento: estoy muy cansado con el trabajo esta semana`

Routing:

1. Dated/actionable → Tasks.
2. Nota/idea/reflexión → Notes Inbox.
3. Diario/mood/check-in claro → Daily Journal.
4. Pet-care symptom urgente/persistente → Note/Task + respuesta orientativa que recomiende veterinario si aplica; no diagnóstico.
5. Relationship details sensibles → Notes only if Jose explícitamente lo captura; no guardar como memoria estable salvo instrucción explícita.

Reglas:

- Si confianza de routing < umbral, preferir Notes Inbox con `Type = Note` y respuesta `Captura guardada ✅`; no ejecutar acciones externas.
- No debe reemplazar `argos.general_capture`: si el input menciona otro agente o dominio (`gasto`, `repo`, `investiga`, `diseño`), devolver route suggestion o dejar a Argos enrutar.
- Esta acción debe pasar el gate Fable 5 antes de habilitar writes amplios automáticos, porque puede capturar datos personales sensibles.

---

## 5) Google Calendar lifecycle / fail-soft

### Crear recordatorio fechado

1. Crear/actualizar Notion Task primero.
2. Si hay `Due date` con hora o necesidad de notificación, crear evento Google Calendar best-effort.
3. Guardar Google Event ID en la Task/metadata si el schema lo permite; si no, en estado auxiliar idempotente ligado a `request_id` + Notion page ID.
4. Responder:
   - Todo OK: `Mañana 4:00 p.m. ✅`
   - Notion OK, Calendar falló: `Task guardada · Calendar pendiente`.

### Reprogramar

- Patch del evento Calendar existente si hay Event ID.
- Si el evento fue borrado o falta ID, crear uno nuevo best-effort y actualizar metadata.
- Si patch falla por OAuth (`invalid_grant`, revoked), mantener la Task con nueva fecha y advertir corto.

### Done/cancel

- Done: marcar Task `Done`, borrar/silenciar evento Calendar asociado y limpiar/ignorar Event ID para que no notifique después.
- Cancel/delete: cancelar Task según política actual y borrar evento Calendar asociado.
- Si Calendar deletion falla, no revertir el estado Notion; responder `Hecho · revisa Calendar` o enviar detalle por canal largo.

### OAuth fail-soft

Calendar nunca debe ser el single point of failure de Aura:

- Captura principal en Notion/lista oficial debe continuar.
- No imprimir tokens ni detalles sensibles.
- Exponer error accionable: `Calendar necesita reauth`.
- Tener retry/reconcile posterior para eventos Calendar pendientes.

---

## 6) Idempotencia end-to-end

Reglas obligatorias:

1. El Watch genera `request_id` al persistir la captura local, antes del primer POST.
2. Retries por timeout/offline usan el **mismo** `request_id`.
3. Hermes guarda `request_id -> resultado compacto` por una ventana razonable.
4. Si llega el mismo `request_id`, devolver `status=duplicate` o el resultado anterior, sin repetir Notion/Calendar/Home Assistant.
5. Para side effects secundarios, propagar la llave:
   - Task: Description/metadata `Aura request_id=<id>` si posible.
   - Calendar: `extendedProperties.private.request_id` si se usa API Calendar.
   - Grocery: estado local `request_id` y dedupe semántico de ítem.
6. Si Jose dicta exactamente lo mismo con un `request_id` nuevo, tratar como nueva intención salvo reglas de dominio (ej. grocery unchecked ya existente no duplica).

Respuesta duplicate estándar:

```json
{
  "status": "duplicate",
  "message": "Ya había recibido esta captura; no la dupliqué.",
  "display_message": "Ya estaba enviado ✅",
  "request_id": "watch-uuid-v4"
}
```

---

## 7) Confirmación explícita de fecha/hora America/Bogota

Para cualquier captura con fecha/hora interpretada, Aura debe devolver un campo humano explícito:

```json
{
  "interpreted_due": {
    "start": "2026-07-07T16:00:00-05:00",
    "timezone": "America/Bogota",
    "display": "martes 7 jul, 4:00 p.m."
  },
  "display_message": "Mar 7 · 4:00 p.m. ✅"
}
```

Reglas:

- `mañana`, `hoy`, `pasado mañana`, `en media hora`, `en dos minutos`, `el viernes` se calculan desde `created_at` en Bogotá.
- Normalizar dictado español: `p.m.`, `pm`, `P M`, `de la tarde`, `dos P.M.`.
- Evitar doble parse de horas (`8:30 pm` no debe generar `08:00` y `20:30`).
- Si la fecha es ambigua (`el 5` sin mes, `a las 8` sin am/pm cuando importa), usar `needs_confirmation`.
- Si se crea Task all-day, la respuesta debe decir `Sin hora` para evitar falsa precisión.

---

## 8) Testing de frases españolas de dictado

Congelar `now = 2026-07-06T14:00:00-05:00` (`America/Bogota`) para pruebas determinísticas.

### Reminder/date parser

| Frase | Acción esperada |
|---|---|
| `recuérdame llamar a mamá mañana a las dos P.M.` | Crear Task due `2026-07-07T14:00:00-05:00`, respuesta con `mañana 2:00 p.m.` |
| `hoy a las cuatro P.M. pagar la luz` | Crear due `2026-07-06T16:00:00-05:00` si futuro; si ya pasó, pedir confirmación. |
| `el 5 de julio a las 8:30 pm llamar a mamá` | Parse natural date + `20:30`, sin fallback a hoy. |
| `en dos minutos revisar el arroz` | Crear due `now + 2m`; confirmar hora exacta Bogotá. |
| `en media hora sacar la ropa` | Crear due `now + 30m`. |
| `pasado mañana comprar medicina` | Crear dated Task; hora solo si política acepta all-day o pedir hora. |
| `03/07/2026 a las 11:00 control médico` | Parse slash date según `es_CO`/regla acordada; confirmar fecha para evitar MM/DD. |
| `mañana 11am, 2pm y 6pm tomar medicina` | Crear múltiples reminders solo si el flujo soporta lote; si no `needs_confirmation`. |

### Done/cancel/list

| Frase | Resultado esperado |
|---|---|
| `qué tareas tengo pendientes para hoy` | List/query; cero creación. |
| `lista mis recordatorios hechos` | Query Done; cero creación. |
| `marca como hecha la última tarea que te envié` | Done solo si “última” es inequívoca; si no `needs_confirmation`. |
| `cancela el recordatorio de pagar la luz mañana` | Cancel match title + due; delete/silence Calendar. |
| `mueve llamar a mamá para el viernes a las 9 am` | Reschedule único o `needs_confirmation`. |

### Grocery

| Frase | Resultado esperado |
|---|---|
| `agrega leche huevos y pan al mercado` | Añadir 3 unchecked; respuesta `Mercado: +3 ✅`. |
| `falta detergente y arroz` | Añadir/confirmar faltantes. |
| `ya compré leche` | Marcar checked item `leche`; no borrar. |
| `qué falta del mercado` | Listar faltantes; no crear. |
| `borra pan de la lista de mercado` | Borrar solo por verbo explícito `borra`. |

### Home

| Frase | Resultado esperado |
|---|---|
| `apaga las luces de la sala` | Home Assistant command si entidad clara; respuesta `Luces sala off ✅`. |
| `enciende el aire del cuarto a 22` | Ejecutar si entidad/temperatura válida; si ambiguo, confirmar. |
| `abre la puerta` | Acción sensible: `needs_confirmation`/iPhone. |
| `recuérdame limpiar la cocina el sábado` | Task hogar, no comando Home Assistant. |
| `qué luces están prendidas` | Query estado; no write. |

### General life capture

| Frase | Resultado esperado |
|---|---|
| `idea para cita con Laura: picnic nocturno con sushi` | Notes Inbox, Type Idea. |
| `anota que el perro comió a las siete` | Notes o Daily Journal según contexto; preservar original. |
| `recordar vacunas del perro este mes` | Task sin fecha exacta o `needs_confirmation` si requiere día. |
| `me siento drenado por el trabajo esta semana` | Notes/Daily Journal; no memoria estable automática. |
| `investiga mejores collares para perro` | Si está en Aura puede ser Note/Task; si dominio research, sugerir Atenea/Argos según routing global. |

---

## 9) Fable 5 gate para `aura.general_life_capture`

Antes de habilitar writes automáticos amplios desde Watch, revisar esta acción con Fable 5:

1. **Producto / velocidad de captura**
   - Pasa si Jose puede capturar vida diaria en 1 tap + dictado + feedback claro.
   - No pasa si la app pregunta demasiadas categorías en el Watch.
2. **Plataforma Apple nativa**
   - Pasa si funciona con dictado del sistema, outbox local e idempotencia al bajar la muñeca.
   - No pasa si depende de background execution inmediato o de iPhone siempre presente.
3. **Seguridad / privacidad**
   - Pasa si texto sensible se minimiza en logs, token está en Keychain y no hay memoria estable automática.
   - No pasa si relationship/pet/health notes quedan en logs de release o se comparten a canales no verificados.
4. **Confiabilidad / idempotencia**
   - Pasa si retries no duplican Notes/Tasks/Calendar y las capturas ambiguas van a Inbox/confirmation.
   - No pasa si una frase vaga puede ejecutar Home Assistant, Calendar o notificaciones externas sin confirmación.
5. **Mantenibilidad / evolución**
   - Pasa si `general_life_capture` usa las mismas rutas backend que Aura Capture y tests de parser/routing.
   - No pasa si Swift empieza a codificar reglas de Notion/Calendar o IDs internos.

**Veredicto recomendado:** `APPROVED WITH CONDITIONS` para MVP si se limita a Notes/Tasks/official grocery + Home Assistant seguro, con Calendar fail-soft, request_id obligatorio, y acciones sensibles confirmadas en iPhone.

---

## 10) Recomendaciones para tickets Pipo/Argos

1. Mantener `API_CONTRACT_V1.md` y `HermesAction` sincronizados con `aura.general_life_capture` / `general_life_capture` en cualquier cambio de schema.
2. Agregar fixtures JSON por cada acción Aura y snapshots de response corta.
3. Crear matriz de tests backend para parser español, list/done/cancel y Calendar fail-soft.
4. Implementar idempotency store antes de habilitar Notion/Calendar writes desde endpoint unificado.
5. Mantener compatibilidad con Shortcuts Aura actuales durante al menos una semana estable antes de retirar webhooks legacy.
