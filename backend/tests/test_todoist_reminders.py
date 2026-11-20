"""Synthetic, offline Todoist v1 contract and recovery tests."""
from __future__ import annotations

import copy
import io
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from urllib import error, parse

import todoist_reminders as todoist
import todoist_auth as auth

COMMAND = "96e26ce3-5cd1-4b7b-a393-2f64abac5bdf"
TEMP_ID = "c055f623-d8b0-44cf-b47a-856684b1293c"
IDENTITY = {"user_id": "synthetic-user", "inbox_project_id": "synthetic-inbox", "timezone": "America/Bogota"}
USER = {"id": IDENTITY["user_id"], "inbox_project_id": IDENTITY["inbox_project_id"], "tz_info": {"timezone": IDENTITY["timezone"]}, "token": "private-provider-value", "email": "synthetic@example.invalid"}
PLAN = {"title": "Llamar a mamá", "due": {"start": "2099-10-30T14:00:00-05:00", "timezone": "America/Bogota", "display": "30 oct 2099 14:00 Bogotá"}}


def task(**changes):
    value = {
        "id": "synthetic-task", "user_id": IDENTITY["user_id"],
        "project_id": IDENTITY["inbox_project_id"], "content": PLAN["title"],
        "description": todoist.marker(COMMAND), "parent_id": None, "section_id": None,
        "checked": False, "is_deleted": False, "deadline": None,
        "labels": [], "priority": 1,
        "due": {"date": "2099-10-30T19:00:00.000000Z", "timezone": "America/Bogota", "is_recurring": False},
    }
    value.update(changes)
    return value


def page(rows=None, cursor=None):
    return {"results": rows or [], "next_cursor": cursor}


def sync(mapping=True, status="ok"):
    return {"sync_status": {COMMAND: status}, "temp_id_mapping": {TEMP_ID: "synthetic-task"} if mapping else {}}


class FakeResponse:
    def __init__(self, value, *, raw=None, wait=None):
        self.raw = json.dumps(value, ensure_ascii=False).encode() if raw is None else raw
        self.closed = False
        self.wait = wait

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def read(self, amount):
        if self.wait is not None:
            self.wait.wait(2)
        return self.raw[:amount]


class FakeOpener:
    def __init__(self, values):
        self.values = list(values)
        self.calls = []
        self.responses = []

    def open(self, req, timeout):
        self.calls.append((req, timeout))
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        response = value if isinstance(value, FakeResponse) else FakeResponse(value)
        self.responses.append(response)
        return response


