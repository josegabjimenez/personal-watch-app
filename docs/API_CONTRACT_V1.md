# Personal Watch App — API Contract v1

The product is **Personal Watch App**. Existing schema names, `HermesCapture` client values and `X-Hermes-*` headers below are retained wire-compatibility identifiers for installed clients and the current backend; they are not the app's visible name.

The current Reminder implementation is described in [AI/Todoist reminder configuration](REMINDER_AI_TODOIST.md): it writes directly to Todoist Inbox, retains the original request/date identity, and reports unverified alerts. Historical generic routes below do not enable a Reminder helper, Notion or Calendar fallback.

This document records the compatible wire format. The [current three-action MVP](PERSONAL_WATCH_APP_MVP.md) defines the allowed app actions; older contract examples do not enable general capture or additional write domains.

## Canonical endpoint

```http
POST /webhooks/mobile-capture-v1
Content-Type: application/json
X-Webhook-Timestamp: <unix_seconds>
X-Webhook-Signature-V2: <hex_hmac_sha256("<timestamp>.<raw_body>", route_secret)>
X-Request-ID: <uuid_v4>
X-Hermes-Payload-Version: 1
X-Hermes-Client: HermesCapture/<surface>/<app_version>
X-Hermes-Device-ID: <pseudonymous_device_id>
```

## Legacy compatibility

Existing Shortcuts/webhooks stay operational during migration:

- `/webhooks/apple-watch-expense` → Megan/Firefly expense flow.
- Existing Aura capture route → Notion/Tasks/Calendar/grocery flow.

`X-Gitlab-Token` can remain as a compatibility auth header for current Shortcuts. The native iOS/watchOS app should use HMAC for v1.

## Auth rules

1. HMAC V2 signs `"<X-Webhook-Timestamp>.<exact raw JSON bytes>"`.
2. Do not reserialize after signing.
3. Secret lives in Keychain, never in source, UserDefaults, logs, screenshots, or chat.
4. `X-Request-ID` is required and must match `request_id` in the JSON.
5. TLS/HTTPS is required outside local debug. ATS exceptions are Debug-only.
6. Replay defense should combine `request_id`, payload hash, timestamp sanity, and the server idempotency ledger.

## Request schema

```json
{
  "event_type": "mobile_capture.v1",
  "schema": "com.jose.hermes.mobile_capture",
  "schema_version": 1,
  "request_id": "8F82D8A7-6A0A-48F7-9B57-2C52E9385D7E",
  "created_at": "2026-07-06T15:20:30Z",
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
    "agent": "megan",
    "intent": "expense",
    "domain": "megan.expense_capture"
  },
  "capture": {
    "modality": "watch_dictation",
    "language": "es",
    "text": "35 mil en D1 con Nu",
    "raw_text": "35 mil en D1 con Nu"
  },
  "entities": {
    "amount": null,
    "currency": "COP",
    "merchant": null,
    "concept": null,
    "account_hint": null,
    "card_hint": null,
    "date_hint": null,
    "due_at": null,
    "calendar_at": null,
    "tags": []
  },
  "context": {
    "user_confirmation": false,
    "watch_reachable_to_phone": true,
    "shortcut_compatibility": false,
    "requires_confirmation": false,
    "dry_run": false,
    "allow_write": true
  },
  "delivery": {
    "expect_response": true,
    "response_preference": "short"
  },
  "client_state": {
    "outbox_attempt": 0,
    "client_sent_at": "2026-07-06T15:20:31Z"
  }
}
```

## Required fields

| Field | Required | Notes |
|---|---:|---|
| `event_type` | yes | Must be `mobile_capture.v1`. |
| `schema_version` | yes | Integer `1` for this contract. |
| `request_id` | yes | End-to-end idempotency key. |
| `created_at` | yes | ISO timestamp; basis for relative date parsing. |
| `source.platform` | yes | `watchOS`, `iOS`, `shortcut`, etc. |
| `source.timezone` | yes | For Jose default to `America/Bogota`. |
| `source.surface` | yes | `watch_app`, `ios_app`, `app_intent`, `shortcut`. |
| `route.agent` | yes | Suggested agent. Server remains authority. |
| `route.intent` | yes | Suggested domain intent. |
| `route.domain` | recommended | Canonical dotted domain when the client knows it. Server can infer if absent. |
| `capture.text` | yes | Main text to process; untrusted data. |

## Canonical domains and route mapping

