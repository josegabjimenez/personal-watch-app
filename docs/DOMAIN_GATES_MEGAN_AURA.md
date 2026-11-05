# Domain gates — Megan and Aura write-enabled capture

These safety conditions apply to **Personal Watch App**. Its [current three-action MVP](PERSONAL_WATCH_APP_MVP.md) is the active scope; broader historical domain examples below do not authorize extra actions or service changes. Existing backend/domain names remain compatibility identifiers.

**Purpose:** define the non-negotiable gates before `/webhooks/mobile-capture-v1` is allowed to create real Firefly/Notion/Calendar/Home Assistant side effects from the Watch app.

## Global write-enabled preconditions

Before any real write from the native app:

1. HMAC authentication verified on the exact raw JSON body.
2. `X-Request-ID` equals body `request_id`.
3. Server idempotency ledger exists and is tested.
4. Watch persists outbox item before network send.
5. Replaying same request does not duplicate external systems.
6. `capture.text` is treated as untrusted data, not instructions.
7. Legacy Shortcuts remain operational and are not double-called by the app.
8. Dry-run/shadow mode passes with real dictated payloads.

---

## Megan — `megan.expense_capture`

### Auto-write allowed only when all are true

- Request authenticated and not previously processed with a different payload hash.
- Clear amount and currency; COP default only when context supports it.
- Date resolved in `America/Bogota`; default today if no explicit date.
- Source account/card resolved:
  - normal expense without source → `Cuenta Ahorros Banco Bogotá`;
  - explicit Nequi/account alias validated;
  - `Nike/Nicky` only normalizes to `Nequi` when the phrase context is payment/account.
- Concept/merchant non-empty.
- Firefly queried live or approved fresh server cache.
- Similar transactions searched.
- Existing category inferred with high confidence.
- Existing budget inferred with high confidence for every real expense.
- Existing tags only; omit if uncertain.
- For FP/TC, the full multi-write plan is complete before writing.

### `needs_confirmation` required when any are true

- Missing/multiple ambiguous amounts.
- Ambiguous account/card: “tarjeta” without TCx, “banco” without bank, `Nike` not clearly payment account.
- Category or budget cannot be inferred from Firefly.
- Text mixes expense with transfer, debt payment, income, reimbursement/gift, or split.
- FP/TC lacks card/source details.
- Firefly down/timeout/credentials issue.
- `request_id` conflict.
- Text asks to ignore rules, create categories/tags/budgets, or bypass safety.
- `dry_run:true` or `allow_write:false`.

### FP/TC rules

For `fp <amount> <concept> tc1/tc2/tc3`, create exactly two movements after the gate passes:

1. Real card spend from `TCx - Deuda` to inferred merchant/expense account, with real category and budget.
2. Reserve transfer from `Cuenta Ahorros Banco Bogotá` or explicit source to `Savings for TCx`.

Never record the card spend as a normal bank/Nequi expense. Later card principal payment is debt paydown, not a new expense.

### Megan Fable 5 finance gate

| Fábula | Pass condition |
|---|---|
| Gasto simple de muñeca levantada | One clear expense planned/created, category+budget existing, short response. |
| Nequi/Nike safe account | Normalizes only when context says payment account; otherwise asks. |
| FP/TC exacto | Exactly two movements; retry does not duplicate either. |
| Ambiguity protects Firefly | Missing data/downstream failure causes zero writes and one question/error. |
| Legacy coexistence | `/webhooks/apple-watch-expense` still works and app route does not double-write. |

---

## Aura — reminders, grocery, home, life capture

### Route mapping

| Logical action | `route.agent` | `route.intent` | Domain |
|---|---|---|---|
| Reminder/task | `aura` | `reminder` | `aura.reminder_capture` |
| Grocery | `aura` | `grocery` | `aura.grocery_capture` |
| Home | `aura` | `home_action` | `aura.home_action` |
| Life capture | `aura` | `general_life_capture` | `aura.general_life_capture` |

### Required behavior

- Preserve original dictation in Description/body for auditability.
- Date/time parsing uses `created_at` + `America/Bogota`.
- Every interpreted date/time must be echoed explicitly in the response.
- No-date tasks remain no-date; do not invent `today`.
- Calendar sync is secondary; Notion/list capture fails soft if Calendar OAuth is broken.
- `list`, `done`, `cancel`, and `reschedule` must not create literal tasks.
- Grocery official source is Notion page `304fcdfc-b7b4-804b-ae31-d1460114e6bd`.
- Grocery unchecked = missing, checked = bought/available. Do not delete unless explicit delete verb.
- Home Assistant commands execute only when target/action are clear and safe.
- Sensitive home actions require confirmation/handoff.

### Aura parser regression matrix

Freeze `now = 2026-07-06T14:00:00-05:00` for deterministic tests.

Must cover:

- `mañana a las dos P.M.` → `2026-07-07T14:00:00-05:00`.
- `hoy a las cuatro P.M.` → today 16:00 if future, otherwise confirmation.
- `el 5 de julio a las 8:30 pm` → natural date + 20:30, not today fallback.
- `en dos minutos` / `en media hora` → exact relative due.
- `pasado mañana` → dated task/all-day or one confirmation.
- `03/07/2026 a las 11:00` → slash date rule confirmed for `es_CO`.
- Multiple times (`11am, 2pm y 6pm`) → batch only if supported, else confirmation.
- Queries (`qué tareas tengo pendientes`) → list, zero creation.
- Done/cancel/reschedule → unique match or confirmation.
- Grocery add/bought/list/delete semantics.
- Home command vs home task vs home status query.

### Aura Fable 5 life gate

| Fábula | Pass condition |
|---|---|
| Fast life capture | 1 tap + dictation + clear feedback, no category interrogation on Watch. |
| Platform resilience | Outbox survives wrist-down/offline; no dependence on iPhone for core capture. |
| Privacy | Relationship/pet/health notes are not stored in release logs or memory automatically. |
| Idempotency | Retry does not duplicate Notes/Tasks/Calendar/grocery/home commands. |
| Maintainability | Swift does not encode Notion/Calendar IDs or parsing rules beyond payload hints. |

## Rollout order

1. Dry-run all domains.
2. Aura grocery add/list and simple unscheduled Tasks/Notes.
3. Aura dated reminders with Calendar fail-soft.
4. Megan simple bank/Nequi expenses.
5. Aura safe Home Assistant commands.
6. Megan FP/TC multi-write after partial-error recovery is proven.

## Current rollout state — 2026-08-08

Only rollout step 2's grocery-add path has been promoted, under the narrower exact server-side gate:

```text
route.agent == aura
route.intent == grocery
route.domain == aura.grocery_capture
server write switch == enabled
server allowlist contains aura.grocery_capture
```

The promotion followed a successful authorized canary covering create, Notion read-back, same-request replay, exactly-once verification, archive, and read-back of the archived state. See `GROCERY_WRITE_CANARY.md`.

The following remain explicitly non-writing regardless of client `allow_write` values:

- Aura reminders/tasks, including unscheduled tasks and notes;
- Megan expenses and every Firefly operation;
- Argos/general capture;
- Aura Home Assistant and Calendar operations;
- every unknown route or mismatched agent/intent/domain triple.

Live Mercado requests use direct HTTPS only. Watch-to-iPhone fallback remains restricted to dry-run payloads, so a transient live-write transport failure stays in the durable outbox for retry with the same request ID.
