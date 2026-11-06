# Todoist local authorization follow-up — November 5, 2026

Jose approved the dedicated Todoist browser flow and the previously documented
private local credential destination. Sign-in completed, but the first consent
route ended at `app.todoist.com/oauth/permission_submit` with Todoist's 404 page
after **Allow access**. No callback arrived and no credential bundle was saved.
The local callback attempts subsequently timed out. A successful browser click
is therefore not evidence that backend authorization completed.

## Provider discovery and correction

Credential-free GET requests to the official metadata endpoints returned:

- [Protected resource metadata](https://api.todoist.com/.well-known/oauth-protected-resource):
  resource and authorization server `https://api.todoist.com`.
- [Authorization server metadata](https://api.todoist.com/.well-known/oauth-authorization-server):
  authorization endpoint `https://api.todoist.com/oauth/authorize`, registration
  endpoint `https://api.todoist.com/oauth/register`, token endpoint
  `https://api.todoist.com/oauth/access_token`, PKCE `S256`, and public-client
  authentication method `none`.

The prose [OAuth guide](https://developer.todoist.com/api/v1/#tag/Authorization/OAuth)
still names the `app.todoist.com` authorization route. The setup helper now uses
the endpoint advertised by the live authorization-server metadata. The observed
404 motivates this correction; discovery alone does not prove that it resolves
the complete browser flow.

Only the authorization endpoint changes. The existing `task:add` and `data:read`
scopes, redirect/state checks, PKCE, fixed token transport, private-file rules and
rotating refresh storage remain unchanged. No arbitrary endpoint fallback or
additional resource parameter is introduced.

## Candidate verification

The focused authorization suite passed all 35 synthetic tests. Independent review
verified the discovery documents and found no blocker in the endpoint correction.
The reviewed twelve-file candidate replaces only `todoist_auth.py`; all other
runtime hashes and baseline metadata match the previous seal. Its new manifest
SHA256 is `f3e0996c29cc3f88d3132db5885420345f8e07a8c9d4b00dff6cf177243ba92a`.
The earlier `548a7ac4…11344af` artifact remains the pre-correction checkpoint.
All 37 generated-package/runtime tests passed, and the new package was built and
independently verified against that seal. No other runtime hash changed.

## Observed authorization and server follow-up

The corrected browser flow subsequently completed successfully on November 5.
The callback exchanged the code and saved the dedicated local bundle. A separate
read-only status check reported `configured: true`, `status: ready`, and exactly
`data:read` plus `task:add`. The file was owned by the current user with mode
`0600`, under a `0700` parent, and ignored by Git. No token value was printed.

Jose then authorized autonomous server installation. The authenticated encrypted
handoff, local-source retirement, server identity verification and actual runtime
state are recorded separately in
[REMINDER_SERVER_PREPARATION.md](REMINDER_SERVER_PREPARATION.md).
Provider authorization, service installation, a saved Todoist task and observed
phone/Watch delivery remain separate results.
