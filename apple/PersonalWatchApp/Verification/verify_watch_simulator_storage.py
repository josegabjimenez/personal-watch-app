#!/usr/bin/env python3
"""Read-only assertions for the synthetic cases from a completed Watch UI run.

Pass the target simulator app's tmp/PersonalWatchSyntheticUI directory and the
xcodebuild log for ONE successful full run. This never contacts a device or a
service and never changes an outbox. Missing audit logs cannot prove no sends.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path


HEALTH = {
    "testHealthReadyShowsGateEnabled", "testHealthDisabledGateIsNotClaimedReady",
    "testHealthHTTPFailureIsSpecific", "testHealthTLSFailureKeepsSecureConnection",
    "testHealthTimeoutIsSpecific",
}
DNS_RETAINED = {
    "testFourDNSFailuresCanBeRetainedAfterCancellingFirst",
    "testRetainedReminderAllowsOnlyExplicitDifferentIntent",
    "testRetainedReminderIsExcludedFromBulkRetry",
}
PURE_UI = {
    "testWatchHomeLaunchesWithApprovedActions", "testFreshWatchHistoryIsEmptyAndReadOnly",
    "testUnconfiguredWatchChecksWithoutSending",
}
POST_COUNTS = {
    "testGroceryCommitClearsEditor": 1,
    "testGrocerySixthExplicitRetryPreservesOriginalAndDistinctDraft": 6,
    "testReminderShowsBogotaScheduleWithNotificationStillPending": 1,
    "testRetainedReminderAllowsOnlyExplicitDifferentIntent": 1,
    "testServerPendingReminderCanBeRetainedWithoutRetry": 1,
    "testExpiredReminderNoWriteReleasesEditorAcrossRelaunch": 1,
    "testEligibleBulkRetryUsesOriginalAndThenBecomesNoOp": 2,
    "testExpensePreviewNeedsExplicitConfirmationAndCommitsOnce": 2,
    "testExpenseCancellationPersistsAcrossRelaunch": 2,
    "testOfflineExpenseConfirmationCanBeCancelled": 3,
    "testExpiredExpensePreviewIsTerminal": 2,
}
SEEDED_GET_ONLY = DNS_RETAINED | {
    "testConnectionCheckDoesNotRetryFourFailureReminder",
    "testUnknownSendingReminderCanBeRetainedWithoutAnotherSend",
    "testRefreshedExpenseCurrentSendingStaysDisabledOnRelaunch",
}
ALL_CASES = HEALTH | PURE_UI | POST_COUNTS.keys() | SEEDED_GET_ONLY


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def wire_hash(payload):
    # The capture encoder uses sortedKeys + withoutEscapingSlashes. These
    # fixtures contain JSON integers/booleans/strings, not ambiguous floats.
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def expected_seed_payload(case_id, kind="reminder", suffix="old-reminder", token=None):
    """Literal, complete capture contract for the fixed synthetic seeds."""
    expense = kind == "expense"
    text = "45 mil Uber Efectivo" if expense else "Recordatorio anterior sintético"
    context = {
        "user_confirmation": token is not None, "user_cancellation": False,
        "watch_reachable_to_phone": False, "shortcut_compatibility": False,
        "requires_confirmation": expense and token is None,
        "dry_run": False, "allow_write": True, "allow_firefly_write": expense,
    }
    if token is not None:
        context.update(confirmation_token=token, operation_id="exp_" + "8" * 32)
    return {
        "event_type": "mobile_capture.v1", "schema": "com.jose.hermes.mobile_capture", "schema_version": 1,
        "request_id": "synthetic-" + suffix + "-" + case_id, "created_at": "2026-11-01T00:00:00Z",
        "source": {"app": "HermesCapture", "app_version": "synthetic-ui", "platform": "simulator",
                   "os_version": "synthetic", "device_id": "synthetic-ui-device", "locale": "es_CO",
                   "timezone": "America/Bogota", "surface": "synthetic-ui"},
        "route": {"agent": "megan" if expense else "aura", "intent": kind,
                  "domain": "megan.expense_capture" if expense else "aura.reminder_capture"},
        "capture": {"modality": "watch_dictation", "language": "es", "text": text, "raw_text": text},
        "entities": {"currency": "COP", "tags": []}, "context": context,
        "delivery": {"expect_response": True, "response_preference": "short"},
        "client_state": {"outbox_attempt": 0},
    }


def validate_case(method, case_id, root):
    folder = root / case_id
    outbox = folder / "outbox.json"
    audit = folder / "transport-audit.jsonl"
    rows = json.loads(outbox.read_text()) if outbox.exists() else []
    require(isinstance(rows, list), "Outbox must be a list")
    entries = [json.loads(line) for line in audit.read_text().splitlines()] if audit.exists() else []
    posts = [x for x in entries if x["method"] == "POST"]
    gets = [x for x in entries if x["method"] == "GET"]
    indexed = {x["payload"]["request_id"]: x for x in rows}
    require(len(indexed) == len(rows), "Duplicate outbox request identity")
    for x in entries:
        require(set(x) == {"method", "path", "body_sha256", "request_id"}, "Unexpected audit fields")
        if x["method"] == "GET":
            require(x["path"] == "/health" and x["request_id"] is None, "Health sent capture identity")
            require(x["body_sha256"] == hashlib.sha256(b"").hexdigest(), "Health sent a body")
        else:
            require(x["method"] == "POST" and x["path"] == "/webhooks/mobile-capture-v1", "Unexpected request")
            require(x["request_id"] in indexed, "Transport without a durable outbox row")
            require(x["body_sha256"] == wire_hash(indexed[x["request_id"]]["payload"]), "Wire body changed")

    if method in PURE_UI:
        require(not rows and not entries, "Read-only home/history/setup case changed synthetic storage")
        return {"outbox_rows": 0, "audit_present": audit.exists(), "transport_assertion": "not exercised"}

    # A positive control makes best-effort audit absence a FAILURE for every
    # case whose acceptance relies on request counts, including no-POST cases.
    require(audit.exists() and entries, "Missing positive-control transport audit")
    require(len(posts) == POST_COUNTS.get(method, 0), "Unexpected POST count")
    expected_gets = 1 if method in HEALTH | SEEDED_GET_ONLY else 0
    require(len(gets) == expected_gets, "Unexpected health count")
    for row in rows:
        payload = row["payload"]
        if not payload["request_id"].startswith("synthetic-"):
            require(row["attempts"] == sum(x["request_id"] == payload["request_id"] for x in posts),
                    "Attempt count differs from actual intercepted dispatches")

    if method in HEALTH:
        require(not rows, "Connection check enqueued a capture")
    if method in DNS_RETAINED | {"testConnectionCheckDoesNotRetryFourFailureReminder"}:
        original_id = "synthetic-old-reminder-" + case_id
        original = indexed[original_id]
        require(original["payload"] == expected_seed_payload(case_id), "Original full payload/body changed")
        require(original["created_at"] == "2026-11-01T00:00:00Z", "Original outbox date changed")
        require(original["status"] == "failed" and original["attempts"] == 4, "Original state/attempts changed")
        require(original["last_error"] == "network_-1003" and "last_response" not in original, "Original outcome changed")
        require(bool(original.get("retry_suppressed_at")) == (method in DNS_RETAINED), "Retention choice lost")
        expected_rows = 2 if method == "testRetainedReminderAllowsOnlyExplicitDifferentIntent" else 1
        require(len(rows) == expected_rows, "Old intent replaced or new capture invented")
        if expected_rows == 2:
            other = next(x for x in rows if x is not original)
            require(other["status"] == "sent" and other["attempts"] == 1, "Distinct intent did not finish once")
            require(posts[0]["request_id"] != original_id, "Retained intent was replayed")
    if method == "testUnknownSendingReminderCanBeRetainedWithoutAnotherSend":
        require(len(rows) == 1, "Unknown capture replaced")
        row = rows[0]
        require(row["status"] == "sending" and row["attempts"] == 1 and row.get("retry_suppressed_at"),
                "Retaining unknown intent altered its outcome")
        require(row["payload"] == expected_seed_payload(case_id), "Unknown intent full payload/body changed")
        require(row["created_at"] == "2026-11-01T00:00:00Z"
                and "last_response" not in row and "last_error" not in row, "Unknown intent outcome/date changed")
    if method == "testRefreshedExpenseCurrentSendingStaysDisabledOnRelaunch":
        require(len(rows) == 3, "Confirmation history changed without a send")
        initial_token = "synthetic-ui-confirmation-token-old"
        refreshed_token = "synthetic-ui-confirmation-token"
        for suffix, token in [("expense-preview", None), ("expense-confirm-old", initial_token),
                              ("expense-confirm-current", refreshed_token)]:
            row = indexed["synthetic-" + suffix + "-" + case_id]
            require(row["payload"] == expected_seed_payload(case_id, "expense", suffix, token),
                    "Expense seed full payload/identity/token changed")
            require(row["created_at"] == "2026-11-01T00:00:00Z" and row["attempts"] == 1,
                    "Expense seed date/attempt changed")
        for suffix, token in [("expense-preview", initial_token), ("expense-confirm-old", refreshed_token)]:
            row = indexed["synthetic-" + suffix + "-" + case_id]
            response = row["last_response"]
            require(row["status"] == "sent" and response["status"] == "needs_confirmation", "Missing correlated preview refresh")
            require(response["request_id"] == row["payload"]["request_id"]
                    and response["domain"] == "megan.expense_capture"
                    and response["operation_id"] == "exp_" + "8" * 32
                    and response["confirmation_token"] == token, "Broken preview refresh correlation")
            require(response["write_committed"] is False
                    and response["plan"] == {"would_write": False, "side_effects": []}, "Preview became a committed withdrawal")
        current = indexed["synthetic-expense-confirm-current-" + case_id]
        require(current["status"] == "sending" and current["attempts"] == 1, "Current uncertainty changed")
        require("last_response" not in current, "Invented acknowledgement")
    if method == "testGrocerySixthExplicitRetryPreservesOriginalAndDistinctDraft":
        require(len(rows) == 1 and rows[0]["attempts"] == 6 and rows[0]["status"] == "failed", "Grocery recovery changed identity")
        require(len({x["request_id"] for x in posts}) == 1 and len({x["body_sha256"] for x in posts}) == 1,
                "Sixth retry used a new request/body")
    if method == "testEligibleBulkRetryUsesOriginalAndThenBecomesNoOp":
        require(len(rows) == 1 and rows[0]["status"] == "sent" and rows[0]["attempts"] == 2, "Bulk replay changed identity")
        require(posts[0]["request_id"] == posts[1]["request_id"] and posts[0]["body_sha256"] == posts[1]["body_sha256"],
                "Bulk retry changed original request/body")
    return {"outbox_rows": len(rows), "audit_present": True, "posts": len(posts), "health_gets": len(gets)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-root", required=True, type=Path)
    parser.add_argument("--test-log", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    require(args.case_root.name == "PersonalWatchSyntheticUI", "Only the synthetic case directory is accepted")
    require(not args.report.exists(), "Preserve existing reports; choose a fresh report path")
    log = args.test_log.read_text()
    require(re.search(r"\*\* TEST (?:EXECUTE )?SUCCEEDED \*\*", log), "UI runner did not report a successful full run")
    matches = re.findall(r"WATCH_UI_CASE[^\n]*?\b(test\w+)\][ \t]+([a-z0-9-]{1,128})[ \t]*$", log, re.MULTILINE)
    mapping = dict(matches)
    require(len(matches) == len(mapping), "Use the log of one non-repeated full test run")
    require(len(set(mapping.values())) == len(mapping), "Every case must have separate storage")
    require(set(mapping) == ALL_CASES, "Missing/unexpected Watch acceptance methods: " + str(ALL_CASES ^ set(mapping)))
    results = {method: validate_case(method, case_id, args.case_root) for method, case_id in mapping.items()}
    report = {"status": "pass", "case_count": len(results), "synthetic_only": True, "cases": results}
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"Watch synthetic storage/audit checks passed for {len(results)} cases")


if __name__ == "__main__":
    main()
