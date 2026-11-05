# Decision log — Personal Watch App

The product name is **Personal Watch App** as of October 28, 2026. Earlier entries retain their original architecture, backend integration names and compatibility identifiers. The [current three-action MVP](PERSONAL_WATCH_APP_MVP.md) governs active scope; older proposals do not enable general capture or additional write domains.

## D001 — App shape

**Decision:** One iOS + watchOS app, Watch-first.  
**Status:** Approved.  
**Rationale:** Watch is the fastest capture surface; iPhone is better for setup/history/debug.

## D002 — Canonical endpoint

**Decision:** Use `/webhooks/mobile-capture-v1` as unified mobile route.  
**Status:** Approved with legacy fallback.  
**Rationale:** Covers Watch, iPhone, Siri/App Intents, and Shortcuts migration without being Apple-Watch-only.

## D003 — MVP auth

**Decision:** Native app uses HMAC-SHA256 webhook signature over exact JSON body.  
**Status:** Approved with conditions.  
**Rationale:** Safer than broad API Server token; better than shared static token in body.

## D004 — API Server

**Decision:** Do not use Hermes API Server directly from Watch for MVP.  
**Status:** Rejected for MVP.  
**Rationale:** API Server enables broad agent capability; too risky for mobile secret storage. Consider BFF/scoped token later.

## D005 — Watch networking

**Decision:** Watch sends directly via URLSession; WatchConnectivity is fallback/config sync.  
**Status:** Approved with conditions.  
**Rationale:** Watch-first capture should not depend on iPhone being reachable.

## D006 — Outbox

**Decision:** File-backed local outbox before network attempt.  
**Status:** Approved.  
**Rationale:** watchOS can suspend network on wrist-down; must avoid lost captures.

## D007 — UX navigation

**Decision:** Action-first, not agent-first.  
**Status:** Approved.  
**Rationale:** The Watch should be a command remote, not an agent dashboard.

## D008 — Siri/App Intents timing

**Decision:** Prototype early but ship after core Watch/iPhone capture path.  
**Status:** Approved.  
**Rationale:** Siri/watchOS edge cases need physical validation and should not block MVP core.

## D009 — Dictation/STT

**Decision:** Use Apple system dictation/text input; no custom STT in MVP.  
**Status:** Approved.  
**Rationale:** Speech framework watchOS support is not established from docs; custom audio adds privacy/review risk.
