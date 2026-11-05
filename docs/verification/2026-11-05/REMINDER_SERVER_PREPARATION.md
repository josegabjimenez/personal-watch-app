# Reminder server preparation — November 5, 2026

Jose explicitly authorized continuing the server installation autonomously.
This receipt covers preparation of the existing Mercado service with reminder
writes disabled and a separate reminder allowance of zero. It does not establish
a saved reminder or delivered phone/Watch notification.

## Source and credential handoff

- Exact staged source: `f8294bebeda53960f222e77cce3cc75bace74529`.
- Twelve-file manifest SHA256:
  `f3e0996c29cc3f88d3132db5885420345f8e07a8c9d4b00dff6cf177243ba92a`.
- Accepted six-file Mercado baseline manifest:
  `bad63273cc49f2f7ca7342adcd3bd7f6d6e00a9658a7682d40d53c96a6d3a281`,
  source `f8738b9a1529c09a7a577550706d7fb876ea6f89`.
- Authenticated root preflight verified the loaded service, original six code
  hashes, external helper, effective grocery-only domain, private credential
  metadata, sandbox and existing application state paths.
- Dedicated Todoist credentials moved through a destination-bound encrypted
  handoff. A durable authenticated server receipt preceded retirement of the
  local source. The server is the sole rotating-refresh writer; old credential
  snapshots must never be restored. Credential files and transfer material are
  excluded from Git.
- A separate server identity read verified the expected account, existing Inbox
  and `America/Bogota` timezone. Credential status was `ready`, with exactly
  `data:read` and `task:add`. This check submitted no Todoist task.
- The existing approved OpenAI credential is reused through its file path. Its
  value was not copied, printed or committed.

## Runtime installation

Installation completed. At `2026-11-05T20:41:39.051496+00:00`, the independent
server verifier returned `verified_prepared`, phase `complete`, runtime `new`,
with all eleven checks passing and no errors. It verified all twelve installed
hashes, the five grocery files' original inodes/bytes/ownership, the three original
configuration pins, exact process/configuration, coherent private backup,
private credential/budget metadata and grocery-only loopback health.

A separate GET through the existing private HTTPS endpoint also returned:

```json
{"status":"ok","service":"mobile-capture-v1-staging","mode":"allowlisted-write","write_domains":["aura.grocery_capture"]}
```

The reviewed change replaces the entrypoint and adds six reminder modules.
The five grocery modules retain their existing bytes and inodes. One private
reminder environment file uses the existing writable state mount, and one service
drop-in loads it. The original unit, grocery drop-in, environment, argv and network
remain intact.

Only the target service stopped. The operator took a coherent private backup
while it was stopped, including the existing parent/grocery state and any
reminder budget pair/WAL/SHM. Application journals, initialized witnesses and
rotating OAuth state stayed in place. The reminder budget was initialized once
at zero and retained on retry; it was never reset or restored.

The first attempt safely restored the original grocery entrypoint after a false
startup verification mismatch. The operator had collapsed repeated
`EnvironmentFiles=` lines into one value. This format follows
[systemd's property renderer](https://github.com/systemd/systemd/blob/v255/src/systemctl/systemctl-show.c#L1168-L1177),
which emits each environment file separately. The correction retains both exact
required paths and rejects ambiguous properties. A regression uses that raw
format; another verifies that a fixed failure code survives rollback without
arbitrary exception text. No runtime hash, gate or rollback guard was relaxed.

The corrected private operator SHA256 is
`8580262ec0b2d14e18df29b197aa98cb30afd865de77de1505fbaded99d640a2`;
all 25 synthetic installer tests passed and were independently rerun. The
read-only final verifier SHA256 is
`789b5ae31a4f9b9bc31ac0acf13e7147bd59538c3af6dea539f01924870acc76`.
The original operator and both coherent backups remain private audit artifacts.
Recovery changes code/configuration only; no application data or OAuth snapshot
was restored during either attempt.

The twelve-file runtime was built from exact source `f8294be`; later
documentation commits do not change that deployed code. PR #7 remains open.
The obsolete four-file updater remains unconfigured/inactive and was not used.

The [sanitized machine-readable receipt](REMINDER_SERVER_PREPARATION_RESULTS.json)
contains the observed checks without private account identifiers or paths.

## Remaining acceptance

The effective write domain remains `aura.grocery_capture`; reminder allowance
remains zero. No signed capture, paid model call, Todoist task write, updater
activation or notification-settings change is part of this preparation.
A separate allowance and reminder activation decision precede any precisely
authorized live canary. Task readback, alert configuration and observed device
delivery require their own evidence. The Watch MVP remains in progress.
