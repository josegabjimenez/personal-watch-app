# Hermes Watch Siri AI

Watch-first iOS/watchOS capture system for routing quick Apple Watch and Siri captures into Hermes Agents.

The project starts with a documented backend staging layer and API contract, then moves toward a native iOS + watchOS app that prioritizes Apple Watch capture speed.

## Current scope

- Native iOS + watchOS app roadmap.
- Watch-first UX and Apple-platform constraints.
- `mobile_capture.v1` API contract.
- Synchronous BFF staging endpoint for Watch-friendly responses.
- HMAC V2 signing and idempotency ledger design.
- Server-owned domain gates for write-enabled actions.
- GPT-5.6-sol Max Thinking review gates for important architecture/security/product decisions.
- Initial XcodeGen + Swift Package scaffold under `apple/HermesCapture`.
- Watch system text-entry/dictation flow with local file-backed outbox.
- iPhone HTTPS endpoint configuration, health check, and route secret in Keychain.
- User-initiated WatchConnectivity bootstrap into Watch Keychain.
- End-to-end Watch outbox delivery to the synchronous BFF.
- Local Watch history plus sanitized aggregate diagnostics on iPhone.
- Authenticated iOS/watchOS App Intents and Spanish App Shortcuts.
- Transient Watch→iPhone capture fallback that never transfers the HMAC secret.
- Shared privacy manifest and automatic-signing readiness for physical-device QA.
- Delivery-path observability (`Directo` vs `vía iPhone`) without exposing capture content.
- Physical paired-device dry-run QA passed for direct HTTPS, iPhone fallback, outage persistence, and same-item retry.
- Physical iPhone/Watch Siri capture and archive privacy-manifest gates passed.
- Allowlisted Aura Mercado writes with a reversible Notion canary; every other domain remains dry-run.

## Review policy

New reviews use GPT-5.6-sol with Max Thinking. Historical Fable 5 documents remain in the repository as decision records, but Fable 5 is no longer used as the reviewer.

## What “BFF” means here

**BFF = Backend for Frontend.** In this project it is a small synchronous backend tailored to the iPhone/Apple Watch frontend. It verifies requests, enforces idempotency, returns short Watch-friendly JSON, and keeps sensitive agent/tool writes behind server-side gates.

The generic Hermes webhook adapter remains useful for async ingestion/logging, but the Watch MVP should target the BFF because the Watch UI needs immediate feedback.

## GitHub

Public repository:

```text
https://github.com/josegabjimenez/hermes-watch-siri-ai
```

Default branch: `main`.

## Repository layout

```text
apple/HermesCapture/                    # XcodeGen + Swift Package scaffold

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

Current status is **write-enabled only for Aura Mercado** after a reversible create/read/idempotency/archive/read canary. The exact server-side allowlist is:

```text
agent:  aura
intent: grocery
domain: aura.grocery_capture
```

Client `allow_write` fields are never authorization. The server must also enable the exact triple above. Megan expenses, reminders, general capture, Home Assistant, Calendar, and unknown domains remain dry-run/no-write.

Still required before broader production writes:

- durable deployment behind HTTPS;
- production-grade ledger retention/backup;
- archive privacy-report and App Store privacy reconciliation;
- physical Apple Watch smoke QA for candidate 0.3.3 (6);
- separate domain gates and reversible canaries for every additional write domain.

## License

MIT — see [`LICENSE`](LICENSE).