class TodoistGatewayTests(unittest.TestCase):
    @staticmethod
    def rejected():
        return error.HTTPError("https://api.todoist.com/api/v1/sync", 401,
                               "sensitive provider rejection", {}, io.BytesIO(b"sensitive provider body"))

    def gateway(self, values, *, timeout=5):
        opener = FakeOpener(values)
        patch = mock.patch.object(todoist.request, "build_opener", return_value=opener)
        patch.start()
        self.addCleanup(patch.stop)
        return todoist.TodoistGateway("synthetic-token", timeout=timeout), opener

    def test_identity_retains_only_destination_binding(self):
        gateway, opener = self.gateway([USER])
        self.assertEqual(gateway.identity(), IDENTITY)
        req, timeout = opener.calls[0]
        self.assertEqual(req.full_url, "https://api.todoist.com/api/v1/user")
        self.assertEqual(req.method, "GET")
        self.assertLessEqual(timeout, 5)
        self.assertTrue(opener.responses[0].closed)

    def test_item_add_exact_schedule_inbox_and_readback(self):
        gateway, opener = self.gateway([USER, page(), sync(), task()])
        mapping = []
        result = gateway.create_or_reconcile(PLAN, COMMAND, TEMP_ID, IDENTITY, on_external_id=mapping.append)
        self.assertEqual(result, {"task_id": "synthetic-task", "duplicate": False, "auto_reminder_requested": True, "alert_configured": None})
        self.assertEqual(mapping, ["synthetic-task"])
        req = opener.calls[2][0]
        self.assertEqual(req.full_url, "https://api.todoist.com/api/v1/sync")
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.get_header("Content-type"), "application/x-www-form-urlencoded")
        command = json.loads(parse.parse_qs(req.data.decode())["commands"][0])[0]
        self.assertEqual(command, {
            "type": "item_add", "uuid": COMMAND, "temp_id": TEMP_ID,
            "args": {"content": PLAN["title"], "description": todoist.marker(COMMAND), "project_id": IDENTITY["inbox_project_id"],
                     "due": {"date": "2099-10-30T19:00:00Z", "timezone": "America/Bogota", "is_recurring": False},
                     "auto_reminder": True, "auto_parse_labels": False, "labels": [], "priority": 1},
        })
        self.assertTrue(all(response.closed for response in opener.responses))

    def test_rest_creation_uses_json_one_claim_and_exact_get_readback(self):
        gateway, opener = self.gateway([USER, page(), {"id": "synthetic-task"}, task()])
        claim, mapping = mock.Mock(), []
        result = gateway.create_rest_or_reconcile(PLAN, COMMAND, IDENTITY, on_before_post=claim, on_external_id=mapping.append)
        self.assertEqual(result["task_id"], "synthetic-task")
        self.assertFalse(result["duplicate"])
        self.assertEqual(mapping, ["synthetic-task"])
        claim.assert_called_once_with()
        req = opener.calls[2][0]
        self.assertEqual(req.full_url, todoist.API_ROOT + "/tasks")
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(req.get_header("X-request-id"), COMMAND)
        self.assertEqual(json.loads(req.data), {
            "content": PLAN["title"], "description": todoist.marker(COMMAND), "project_id": IDENTITY["inbox_project_id"],
            "labels": [], "priority": 1, "due_datetime": "2099-10-30T19:00:00Z",
            "auto_reminder": True, "auto_parse_labels": False})
        self.assertEqual(opener.calls[3][0].full_url, todoist.API_ROOT + "/tasks/synthetic-task")

    def test_rest_claim_is_immediately_before_post_and_mapping_precedes_get(self):
        gateway, opener = self.gateway([USER, page(), {"id": "synthetic-task"}, error.URLError("readback unknown")])
        order = []
        original_open = opener.open
        def opened(req, timeout):
            if req.method == "POST":
                self.assertEqual(order, ["claimed"])
            return original_open(req, timeout)
        opener.open = opened
        with self.assertRaises(todoist.RetryableTodoistError):
            gateway.create_rest_or_reconcile(PLAN, COMMAND, IDENTITY,
                on_before_post=lambda: order.append("claimed"), on_external_id=lambda task_id: order.append(task_id))
        self.assertEqual(order, ["claimed", "synthetic-task"])

    def test_rest_callback_failure_prevents_post(self):
        gateway, opener = self.gateway([USER, page()])
        with self.assertRaises(todoist.RetryableTodoistError):
            gateway.create_rest_or_reconcile(PLAN, COMMAND, IDENTITY,
                on_before_post=mock.Mock(side_effect=RuntimeError("synthetic persistence failure")))
        self.assertTrue(all(req.method == "GET" for req, _ in opener.calls))

    def test_rest_401_never_reposts_or_refreshes_after_dispatch_claim(self):
        provider = mock.Mock(return_value="old-token")
        _, opener = self.gateway([USER, page(), self.rejected()])
        gateway = todoist.TodoistGateway(provider)
        claim = mock.Mock()
        with self.assertRaisesRegex(todoist.PermanentTodoistError, "^todoist_authorization_required$"):
            gateway.create_rest_or_reconcile(PLAN, COMMAND, IDENTITY, on_before_post=claim)
        claim.assert_called_once_with()
        self.assertEqual(sum(req.method == "POST" for req, _ in opener.calls), 1)
        self.assertFalse(any(call.kwargs for call in provider.call_args_list))

    def test_rest_existing_marker_recovers_without_claim_or_post(self):
        gateway, opener = self.gateway([USER, page([task()])])
        claim = mock.Mock()
        result = gateway.create_rest_or_reconcile(PLAN, COMMAND, IDENTITY, on_before_post=claim)
        self.assertTrue(result["duplicate"])
        claim.assert_not_called()
        self.assertTrue(all(req.method == "GET" for req, _ in opener.calls))

    def test_rest_requires_durable_callback_before_any_provider_request(self):
        gateway, opener = self.gateway([])
        with self.assertRaisesRegex(todoist.PermanentTodoistError, "dispatch_guard_required"):
            gateway.create_rest_or_reconcile(PLAN, COMMAND, IDENTITY, on_before_post=None)
        self.assertEqual(opener.calls, [])

    def test_rest_slow_claim_cannot_post_after_original_deadline(self):
        gateway, opener = self.gateway([USER, page()], timeout=0.03)
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def slow_claim():
            entered.set(); release.wait(2); finished.set()
        try:
            with self.assertRaises(todoist.RetryableTodoistError):
                gateway.create_rest_or_reconcile(PLAN, COMMAND, IDENTITY, on_before_post=slow_claim)
            self.assertTrue(entered.is_set())
        finally:
            release.set(); self.assertTrue(finished.wait(1))
        self.assertTrue(all(req.method == "GET" for req, _ in opener.calls))

    def test_rest_readback_keeps_exact_bogota_zone_and_full_task_contract(self):
        wrong_zone = {"date": "2099-10-30T19:00:00Z", "timezone": "UTC", "is_recurring": False}
        gateway, _ = self.gateway([USER, page(), {"id": "synthetic-task"}, task(due=wrong_zone)])
        with self.assertRaisesRegex(todoist.RetryableTodoistError, "readback_mismatch"):
            gateway.create_rest_or_reconcile(PLAN, COMMAND, IDENTITY, on_before_post=lambda: None)

    def test_label_catalog_paginates_only_existing_safe_exact_names(self):
        rows = [{"id": f"label-{i}", "name": name} for i, name in enumerate(("On_Phone", "Someday", "GCal", "unknown"))]
        gateway, opener = self.gateway([page(rows, "opaque/+?=cursor"), page([
            {"id": "label-duration", "name": "10_min"},
            {"id": "deleted-label", "name": "On_PC", "is_deleted": True},
        ])])
        self.assertEqual(gateway.label_catalog(), ("10_min", "On_Phone"))
        params = [parse.parse_qs(parse.urlsplit(req.full_url).query) for req, _ in opener.calls]
        self.assertEqual(params, [{"limit": ["100"]}, {"limit": ["100"], "cursor": ["opaque/+?=cursor"]}])
        self.assertTrue(all(req.method == "GET" and parse.urlsplit(req.full_url).path == "/api/v1/labels" for req, _ in opener.calls))

    def test_label_catalog_rejects_invalid_duplicates_and_unbounded_pagination(self):
        row = {"id": "label-phone", "name": "On_Phone"}
        cases = ([page([row, row])], [page([{"id": "label-phone", "name": []}])],
                 [page([{"id": "label-phone", "name": "On_Phone", "is_deleted": "false"}])],
                 [page([{"id": "../private", "name": "On_Phone"}])],
                 [page([row], "repeat"), page(cursor="repeat")],
                 [page(cursor=str(i)) for i in range(todoist.MAX_PAGES)])
        for pages in cases:
            with self.subTest(pages=pages):
                gateway, opener = self.gateway(pages)
                with self.assertRaises(todoist.RetryableTodoistError):
                    gateway.label_catalog()
                self.assertTrue(all(req.method == "GET" for req, _ in opener.calls))

    def test_selected_labels_are_rechecked_sent_as_names_and_exactly_read_back(self):
        labels = ["On_Phone", "10_min", "Free_Time"]
        plan = {**PLAN, "labels": labels}
        catalog = page([{"id": f"label-{i}", "name": name} for i, name in enumerate(labels)])
        gateway, opener = self.gateway([USER, page(), catalog, sync(), task(labels=list(reversed(labels)))])
        result = gateway.create_or_reconcile(plan, COMMAND, TEMP_ID, IDENTITY)
        self.assertEqual(result["task_id"], "synthetic-task")
        post = next(req for req, _ in opener.calls if req.method == "POST")
        args = json.loads(parse.parse_qs(post.data.decode())["commands"][0])[0]["args"]
        self.assertEqual(args["labels"], sorted(labels))
        self.assertEqual(args["priority"], 1)
        self.assertIs(args["auto_parse_labels"], False)
        self.assertEqual(plan["labels"], labels)

    def test_removed_label_never_creates_label_or_task(self):
        gateway, opener = self.gateway([USER, page(), page()])
        with self.assertRaisesRegex(todoist.PermanentTodoistError, "selected_label_unavailable"):
            gateway.create_or_reconcile({**PLAN, "labels": ["On_PC"]}, COMMAND, TEMP_ID, IDENTITY)
        self.assertTrue(all(req.method == "GET" for req, _ in opener.calls))

    def test_new_plan_receipt_requires_exact_labels_and_ordinary_priority(self):
        plan = {**PLAN, "labels": ["On_Phone", "10_min"]}
        for changes in ({"labels": []}, {"labels": ["On_Phone"]}, {"labels": ["On_Phone", "10_min", "GCal"]},
                        {"labels": ["On_Phone", "10_min", "10_min"]}, {"labels": ["On_Phone", True]},
                        {"labels": None}, {"labels": ["On_Phone", "10_min"], "priority": 4},
                        {"labels": ["On_Phone", "10_min"], "priority": True}):
            with self.subTest(changes=changes):
                gateway, opener = self.gateway([USER, task(**changes)])
                with self.assertRaisesRegex(todoist.RetryableTodoistError, "readback_mismatch"):
                    gateway.reconcile(plan, COMMAND, IDENTITY, external_id="synthetic-task")
                self.assertTrue(all(req.method == "GET" for req, _ in opener.calls))

    def test_old_frozen_unlabeled_plan_recovers_without_catalog_or_new_metadata(self):
        legacy = task()
        del legacy["labels"]
        del legacy["priority"]
        gateway, opener = self.gateway([USER, legacy])
        receipt = gateway.reconcile(PLAN, COMMAND, IDENTITY, external_id="synthetic-task")
        self.assertTrue(receipt["duplicate"])
        self.assertEqual(len(opener.calls), 2)
        self.assertTrue(all("/labels" not in req.full_url and req.method == "GET" for req, _ in opener.calls))
        gateway, _ = self.gateway([USER, task(labels=["On_Phone"])])
        with self.assertRaisesRegex(todoist.RetryableTodoistError, "readback_mismatch"):
            gateway.reconcile(PLAN, COMMAND, IDENTITY, external_id="synthetic-task")

    def test_invalid_label_plans_never_contact_provider(self):
        for labels in (None, "On_PC", ["on_pc"], ["GCal"], ["Someday"], ["unknown"], ["5_min", "10_min"], ["On_PC", "On_PC"], [False]):
            with self.subTest(labels=labels):
                gateway, opener = self.gateway([])
                with self.assertRaisesRegex(todoist.PermanentTodoistError, "invalid_reminder_labels"):
                    gateway.create_or_reconcile({**PLAN, "labels": labels}, COMMAND, TEMP_ID, IDENTITY)
                self.assertEqual(opener.calls, [])

    def test_callable_bearer_is_refreshed_per_request(self):
        gateway, opener = self.gateway([USER, USER])
        provider = mock.Mock(side_effect=["first-synthetic-token", "second-synthetic-token"])
        gateway = todoist.TodoistGateway(provider)
        gateway.identity()
        gateway.identity()
        self.assertEqual(provider.call_count, 2)
        self.assertEqual([req.get_header("Authorization") for req, _ in opener.calls],
                         ["Bearer first-synthetic-token", "Bearer second-synthetic-token"])

    def test_callable_bearer_errors_or_invalid_values_are_sanitized_without_network(self):
        for provider in (mock.Mock(side_effect=ValueError("sensitive auth bundle")), lambda: None,
                         lambda: "synthetic\nheader", lambda: {"token": "sensitive auth bundle"}):
            with self.subTest(provider=type(provider).__name__):
                gateway, opener = self.gateway([])
                gateway = todoist.TodoistGateway(provider)
                with self.assertRaises(todoist.PermanentTodoistError) as raised:
                    gateway.identity()
                self.assertNotIn("sensitive", str(raised.exception))
                self.assertIsNone(raised.exception.__cause__)
                self.assertEqual(opener.calls, [])

    def test_slow_bearer_cannot_dispatch_after_the_caller_deadline(self):
        release = threading.Event()
        finished = threading.Event()

        def bearer():
            release.wait(2)
            finished.set()
            return "synthetic-token"

        gateway, opener = self.gateway([])
        gateway = todoist.TodoistGateway(bearer, timeout=0.03)
        try:
            with self.assertRaises(todoist.RetryableTodoistError):
                gateway.identity()
        finally:
            release.set()
            self.assertTrue(finished.wait(1))
        self.assertEqual(opener.calls, [])

    def test_401_refreshes_future_expiring_credential_store_token_once(self):
        stamp = 2_000_000_000
        tokens = {"access_token": "synthetic-access-token", "refresh_token": "synthetic-refresh-token",
                  "token_type": "Bearer", "expires_in": 3600, "scope": "task:add data:read"}
        replacement = {**tokens, "access_token": "synthetic-new-access-token", "refresh_token": "synthetic-new-refresh-token"}
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            path = Path(directory) / "synthetic-credentials.json"
            path.write_text(json.dumps(auth._new_bundle("tdd_synthetic_personal_app", tokens, stamp)))
            path.chmod(0o600)
            store = auth.CredentialStore(path, now=lambda: stamp)
            _, opener = self.gateway([self.rejected(), USER])
            gateway = todoist.TodoistGateway(store.bearer)
            with mock.patch.object(auth, "_post", return_value=replacement) as refresh:
                self.assertEqual(gateway.identity(), IDENTITY)
            self.assertEqual(refresh.call_count, 1)
            self.assertEqual([req.get_header("Authorization") for req, _ in opener.calls],
                             ["Bearer synthetic-access-token", "Bearer synthetic-new-access-token"])
            self.assertEqual(json.loads(path.read_text())["access_token"], replacement["access_token"])

    def test_401_post_retry_keeps_exact_sync_bytes_uuid_url_and_deadline(self):
        provider = mock.Mock(side_effect=["old-token", "old-token", "old-token", "new-token", "new-token"])
        _, opener = self.gateway([USER, page(), self.rejected(), sync(), task()])
        gateway = todoist.TodoistGateway(provider)
        self.assertEqual(gateway.create_or_reconcile(PLAN, COMMAND, TEMP_ID, IDENTITY)["task_id"], "synthetic-task")
        posts = [(req, timeout) for req, timeout in opener.calls if req.method == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0][0].full_url, posts[1][0].full_url)
        self.assertEqual(posts[0][0].data, posts[1][0].data)
        self.assertEqual(json.loads(parse.parse_qs(posts[1][0].data.decode())["commands"][0])[0]["uuid"], COMMAND)
        self.assertEqual(posts[0][0].get_header("Authorization"), "Bearer old-token")
        self.assertEqual(posts[1][0].get_header("Authorization"), "Bearer new-token")
        self.assertLessEqual(posts[1][1], posts[0][1])
        self.assertEqual([call for call in provider.call_args_list if call.kwargs], [mock.call(rejected_token="old-token")])

    def test_second_401_stops_without_another_refresh_or_http_attempt(self):
        provider = mock.Mock(side_effect=["old-token", "new-token"])
        _, opener = self.gateway([self.rejected(), self.rejected()])
        gateway = todoist.TodoistGateway(provider)
        with self.assertRaisesRegex(todoist.PermanentTodoistError, "^todoist_authorization_required$") as raised:
            gateway.identity()
        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(provider.call_args_list, [mock.call(), mock.call(rejected_token="old-token")])
        self.assertEqual(len(opener.calls), 2)

    def test_failed_unsupported_or_unchanged_refresh_never_reuses_rejected_bearer(self):
        providers = ("static-token", lambda: "old-token",
                     mock.Mock(side_effect=["old-token", RuntimeError("sensitive auth bundle")]),
                     mock.Mock(side_effect=["old-token", "old-token"]),
                     mock.Mock(side_effect=["old-token", None]))
        for provider in providers:
            with self.subTest(provider=type(provider).__name__):
                _, opener = self.gateway([self.rejected()])
                gateway = todoist.TodoistGateway(provider)
                with self.assertRaises(todoist.PermanentTodoistError) as raised:
                    gateway.identity()
                self.assertNotIn("sensitive", str(raised.exception))
                self.assertIsNone(raised.exception.__cause__)
                self.assertEqual(len(opener.calls), 1)

    def test_uncertain_credential_refresh_remains_reserved_and_does_not_retry_api(self):
        stamp = 2_000_000_000
        tokens = {"access_token": "synthetic-access-token", "refresh_token": "synthetic-refresh-token",
                  "token_type": "Bearer", "expires_in": 3600, "scope": "task:add data:read"}
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            path = Path(directory) / "synthetic-credentials.json"
            path.write_text(json.dumps(auth._new_bundle("tdd_synthetic_personal_app", tokens, stamp)))
            path.chmod(0o600)
            store = auth.CredentialStore(path, now=lambda: stamp)
            _, opener = self.gateway([self.rejected()])
            gateway = todoist.TodoistGateway(store.bearer)
            with mock.patch.object(auth, "_post", side_effect=auth.ReauthorizationRequired("sensitive refresh failure")) as refresh:
                with self.assertRaisesRegex(todoist.PermanentTodoistError, "^todoist_authorization_required$"):
                    gateway.identity()
                with self.assertRaises(todoist.PermanentTodoistError):
                    gateway.identity()
            self.assertEqual(refresh.call_count, 1)
            self.assertEqual(len(opener.calls), 1)
            self.assertEqual(json.loads(path.read_text())["refresh_state"], "refreshing")

    def test_slow_401_refresh_cannot_dispatch_http_after_original_deadline(self):
        release, entered, finished = threading.Event(), threading.Event(), threading.Event()

        def bearer(*, rejected_token=None):
            if rejected_token is None:
                return "old-token"
            entered.set()
            release.wait(2)
            finished.set()
            return "new-token"

        _, opener = self.gateway([self.rejected()])
        gateway = todoist.TodoistGateway(bearer, timeout=0.03)
        try:
            with self.assertRaises(todoist.RetryableTodoistError):
                gateway.identity()
            self.assertTrue(entered.is_set())
        finally:
            release.set()
            self.assertTrue(finished.wait(1))
        self.assertEqual(len(opener.calls), 1)

    def test_non_401_transport_failure_does_not_refresh_or_retry(self):
        provider = mock.Mock(return_value="old-token")
        _, opener = self.gateway([error.URLError("sensitive uncertain POST")])
        gateway = todoist.TodoistGateway(provider)
        with self.assertRaises(todoist.RetryableTodoistError):
            gateway.identity()
        self.assertEqual(provider.call_args_list, [mock.call()])
        self.assertEqual(len(opener.calls), 1)

    def test_lost_ack_retries_same_uuid_and_same_temp_id(self):
        gateway, opener = self.gateway([USER, page(), error.URLError("private timeout"), USER, page(), sync(), task()])
        with self.assertRaises(todoist.RetryableTodoistError):
            gateway.create_or_reconcile(PLAN, COMMAND, TEMP_ID, IDENTITY)
        result = gateway.create_or_reconcile(PLAN, COMMAND, TEMP_ID, IDENTITY)
        posts = [call[0] for call in opener.calls if call[0].method == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0].data, posts[1].data)
        self.assertEqual(result["task_id"], "synthetic-task")

    def test_lost_ack_finds_marker_without_second_post(self):
        gateway, opener = self.gateway([USER, page([task()])])
        result = gateway.create_or_reconcile(PLAN, COMMAND, TEMP_ID, IDENTITY)
        self.assertTrue(result["duplicate"])
        self.assertTrue(all(req.method == "GET" for req, _ in opener.calls))

    def test_missing_temp_mapping_reconciles_marker(self):
        gateway, _ = self.gateway([USER, page(), sync(mapping=False), page([task()])])
        result = gateway.create_or_reconcile(PLAN, COMMAND, TEMP_ID, IDENTITY)
        self.assertEqual(result["task_id"], "synthetic-task")

    def test_ack_without_task_is_never_saved(self):
        gateway, _ = self.gateway([USER, page(), sync(mapping=False), page()])
        with self.assertRaisesRegex(todoist.RetryableTodoistError, "saved_task_not_verified"):
            gateway.create_or_reconcile(PLAN, COMMAND, TEMP_ID, IDENTITY)

    def test_mapping_is_persisted_before_readback_failure(self):
        gateway, _ = self.gateway([USER, page(), sync(), error.URLError("read failed")])
        mapping = []
        with self.assertRaises(todoist.RetryableTodoistError):
            gateway.create_or_reconcile(PLAN, COMMAND, TEMP_ID, IDENTITY, on_external_id=mapping.append)
        self.assertEqual(mapping, ["synthetic-task"])

    def test_existing_id_only_reads_and_never_reposts(self):
        gateway, opener = self.gateway([USER, task()])
        result = gateway.create_or_reconcile(PLAN, COMMAND, TEMP_ID, IDENTITY, external_id="synthetic-task")
        self.assertTrue(result["duplicate"])
        self.assertEqual([req.method for req, _ in opener.calls], ["GET", "GET"])

    def test_deleted_known_task_remains_uncertain_without_post(self):
        missing = error.HTTPError("https://api.todoist.com/private", 404, "private", {}, io.BytesIO(b"private"))
        gateway, opener = self.gateway([USER, missing])
        with self.assertRaisesRegex(todoist.RetryableTodoistError, "known_task_unavailable"):
            gateway.create_or_reconcile(PLAN, COMMAND, TEMP_ID, IDENTITY, external_id="synthetic-task")
        self.assertTrue(all(req.method == "GET" for req, _ in opener.calls))

    def test_readback_requires_exact_time_title_destination_marker_and_timezone(self):
        for changes in (
            {"content": "Different task"}, {"project_id": "other-inbox"}, {"description": "wrong marker"},
            {"user_id": "other-user"}, {"id": "different-id"}, {"checked": True}, {"is_deleted": True},
            {"deadline": {"date": "2099-10-30"}}, {"parent_id": "a-parent"}, {"section_id": "a-section"},
            {"due": {"date": "2099-10-30T20:00:00Z", "timezone": "America/Bogota", "is_recurring": False}},
            {"due": {"date": "2099-10-30T19:00:00Z", "timezone": None, "is_recurring": False}},
            {"due": {"date": "2099-10-30T19:00:00", "timezone": "America/Bogota", "is_recurring": False}},
            {"due": {"date": "2099-10-30", "timezone": "America/Bogota", "is_recurring": False}},
            {"due": {"date": "2099-10-30T19:00:00Z", "timezone": "America/Bogota", "is_recurring": True}},
        ):
            with self.subTest(changes=changes):
                gateway, _ = self.gateway([USER, task(**changes)])
                with self.assertRaises(todoist.RetryableTodoistError):
                    gateway.reconcile(PLAN, COMMAND, IDENTITY, external_id="synthetic-task")

    def test_identity_mismatch_never_posts(self):
        for user in ({**USER, "id": "other-user"}, {**USER, "inbox_project_id": "other-inbox"}, {**USER, "tz_info": {"timezone": "UTC"}}):
            with self.subTest(user=user):
                gateway, opener = self.gateway([user])
                with self.assertRaises(todoist.PermanentTodoistError):
                    gateway.create_or_reconcile(PLAN, COMMAND, TEMP_ID, IDENTITY)
                self.assertEqual(len(opener.calls), 1)

    def test_reconciliation_is_read_only_after_schedule_elapsed(self):
        past = copy.deepcopy(PLAN)
        past["due"]["start"] = "2020-10-30T14:00:00-05:00"
        due = {"date": "2020-10-30T19:00:00Z", "timezone": "America/Bogota", "is_recurring": False}
        gateway, opener = self.gateway([USER, page([task(due=due)])])
        self.assertTrue(gateway.reconcile(past, COMMAND, IDENTITY)["duplicate"])
        self.assertTrue(all(req.method == "GET" for req, _ in opener.calls))

    def test_elapsed_before_dispatch_never_posts(self):
        gateway, opener = self.gateway([USER, page()])
        with mock.patch.object(todoist, "dispatch_now", return_value=datetime.fromisoformat("2099-10-30T19:00:01+00:00")):
            with self.assertRaisesRegex(todoist.PermanentTodoistError, "due_elapsed"):
                gateway.create_or_reconcile(PLAN, COMMAND, TEMP_ID, IDENTITY)
        self.assertEqual(len(opener.calls), 2)

    def test_pagination_keeps_inbox_scope_and_opaque_cursor(self):
        cursor = "opaque/+?=cursor"
        gateway, opener = self.gateway([USER, page([{"id": "other-task"}], cursor), page([task()])])
        result = gateway.reconcile(PLAN, COMMAND, IDENTITY)
        self.assertEqual(result["task_id"], "synthetic-task")
        params = [parse.parse_qs(parse.urlsplit(req.full_url).query) for req, _ in opener.calls[1:]]
        self.assertEqual(params[0], {"project_id": ["synthetic-inbox"], "limit": ["100"]})
        self.assertEqual(params[1], {**params[0], "cursor": [cursor]})

    def test_repeated_or_excessive_pagination_fails_closed(self):
        for pages in ([page(cursor="repeat"), page(cursor="repeat")], [page(cursor=str(i)) for i in range(todoist.MAX_PAGES)]):
            with self.subTest(pages=len(pages)):
                gateway, opener = self.gateway([USER] + pages)
                with self.assertRaises(todoist.RetryableTodoistError):
                    gateway.create_or_reconcile(PLAN, COMMAND, TEMP_ID, IDENTITY)
                self.assertTrue(all(req.method == "GET" for req, _ in opener.calls))

    def test_duplicate_marker_candidates_fail_closed(self):
        gateway, _ = self.gateway([USER, page([task(), task(id="another-task")])])
        with self.assertRaisesRegex(todoist.RetryableTodoistError, "multiple_matching"):
            gateway.reconcile(PLAN, COMMAND, IDENTITY)

    def test_command_rejection_is_sanitized(self):
        gateway, _ = self.gateway([USER, page(), sync(status={"http_code": 400, "error": "private provider body"})])
        with self.assertRaisesRegex(todoist.PermanentTodoistError, "^todoist_command_rejected$"):
            gateway.create_or_reconcile(PLAN, COMMAND, TEMP_ID, IDENTITY)

    def test_missing_command_status_is_uncertain(self):
        gateway, _ = self.gateway([USER, page(), {}])
        with self.assertRaisesRegex(todoist.RetryableTodoistError, "command_unconfirmed"):
            gateway.create_or_reconcile(PLAN, COMMAND, TEMP_ID, IDENTITY)

    def test_http_and_transport_errors_never_expose_provider_content(self):
        for exc, kind in (
            (error.HTTPError("https://private.invalid/token", 401, "private-token", {}, io.BytesIO(b"private body")), todoist.PermanentTodoistError),
            (error.HTTPError("https://private.invalid/token", 500, "private-token", {}, io.BytesIO(b"private body")), todoist.RetryableTodoistError),
            (error.URLError("private-token"), todoist.RetryableTodoistError),
        ):
            with self.subTest(kind=kind):
                gateway, _ = self.gateway([exc])
                with self.assertRaises(kind) as raised:
                    gateway.identity()
                self.assertNotIn("private", str(raised.exception))

    def test_invalid_and_oversized_json_is_uncertain(self):
        for raw in (b"{private invalid json", b"[]", b"x" * (todoist.MAX_RESPONSE_BYTES + 1)):
            with self.subTest(size=len(raw)):
                gateway, opener = self.gateway([FakeResponse(None, raw=raw)])
                with self.assertRaises(todoist.RetryableTodoistError):
                    gateway.identity()
                self.assertTrue(opener.responses[0].closed)

    def test_total_timeout_bounds_stalled_response(self):
        release = threading.Event()
        gateway, opener = self.gateway([FakeResponse(USER, wait=release)], timeout=0.04)
        started = time.monotonic()
        try:
            with self.assertRaises(todoist.RetryableTodoistError):
                gateway.identity()
            self.assertLess(time.monotonic() - started, 0.3)
        finally:
            release.set()

    def test_validation_prevents_requests(self):
        gateway, opener = self.gateway([])
        for plan, command, temporary, identity in (
            ({**PLAN, "title": ""}, COMMAND, TEMP_ID, IDENTITY),
            ({**PLAN, "due": {"start": "2099-10-30", "timezone": "America/Bogota"}}, COMMAND, TEMP_ID, IDENTITY),
            ({**PLAN, "due": {"start": "2099-10-30T19:00:00Z", "timezone": "UTC"}}, COMMAND, TEMP_ID, IDENTITY),
            (PLAN, "new command please", TEMP_ID, IDENTITY),
            (PLAN, COMMAND, "https://other.invalid", IDENTITY),
            (PLAN, COMMAND, TEMP_ID, {**IDENTITY, "inbox_project_id": "../tasks"}),
        ):
            with self.subTest(command=command):
                with self.assertRaises(todoist.PermanentTodoistError):
                    gateway.create_or_reconcile(plan, command, temporary, identity)
        self.assertEqual(opener.calls, [])

    def test_gateway_never_accepts_nonfinite_timeout_or_header_whitespace(self):
        for token, timeout in (("", 5), ("secret\nheader", 5), ("synthetic", float("inf")), ("synthetic", 0), ("synthetic", True)):
            with self.subTest(timeout=timeout):
                with self.assertRaises(todoist.PermanentTodoistError):
                    todoist.TodoistGateway(token, timeout)


if __name__ == "__main__":
    unittest.main()
