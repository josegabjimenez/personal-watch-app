# Personal Watch App — minimal backend rollout proposal

This is a reviewable proposal, not an executed deployment. The Xcode/device setup request does not authorize production domain activation or a financial canary.

## Verified baseline

On October 25, 2026, the existing app's configured HTTPS `/health` returned:

```json
{"status":"ok","service":"mobile-capture-v1-staging","mode":"allowlisted-write","write_domains":["aura.grocery_capture"]}
```

The endpoint and authentication remain private. Health proves current grocery-only gating, not the deployed code identity or adapter implementation. No exact Aura helper source or supported existing server-access route was found in the known local sources; acquisition is not being repeated and no infrastructure is being created.

## Smallest source handoff

Obtain the exact deployed `apple_watch_capture.py` with credentials removed, its version/commit or SHA256, and sanitized service launch metadata giving the BFF source path, Aura helper path, and durable adapter-state path. Do not include `.env` contents, tokens, device identifiers, ledgers or real captures.

That source is needed to prepare any necessary adapter correction. The current BFF's subprocess mocks cannot certify official-list identity, pagination, create-only Notion authority, durable idempotency, date handling or actual notification behavior in the external helper.

## Proposed sequence after review and deployment approval

1. Reuse the existing host, service, HTTPS/Tailscale proxy, endpoint and server-held credentials. Deploy only the reviewed BFF and packaged expense adapter v3. Preserve a recoverable prior code snapshot and the existing SQLite ledger and Aura state in place, including writing/uncertain operation claims. Do not initialize a replacement ledger.
2. Initially preserve the currently enabled domain exactly: `MOBILE_CAPTURE_WRITE_ENABLED=1`, `MOBILE_CAPTURE_WRITE_DOMAINS=aura.grocery_capture`. Point `MOBILE_CAPTURE_AURA_HELPER` at the exact reviewed existing helper and `MOBILE_CAPTURE_EXPENSE_HELPER` at the packaged v3 script. Preserve private Firefly/Notion/Calendar configuration. Verify code identity and health after the separately authorized restart.
3. Before adding `aura.reminder_capture`, verify/patch the exact Aura helper against fake services. It must create only the intended new Task, echo the supplied frozen Bogotá instant in preflight and commit, preserve durable request identity after restart/lost ACK, and never mutate an existing reminder. Preserve `--no-notify` until its implementation is understood. Require actual Calendar event/alarm readback and a separately authorized physical alert check; a Task or event ID alone is insufficient.
4. Add `megan.expense_capture` separately after validating the packaged v3 adapter and existing private server configuration. Preserve COP agreement, the frozen preview, explicit confirmation and external-ID reconciliation. Real records or transactions are not required for synthetic verification; any live expense check needs separate authorization.
5. Roll back a new domain by disabling that domain first while preserving all ledgers/state. Older binaries must not reinterpret new financial state or reactivate uncertain writes. Replaying an app request must never become permission for a second create.

No broad/general dispatch, new cloud service, new notification transport, account/catalog creation, or credential expansion is proposed. The existing `notification_verified=false` / `delivery_status=pending_verification` response remains truthful until alarm delivery is actually established.
