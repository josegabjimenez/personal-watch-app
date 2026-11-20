# Structured reminders to Todoist Inbox

November 5 continuation: see [the resumed implementation evidence](verification/2026-11-05/REMINDER_RESUME.md) and [the exact historical-service package and rollout plan](../ops/hotfixes/reminder-ai/README.md). Dedicated OAuth, existing labels, an Inbox pin and an independent zero-default cost guard extend the original implementation below. See the [current REST deployment and live-test evidence](verification/2026-11-05/REMINDER_REST_LIVE_TEST.md) and [historical first live-test result](verification/2026-11-05/REMINDER_ACTIVATION_TEST.md). The narrow REST follow-up below preserves older Sync captures; device delivery remains a separate verification step.

This increment retains the standalone Python BFF and the signed mobile-capture v1/outbox contract. The existing Recordatorio action accepts natural Spanish and English; it proposes one new personal task through OpenAI Responses with strict Structured Outputs, then deterministic code validates its title, future intended task time and `America/Bogota`. Mocked language examples establish the interface, not actual model accuracy or latency.

The destination is the authenticated user's existing Todoist Inbox. No project, label, account setting, old reminder or other service is created or reorganized. Grocery still uses the approved official list and its existing helper. Expenses retain their separate confirmed COP flow. Reminder never falls back to Notion, Calendar, Slack or the legacy helper.

## Server configuration gate

All write gates remain server-owned. Configure these paths in the existing private environment file only after secure server authorization is separately completed:

```dotenv
# Preserve the already approved domain list; append this domain only at rollout.
# MOBILE_CAPTURE_WRITE_DOMAINS=...existing domains...,aura.reminder_capture
MOBILE_CAPTURE_REMINDER_OPENAI_KEY_FILE=/etc/hermes/reminder-openai-api-key.txt
MOBILE_CAPTURE_REMINDER_TODOIST_TOKEN_FILE=/etc/hermes/reminder-todoist-token.txt
MOBILE_CAPTURE_REMINDER_PROVIDER=openai
MOBILE_CAPTURE_REMINDER_MODEL=gpt-6-luna
MOBILE_CAPTURE_REMINDER_AI_TIMEOUT=8
MOBILE_CAPTURE_REMINDER_TODOIST_TIMEOUT=5
```

The two credential files must contain only their respective app/server credential, be readable by the service account, and have mode `0600` or stricter. Their contents stay outside the repository, chat, commands, logs and screenshots. Do not extract credentials from desktop ChatGPT plugins, reuse another agent's environment, or assume a Codex subscription supplies app API authorization. No runtime secret has been obtained by this implementation.

`MOBILE_CAPTURE_WRITE_ENABLED=true`, the exact `aura.reminder_capture` allowlist entry, and the explicit client live-write context are still required together. Without either configured path the BFF returns its side-effect-free server-gate plan; the outbox keeps the original capture for retry. Unreadable, empty or insufficiently private files produce a retryable adapter failure with no credential value returned. AI timeout is constrained to 0.1–30 seconds; Todoist timeout to 0.1–15 seconds at CLI startup. Each provider call also has bounded transport concurrency, response size and wall time. The provider and model selectors are configurable. This increment registers only `openai`; an unknown provider fails closed without fallback or an arbitrary credential-bearing endpoint. Verify schema and reasoning support before changing the model.

The gateway reads the minimal user identity to verify Inbox and Bogotá, freezes account/Inbox identity before dispatch, and rejects an account change on retry. It does not change the user's timezone or default alert preference. No custom alert entitlement is inferred from API exposure or the Free plan.

## Validation and truthful results

A valid original `created_at` must include timezone and cannot be in the future. Relative minutes/hours resolve from that original capture, preserving seconds; calendar language resolves to an explicit date and clock time in Bogotá. Missing or ambiguous date/time, elapsed time, multiple tasks, unsupported actions, actual deadlines, recurrence and custom alert requests produce a clarification/rejection without a Todoist write. Invalid structured output, refusal and incomplete model output return a clear no-write clarification. Network/provider unavailability remains retryable.

AI never receives tools, destination authority or the HMAC secret. The code rejects extra proposal fields, unsupported action/timezone, invalid dates and inconsistent schedules. Structured validation cannot prove that the model understood a sentence accurately; actual Spanish/English accuracy still needs separately authorized evaluation.

A successful response requires an exact task readback: title, command marker, Inbox, future intended time, timezone and non-recurring due. It includes:

```json
{
  "status": "committed",
  "dry_run": false,
  "write_committed": true,
  "request_id": "same-original-request-id",
  "domain": "aura.reminder_capture",
  "interpreted_due": {
    "start": "2026-10-30T14:00:00-05:00",
    "timezone": "America/Bogota",
    "display": "2026-10-30 14:00 Bogotá"
  },
  "external_ids": {"todoist_task_id": "synthetic-task-id"},
  "auto_reminder_requested": true,
  "alert_configured": null,
  "notification_verified": false,
  "delivery_status": "pending_verification",
  "plan": {"would_write": true, "side_effects": ["todoist.task.write"]}
}
```

The user sees the task time and “alertas pendientes de verificar.” The timed task is distinct from a deadline. New captures use JSON `POST /api/v1/tasks` with `auto_reminder=true` and `auto_parse_labels=false`, as supported by the official Python SDK. Existing operations retain their persisted Sync transport. The default reminder request follows the user's account preference. Its default offset may differ from the intended task time (for example, 30 minutes earlier); neither that offset nor a configured alert or phone/Watch receipt is verified here. The task time is never shifted to compensate, and global reminder settings are never changed. Ordinary tasks retain Todoist's default p4 priority. The app does not schedule a local fallback alarm.