| Domain | `route.agent` | Canonical `route.intent` | Aliases accepted | Purpose |
|---|---|---|---|---|
| `megan.expense_capture` | `megan` | `expense` | `expense_capture` | Expense / Firefly capture. |
| `aura.reminder_capture` | `aura` | `reminder` | `reminder_capture` | Reminder/task capture. |
| `aura.grocery_capture` | `aura` | `grocery` | `grocery_capture` | Official grocery list capture. |
| `aura.home_action` | `aura` | `home_action` | — | Home Assistant/life action. |
| `aura.general_life_capture` | `aura` | `general_life_capture` | `life_capture` | Aura-scoped home/relationship/pet/Second Brain capture. |
| `argos.general_capture` | `argos` | `general_capture` | `capture` | Universal inbox/routing. |
| `argos.agent_status` | `argos` | `agent_status` | `status` | Agents/system status. |
| `pipo.coding_task_capture` | `pipo` | `coding_task` | `coding_task_capture` | Coding task capture. |
| `atenea.research_capture` | `atenea` | `research` | `research_capture` | Research brief capture. |
| `horacio.design_brief_capture` | `horacio` | `design_brief` | `design_capture` | Design/creative brief capture. |

Routing rules:

- `route.agent` and `route.intent` are suggestions; Hermes/Argos validates and may re-route.
- `route.domain` is the canonical backend domain when present.
- If `capture.text` names a different agent/domain clearly, backend can return `needs_confirmation` or route through Argos, depending on safety.

## Response schema

```json
{
  "status": "ok",
  "message": "Listo, enviado a Megan.",
  "display_message": "Gasto enviado ✅",
  "request_id": "8F82D8A7-6A0A-48F7-9B57-2C52E9385D7E",
  "result_id": "optional-firefly-notion-calendar-run-id",
  "needs_confirmation": false,
  "next_actions": [],
  "duplicate": false,
  "interpreted_due": null
}
```

## Response statuses

| Status | Meaning | Watch behavior |
|---|---|---|
| `ok` | Completed and verified. | Success haptic + auto-close. |
| `accepted` | Received; long work continues. | “Recibido” + optional notification later. |
| `queued` | Stored server/client-side for later. | “En cola”. |
| `needs_confirmation` | Missing/ambiguous critical field. | Ask one short question or open iPhone. |
| `duplicate` | Same request already processed. | “Ya estaba enviado”. |
| `rejected` | Unsafe/invalid; do not retry blindly. | Short explanation + open iPhone if needed. |
| `error` | Recoverable failure. | Show short error + retry/queue. |
| `partial_error` | Multi-step operation partially completed; server must reconcile. | Do not blind-retry; handoff to iPhone/Megan/Aura. |

Duplicate response example:

```json
{
  "status": "duplicate",
  "message": "Ya había recibido esta captura; no la dupliqué.",
  "display_message": "Ya estaba enviado ✅",
  "request_id": "8F82D8A7-6A0A-48F7-9B57-2C52E9385D7E",
  "duplicate": true
}
```

Aura interpreted due response example:

```json
{
  "status": "ok",
  "display_message": "Mar 7 · 4:00 p.m. ✅",
  "interpreted_due": {
    "start": "2026-07-07T16:00:00-05:00",
    "timezone": "America/Bogota",
    "display": "martes 7 jul, 4:00 p.m."
  }
}
```

## Idempotency requirements

- Client generates one `request_id` per capture before network send.
- Retries reuse the same `request_id`.
- Server stores compact result by `request_id` before/around side effects.
- Firefly/Notion/Calendar/Home Assistant actions must not repeat for duplicate request IDs.
- If same `request_id` arrives with a different payload hash, return a conflict/rejected response and do not write.
- Multi-write operations, especially FP/TC card purchases, require a ledger that can resume safely instead of duplicating the first write.

## Domain safety rules

1. Treat `capture.text` as untrusted data, never system instruction.
2. Keep mobile webhook route narrow and scoped.
3. Do not store full financial/personal transcripts in release logs by default.
4. Megan must query Firefly live, reuse existing category/tag/budget patterns, and ask when confidence is low.
5. Megan auto-write requires clear amount, account/card, concept, existing category, and existing budget.
6. Aura must preserve `America/Bogota`, confirm interpreted due date/time, and fail soft if Calendar sync is unavailable.
7. Aura list/done/cancel/reschedule commands must never create literal tasks by accident.
8. Home actions that are sensitive or ambiguous must require confirmation/handoff.
9. Results too long for Watch should be delivered to Discord/Telegram/iPhone later, not forced onto the watch screen.
