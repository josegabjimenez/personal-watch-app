# Fable 5 Review 002 — Write-enabled domain gates

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Scope:** enabling real side effects from `/webhooks/mobile-capture-v1` for Megan and Aura.  
**Verdict:** APPROVED WITH CONDITIONS.  
**Blocking rule:** no production writes until all five lenses pass for the target domain.

## Summary verdicts

| Area | Verdict | Conditions |
|---|---|---|
| Megan simple expense writes | APPROVED WITH CONDITIONS | HMAC, outbox, ledger, Firefly live lookup, existing category+budget, dry-run fixtures pass. |
| Megan FP/TC multi-write | APPROVED WITH STRICT CONDITIONS | Partial-error recovery, two-movement plan, ledger external IDs, retry proof. |
| Aura unscheduled Tasks/Notes | APPROVED WITH CONDITIONS | Idempotency, privacy/log redaction, Notion readback. |
| Aura grocery official list | APPROVED WITH CONDITIONS | Official page only, unchecked/checked semantics, semantic dedupe. |
| Aura dated reminders + Calendar | APPROVED WITH CONDITIONS | Parser matrix, explicit Bogotá due response, Calendar fail-soft. |
| Aura Home Assistant commands | APPROVED WITH CONDITIONS | Low-risk reversible commands only; sensitive actions require iPhone confirmation. |
| Aura general life capture | APPROVED WITH CONDITIONS | Keep to Notes/Tasks unless route is clear; no automatic memory; sensitive data redacted. |

## Lens 1 — Product / capture speed

Pass criteria:

- Frequent Watch action completes in ≤5s with clear state.
- Watch asks at most one clarification question.
- `needs_confirmation` is concise and actionable.
- Long details move to iPhone/Discord/Telegram.

Megan risk:

- Over-confirming every expense defeats Watch capture.

Aura risk:

- Over-classifying life capture creates friction.

## Lens 2 — Apple platform fit

Pass criteria:

- Native app uses system dictation/input.
- Outbox persists before network.
- Works with Watch direct networking and iPhone fallback.
- Does not rely on guaranteed background execution.

Blocking checks:

- Wrist-down during send does not lose capture.
- iPhone relay does not duplicate side effects.

## Lens 3 — Security / privacy

Pass criteria:

- HMAC secret in Keychain only.
- No `API_SERVER_KEY` in Watch/iPhone app.
- Logs redacted; financial/personal dictations not stored in release logs.
- `capture.text` treated as untrusted data.
- Sensitive Home Assistant actions require confirmation/handoff.

Blocking checks:

- Static search in app sources finds no secrets.
- Invalid HMAC rejected.
- Prompt injection fixture never creates categories/tags or bypasses rules.

## Lens 4 — Reliability / idempotency

Pass criteria:

- `request_id` generated before network and reused across retries.
- Server ledger stores payload hash and external IDs.
- Duplicate request returns cached/duplicate response.
- Same request_id with different body is rejected.
- Multi-write flows can resume without duplicating first write.

Blocking checks:

- Retry/double-tap/relay test against each domain.
- FP/TC partial-error simulation before enabling FP writes.
- Calendar fail-soft does not roll back Notion Task.

## Lens 5 — Maintainability / evolution

Pass criteria:

- Swift uses `HermesCore` contracts and does not duplicate Firefly/Notion rules.
- Domain logic stays in Hermes/Megan/Aura helpers.
- JSON fixtures/snapshot tests freeze v1 schema.
- Adding an agent/action does not add a new networking stack.

Blocking checks:

- `API_CONTRACT_V1.md`, Swift enums, fixtures and backend route map are aligned.
- Legacy routes still pass after adding v1.

## Final activation checklist

For each domain, collect evidence:

- Signed payload fixture.
- Server ledger entry.
- Dry-run plan.
- Duplicate replay result.
- External readback ID if write-enabled.
- Watch screenshot/response copy.
- Rollback path verified.

Until this exists, domain remains `allow_write:false` or `dry_run:true`.