## Durable retries and uncertain outcomes

The existing SQLite database gains `reminder_operation_ledger`. Before external dispatch it stores the original payload hash, validated plan, command UUID, temporary ID, account binding and transport. Schema migration marks existing rows `sync_v1`; only new captures select `rest_v1`. A durable REST dispatch flag starts at zero and may change to one once. Exact readback and response are stored after dispatch. The mapped task ID is persisted before readback when available. Keep this database on durable private storage; never reset uncertain rows to make a retry “work.”

A per-request OS file lock in the adjacent `*.reminder-locks/` directory serializes concurrent reminder attempts across threads/processes. Process death releases the lock. No SQLite transaction or global grocery/expense execution lock spans a slow AI/network call. The request ledger retains hash binding even when Reminder returns HTTP 502. Reusing an ID with different bytes is HTTP 409. Original-ID replays reuse the plan, command identity and persisted transport; they never reinterpret relative time. New captures with new IDs are distinct operations.

Recovery first reads the mapped task ID or searches paginated active Inbox tasks for the exact command marker. A known missing/changed task is never reposted. For REST captures, the dispatch flag and old-runtime-compatible reconciliation fence commit immediately before the network POST. After that claim, every retry is read-only, including a lost acknowledgement, explicit rejection or crash before the network call. This intentionally favors preventing duplicates over automatic retry after an unproven write. The stable `X-Request-ID` is additional correlation; no undocumented REST idempotency retention guarantee is assumed. Legacy Sync captures retain their original same-UUID behavior before task time elapses. After task time elapses, all recovery is read-only. There is no automatic cross-transport fallback: a missing task, timeout or another capture's rejection never authorizes migration. A separately reviewed, proof-bound migration may select REST only for a specific definitively rejected Sync command, preserving its frozen identity and usage. Rollback to a legacy runtime must disable reminder writes and preserve all records. If a previous result cannot be proven—for example, the task was completed, deleted or moved before receipt—return HTTP 502 and retain uncertainty for operator investigation. A missing active task is not proof that nothing was saved. Never generate a replacement command UUID, claim no write, or clear an uncertain outbox item to resolve this state.

A terminal no-write result is cached for the original request. Clarification is a new capture after the user supplies the missing information. There is no general agent/dispatch, background task migration or external mutation surface in this increment.

## Offline verification

Use synthetic data only:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend python3 -m unittest discover -s backend/tests -v
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer \
CLANG_MODULE_CACHE_PATH=/tmp/personal-watch-reminder-clang-cache \
SWIFTPM_MODULECACHE_OVERRIDE=/tmp/personal-watch-reminder-swift-cache \
swift test --disable-sandbox --package-path apple/PersonalWatchApp/Packages/PersonalWatchCore \
  --scratch-path /tmp/personal-watch-reminder-swift --cache-path /tmp/personal-watch-reminder-package-cache
```

The backend suite mocks all provider calls. Loopback HTTP tests need local socket access. Tests cover invalid timestamps, Bogotá day boundaries, missing/ambiguous/unsupported proposals, strict response parsing, refusals, bounded latency, exact receipts, account changes, cursor pagination, lost acknowledgements, crashes before/after POST, same-command retries, elapsed read-only recovery, signed-request conflicts and unrelated grocery progress during slow AI. Grocery and confirmed COP expense regressions remain included. The shared Swift test decodes the new response without converting unverified alerts into success claims.

## Later authorized rollout and live checks

No deployment, service restart, route activation, network change, paid API call or live capture is performed by this code task. An operator can prepare the following after review and separate authorization:

1. Integrate the reviewed commit into the existing delivery, keep the primary device owner's changes, and preserve the private service paths and durable ledger. Deploy all three new backend modules alongside the server; no Python package install is required.
2. Complete secure OpenAI app API and Todoist server authorization through the selected configuration mechanism. Install the private credential files and verify their permissions without printing content. Preserve existing grocery/expense flags; append only the exact Reminder domain when authorized. Retain the current private endpoint topology.
3. Use the existing service procedure for the authorized rollout/restart. Inspect health and the exact deployed revision without exposing private details. Server health advertising a domain does not prove credentials or a successful write. Check actual installed app endpoint/source parity separately with the device owner.
4. With a separately approved paid/API/live test, submit one clearly identified short-future synthetic reminder through the installed action. Read back the exact task title, Bogotá time and Inbox; retry its original request and verify the same task/command. Do not migrate old reminders or test expenses.
5. Verify the Todoist user's default alert configuration and enabled phone notifications, then observe an actual alert on the intended device under recorded conditions. Report task save, alert configuration and observed delivery separately. If Free does not satisfy the desired notification behavior, return that evidence for a decision without purchasing an upgrade or changing transport.

## Official implementation sources

- [OpenAI GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna): candidate API ID, Responses/Structured Outputs and low reasoning support.
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses): strict `text.format` JSON schema, required nullable fields and refusal handling.
- [Official Todoist Python SDK](https://doist.github.io/todoist-api-python/api/): REST `add_task` JSON fields including `due_datetime`, `auto_reminder` and `auto_parse_labels`, checked November5,2026.
- [Todoist API v1](https://developer.todoist.com/api/v1/): task-add permission, REST task creation, legacy Sync command UUID idempotency, `item_add`, fixed-zone dates, `auto_reminder`, user identity, task readback and cursor pagination. These contracts were read on October 29, 2026; no account call was made to verify runtime behavior.
