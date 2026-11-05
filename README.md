# Personal Watch App

Personal Watch App is a Watch-first iOS/watchOS capture app for quick Apple Watch and Siri requests to the existing private backend.

The personal MVP contains three actions on iPhone and Apple Watch: **Mercado**, **Recordatorio**, and **Gasto**. It reuses the synchronous BFF, signed requests, and durable outbox.

See [the resumed reminder package and verification](docs/verification/2026-11-05/REMINDER_RESUME.md), [AI/Todoist reminder configuration and verification](docs/REMINDER_AI_TODOIST.md), [the current MVP contract and device handoff](docs/PERSONAL_WATCH_APP_MVP.md), [rename verification](docs/verification/2026-10-28/REPORT.md), and [earlier MVP verification](docs/verification/2026-10-25/REPORT.md). Older status/review documents below are historical evidence and do not certify this candidate.

## Current scope

- Add groceries to the existing official list; confirm success only after a committed helper result.
- Interpret a natural Spanish/English reminder with structured AI, validate its original-capture Bogotá schedule, and save directly to Todoist Inbox. Task creation and verified notification delivery are separate results.
- Preview one simple COP expense against existing accounts/categories/budgets; record only after an explicit confirmation bound to that frozen operation.
- Authenticated Spanish Siri/Shortcuts, Watch dictation, and an iPhone review form.
- Durable offline outboxes, cancellation tombstones, serialized delivery claims, and correlated terminal preview invalidation.
- HTTPS/HMAC V2, Keychain configuration, server-owned per-domain gates, and persistent idempotency ledgers.
- Watch live writes use direct HTTPS. The iPhone relay remains restricted to dry-run traffic.
- General capture/Argos dispatch, favorites, account/catalog creation, transfers, cards, installments, income, and mutations of existing reminders are outside this MVP.

## Review policy

The current delivery uses independent GPT-6.1 Sol Ultra review for consequential confirmation and cancellation boundaries. Older reviews remain as decision records.

## What “BFF” means here

**BFF = Backend for Frontend.** In this project it is a small synchronous backend tailored to the iPhone/Apple Watch frontend. It verifies requests, enforces idempotency, returns short Watch-friendly JSON, and keeps sensitive agent/tool writes behind server-side gates.

The existing Hermes server's generic webhook adapter remains an async ingestion/logging integration. Personal Watch App targets the synchronous BFF because its Watch UI needs immediate feedback. The server integration keeps its established name and protocol identifiers.

## GitHub

Public repository:

```text
https://github.com/josegabjimenez/personal-watch-app
```

Default branch: `main`.

## Naming and compatibility

The current product and repository name is **Personal Watch App**. Existing installation and backend identities remain unchanged: the `dev.josegabjimenez.HermesCapture` bundle-ID family, Keychain/storage namespaces, `com.jose.hermes.mobile_capture` schema, `HermesCapture` wire client value, `X-Hermes-*` headers and `hermes-watch:` external expense IDs are legacy compatibility identifiers. Existing app Swift module and App Intent identities also remain for saved Shortcuts. Changing these identifiers would require a separately reviewed migration of app data, signing or request identity.

Historical reports keep the names and commands of the artifacts actually reviewed. The local checkout folder also keeps its existing path until the Codex project mapping can be updated safely.

## Repository layout

```text
apple/PersonalWatchApp/                 # XcodeGen + shared PersonalWatchCore package

backend/
  mobile_capture_staging_server.py       # synchronous BFF with server-side domain gates
  mobile-capture-v1-webhook-prompt.md    # generic Hermes webhook prompt, log-only/no-write
  fixtures/                              # signed-request test fixtures
  scripts/sign_and_post_mobile_capture.py
  tests/test_mobile_capture_staging_server.py

docs/
  ROADMAP.md
  ARCHITECTURE.md
  API_CONTRACT_V1.md
  BACKEND_STAGING_PLAN.md
  IMPLEMENTATION_STATUS.md
  TAILSCALE_SERVE_ENDPOINT.md
  DOMAIN_GATES_MEGAN_AURA.md
  FABLE5_GATES.md
  FABLE5_REVIEW_*.md
  DECISION_LOG.md
  IMPLEMENTATION_TICKETS.md
  SUBAGENT_SYNTHESIS.md
  XCODE_FOUNDATION_STATUS.md
  WATCH_OUTBOX_CAPTURE_STATUS.md
  IPHONE_SECURE_CONFIGURATION_STATUS.md
  WATCH_CONNECTIVITY_BOOTSTRAP_STATUS.md
  WATCH_E2E_DRY_RUN_STATUS.md
  WATCH_HISTORY_DIAGNOSTICS_STATUS.md
  GPT56_SOL_MAX_REVIEW_001_HISTORY_DIAGNOSTICS.md
  APP_INTENTS_SIRI_STATUS.md
  GPT56_SOL_MAX_REVIEW_002_APP_INTENTS_SIRI.md
  PHYSICAL_DEVICE_READINESS.md
  GPT56_SOL_MAX_REVIEW_003_PHYSICAL_DEVICE_READINESS.md
  GPT56_SOL_MAX_REVIEW_004_DELIVERY_PATH_OBSERVABILITY.md
  GPT56_SOL_MAX_REVIEW_005_FINAL_DRY_RUN_RELEASE_READINESS.md
  GROCERY_WRITE_CANARY.md
  ARCHIVE_PRIVACY_REPORT_STATUS.md
  subagent-briefs/
```

## Backend updates

[Manual backend updates](docs/BACKEND_UPDATES.md) reproduces the exact sealed
four-file grocery package, checks it synthetically, and supports an explicitly
triggered atomic activation with batch-compatible recovery on the existing VPS.
Activation requires the documented handoff; push and merge cannot deploy.

## Local development

Start the staging BFF:

```bash
python3 backend/mobile_capture_staging_server.py --port 8650
```

Health check:

```bash
curl -fsS http://127.0.0.1:8650/health
```

Run tests:

```bash
cd backend
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

## Tailscale Serve for device testing

Expose the local BFF inside a Tailscale tailnet:

```bash
tailscale serve --bg --yes --https=8650 http://127.0.0.1:8650
```

Use this public-safe placeholder in docs/app config examples:

```text
https://<TAILSCALE_DNS_NAME>:8650/webhooks/mobile-capture-v1
```

Replace `<TAILSCALE_DNS_NAME>` locally with your actual tailnet DNS name. Do not commit secrets or private local config.

## Security status

All writes are disabled by default. This candidate implements only these exact server-owned route triples:

```text
aura  / grocery  / aura.grocery_capture
aura  / reminder / aura.reminder_capture
megan / expense  / megan.expense_capture
```

Client `allow_write` fields are never authority. Enabling a domain requires private deployment configuration and a reviewed domain adapter. General capture and unknown domains cannot gain write authority from environment flags.

Still required before broader production writes:

- review/merge and separately authorized backend rollout;
- a reviewed Aura grocery helper implementing the official-list, add-only and idempotency contracts; private server API credentials and a separately authorized Todoist reminder check;
- durable HTTPS service and ledger/helper-state retention, backup, and compatible restore;
- authorized signing and physical iPhone/Watch/Siri checks for this exact candidate;
- actual reminder alarm readback and observed notification delivery on Jose's devices;
- separately authorized live-domain checks. Synthetic financial tests never use real records.

## License

MIT — see [`LICENSE`](LICENSE).
