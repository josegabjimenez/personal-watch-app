#!/usr/bin/env python3
from __future__ import annotations

import email.message
import importlib.util
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mobile_expense_capture.py"
SPEC = importlib.util.spec_from_file_location("mobile_expense_capture", SCRIPT)
assert SPEC and SPEC.loader
expense = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(expense)


CATALOG = {
    "currencies": [{"id": "1", "code": "COP"}, {"id": "2", "code": "USD"}],
    "accounts": [
        {"id": "1", "name": "Cuenta Ahorros Banco Bogotá", "type": "asset", "active": True, "currency_code": "COP"},
        {"id": "6", "name": "Nequi", "type": "asset", "active": True, "currency_code": "COP"},
        {"id": "4", "name": "Cash", "type": "asset", "active": True, "currency_code": "COP"},
        {"id": "7", "name": "Food", "type": "expense", "active": True},
        {"id": "8", "name": "Transport", "type": "expense", "active": True},
        {"id": "10", "name": "Home", "type": "expense", "active": True},
        {"id": "11", "name": "Subscriptions", "type": "expense", "active": True},
        {"id": "13", "name": "Health", "type": "expense", "active": True},
        {"id": "16", "name": "Other", "type": "expense", "active": True},
        {"id": "71", "name": "Mercado D1", "type": "expense", "active": True},
        {"id": "82", "name": "Cash expense", "type": "expense", "active": True},
    ],
    "categories": [
        {"id": "1", "name": "Food"},
        {"id": "2", "name": "Transport"},
        {"id": "4", "name": "Home"},
        {"id": "5", "name": "Subscriptions"},
        {"id": "7", "name": "Health"},
        {"id": "8", "name": "Other"},
    ],
    "budgets": [
        {"id": "2", "name": "Mercado", "active": True},
        {"id": "3", "name": "Transporte", "active": True},
        {"id": "4", "name": "Salud y Seguros", "active": True},
        {"id": "5", "name": "Suscripciones y Herramientas", "active": True},
        {"id": "8", "name": "Buffer Imprevistos", "active": True},
        {"id": "12", "name": "Comidas afuera", "active": True},
    ],
    "tags": [{"id": "1", "name": "Mercado"}],
}


def payload(
    text: str,
    request_id: str = "watch-expense-0001",
    operation_id: str = "exp_0123456789abcdef0123456789abcdef",
) -> dict:
    return {
        "request_id": request_id,
        "operation_id": operation_id,
        "created_at": "2026-08-30T21:00:00Z",
        "capture": {"text": text, "raw_text": text},
        "entities": {},
    }


class FakeGateway:
    def __init__(self, *, history=None, existing=None, create_error_after_commit=False):
        self.history = list(history or [])
        self.existing = list(existing or [])
        self.created = []
        self.catalog_calls = 0
        self.search_calls = 0
        self.create_error_after_commit = create_error_after_commit

    def load_catalog(self):
        self.catalog_calls += 1
        return CATALOG

    def search_transactions(self, _query):
        self.search_calls += 1
        return self.history

    def find_by_external_id(self, external_id, _date):
        return [row for row in self.existing + self.created if row.get("external_id") == external_id]

    def create_withdrawal(self, transaction):
        self.created.append(
            {
                **transaction,
                "journal_id": "9001",
                "group_id": "8001",
                "type": "withdrawal",
                "currency_code": "COP",
            }
        )
        if self.create_error_after_commit:
            raise expense.FireflyError("simulated_timeout")
        return self.created[-1]


def confirmed_payload(
    text: str,
    gateway: FakeGateway,
    *,
    request_id: str = "watch-expense-0001",
    operation_id: str = "exp_0123456789abcdef0123456789abcdef",
) -> tuple[dict, dict]:
    preview_body = payload(text, request_id=request_id, operation_id=operation_id)
    preview = expense.process_payload(preview_body, gateway, dry_run=True)
    commit_body = payload(text, request_id=request_id, operation_id=operation_id)
    commit_body.update(
        confirmed_plan=preview["plan"],
        expected_plan_hash=preview["plan_hash"],
    )
    return commit_body, preview


HISTORY_ALMUERZO = {
    "journal_id": "701",
    "type": "withdrawal",
                "currency_code": "COP",
    "description": "Almuerzos",
    "source_id": "4",
    "source_name": "Cash",
    "destination_id": "7",
    "destination_name": "Food",
    "category_id": "1",
    "category_name": "Food",
    "budget_id": "12",
    "budget_name": "Comidas afuera",
    "tags": [],
}


class MobileExpenseCaptureTests(unittest.TestCase):
    def test_money_parser_handles_colombian_forms_without_reading_tc_digit(self):
        self.assertEqual(expense.parse_amount_cop("45 mil en Uber"), 45_000)
        self.assertEqual(expense.parse_amount_cop("33.500 sandwiches"), 33_500)
        self.assertEqual(expense.parse_amount_cop("45,5 mil mercado"), 45_500)
        self.assertEqual(expense.parse_amount_cop("$ 120.000 veterinario"), 120_000)
        self.assertIsNone(expense.parse_amount_cop("tc1 almuerzo"))

    def test_card_fp_and_non_expense_flows_fail_before_firefly(self):
        for text in [
            "tc1 45 mil almuerzo",
            "fp 45 mil ropa",
            "futuro pago 80 mil mercado",
            "transferencia 45 mil a Nequi",
            "ingreso de 2 millones",
        ]:
            with self.subTest(text=text):
                gateway = FakeGateway()
                result = expense.process_payload(payload(text), gateway, dry_run=False)
                self.assertEqual(result["decision"], "needs_confirmation")
                self.assertEqual(result["transaction_kind"], "unsupported")
                self.assertEqual(gateway.catalog_calls, 0)
                self.assertEqual(gateway.created, [])

    def test_ambiguous_or_invalid_amount_never_queries_or_writes(self):
        for text in ["almuerzo", "45 mil y 20 mil mercado", "500 pesos pan", "999999999 mercado"]:
            with self.subTest(text=text):
                gateway = FakeGateway()
                result = expense.process_payload(payload(text), gateway, dry_run=False)
                self.assertEqual(result["decision"], "needs_confirmation")
                self.assertEqual(gateway.catalog_calls, 0)
                self.assertEqual(gateway.created, [])

    def test_history_reuse_keeps_metadata_but_uses_current_explicit_account(self):
        gateway = FakeGateway(history=[HISTORY_ALMUERZO])
        result = expense.process_payload(payload("45 mil almuerzo por Nequi"), gateway, dry_run=True)
        plan = result["plan"]
        self.assertEqual(result["decision"], "auto_write")
        self.assertEqual(plan["amount_cop"], 45_000)
        self.assertEqual(plan["source_name"], "Nequi")
        self.assertEqual(plan["source_id"], "6")
        self.assertEqual(plan["description"], "Almuerzos")
        self.assertEqual(plan["destination_id"], "7")
        self.assertEqual(plan["category_name"], "Food")
        self.assertEqual(plan["budget_name"], "Comidas afuera")
        self.assertEqual(gateway.created, [])

    def test_nike_normalizes_only_in_payment_account_context(self):
        gateway = FakeGateway(history=[HISTORY_ALMUERZO])
        result = expense.process_payload(payload("45 mil almuerzo por Nike"), gateway, dry_run=True)
        self.assertEqual(result["plan"]["source_name"], "Nequi")

        gateway = FakeGateway()
        result = expense.process_payload(payload("120 mil zapatos Nike"), gateway, dry_run=True)
        self.assertEqual(result["decision"], "needs_confirmation")
        self.assertNotEqual(result.get("plan", {}).get("source_name"), "Nequi")

    def test_default_account_is_banco_bogota_asset_id(self):
        gateway = FakeGateway(history=[HISTORY_ALMUERZO])
        result = expense.process_payload(payload("45 mil almuerzo"), gateway, dry_run=True)
        self.assertEqual(result["plan"]["source_name"], "Cuenta Ahorros Banco Bogotá")
        self.assertEqual(result["plan"]["source_id"], "1")

    def test_known_mapping_requires_existing_category_budget_and_destination(self):
        gateway = FakeGateway()
        result = expense.process_payload(payload("80 mil mercado D1"), gateway, dry_run=True)
        plan = result["plan"]
        self.assertEqual(result["decision"], "auto_write")
        self.assertEqual(plan["description"], "Mercado D1")
        self.assertEqual(plan["destination_id"], "71")
        self.assertEqual(plan["category_name"], "Home")
        self.assertEqual(plan["budget_name"], "Mercado")
        self.assertEqual(plan["tags"], ["Mercado"])

    def test_reviewed_mapping_resolves_ambiguous_history_without_guessing_ids(self):
        conflicting = [
            {
                **HISTORY_ALMUERZO,
                "journal_id": "801",
                "description": "Mercado D1",
                "destination_id": "71",
                "destination_name": "Mercado D1",
                "category_id": "4",
                "category_name": "Home",
                "budget_id": "2",
                "budget_name": "Mercado",
            },
            {
                **HISTORY_ALMUERZO,
                "journal_id": "802",
                "description": "Mercado D1",
                "destination_id": "7",
                "destination_name": "Food",
                "category_id": "1",
                "category_name": "Food",
                "budget_id": "12",
                "budget_name": "Comidas afuera",
            },
        ]
        gateway = FakeGateway(history=conflicting)
        result = expense.process_payload(payload("80 mil mercado D1"), gateway, dry_run=True)
        self.assertEqual(result["decision"], "auto_write")
        self.assertEqual(result["plan"]["destination_id"], "71")
        self.assertEqual(result["plan"]["category_name"], "Home")
        self.assertEqual(result["plan"]["budget_name"], "Mercado")
        self.assertEqual(result["plan"]["evidence"], "reviewed_mapping")

    def test_unknown_concept_needs_confirmation_and_does_not_write(self):
        gateway = FakeGateway()
        result = expense.process_payload(payload("45 mil objeto misterioso"), gateway, dry_run=True)
        self.assertEqual(result["decision"], "needs_confirmation")
        self.assertIn("categor", result["question"].lower())
        self.assertEqual(gateway.created, [])

    def test_dry_run_never_calls_create(self):
        gateway = FakeGateway(history=[HISTORY_ALMUERZO])
        result = expense.process_payload(payload("45 mil almuerzo"), gateway, dry_run=True)
        self.assertEqual(result["decision"], "auto_write")
        self.assertFalse(result["write_committed"])
        self.assertEqual(gateway.created, [])

    def test_live_write_creates_exactly_one_id_bound_withdrawal(self):
        gateway = FakeGateway(history=[HISTORY_ALMUERZO])
        live_payload, _preflight = confirmed_payload("45 mil almuerzo", gateway)
        result = expense.process_payload(live_payload, gateway, dry_run=False)
        self.assertEqual(result["decision"], "committed")
        self.assertTrue(result["write_committed"])
        self.assertEqual(result["journal_ids"], ["9001"])
        self.assertEqual(len(gateway.created), 1)
        created = gateway.created[0]
        self.assertEqual(created["type"], "withdrawal")
        self.assertEqual(created["external_id"], "hermes-watch:exp_0123456789abcdef0123456789abcdef")
        self.assertEqual(created["source_id"], "1")
        self.assertEqual(created["category_id"], "1")
        self.assertEqual(created["budget_id"], "12")

    def test_same_external_id_returns_duplicate_without_second_create(self):
        existing = {
            **HISTORY_ALMUERZO,
            "journal_id": "777",
            "group_id": "666",
            "amount": "45000",
            "date": "2026-08-30",
            "source_id": "1",
            "source_name": "Cuenta Ahorros Banco Bogotá",
            "external_id": "hermes-watch:exp_0123456789abcdef0123456789abcdef",
        }
        gateway = FakeGateway(history=[HISTORY_ALMUERZO], existing=[existing])
        live_payload, _preflight = confirmed_payload("45 mil almuerzo", gateway)
        result = expense.process_payload(live_payload, gateway, dry_run=False)
        self.assertEqual(result["decision"], "duplicate")
        self.assertTrue(result["write_committed"])
        self.assertEqual(result["journal_ids"], ["777"])
        self.assertEqual(gateway.created, [])

    def test_post_timeout_recovers_committed_external_id_without_retrying(self):
        gateway = FakeGateway(history=[HISTORY_ALMUERZO], create_error_after_commit=True)
        live_payload, _preflight = confirmed_payload("45 mil almuerzo", gateway)
        result = expense.process_payload(live_payload, gateway, dry_run=False)
        self.assertEqual(result["decision"], "committed")
        self.assertTrue(result["recovered_after_error"])
        self.assertEqual(len(gateway.created), 1)

    def test_plan_hash_change_fails_closed_without_create(self):
        gateway = FakeGateway(history=[HISTORY_ALMUERZO])
        live_payload, _preflight = confirmed_payload("45 mil almuerzo", gateway)
        live_payload["expected_plan_hash"] = "0" * 64
        result = expense.process_payload(live_payload, gateway, dry_run=False)
        self.assertEqual(result["decision"], "needs_confirmation")
        self.assertEqual(result["error"], "plan_changed")
        self.assertEqual(gateway.created, [])

    def test_confirmed_plan_rejects_out_of_range_amount(self):
        gateway = FakeGateway(history=[HISTORY_ALMUERZO])
        live_payload, _preflight = confirmed_payload("45 mil almuerzo", gateway)
        oversized_plan = dict(live_payload["confirmed_plan"])
        oversized_plan["amount_cop"] = expense.MAX_AMOUNT_COP + 1
        live_payload["confirmed_plan"] = oversized_plan
        live_payload["expected_plan_hash"] = expense.compute_plan_hash(oversized_plan)

        result = expense.process_payload(live_payload, gateway, dry_run=False, mode="commit")

        self.assertEqual(result["decision"], "needs_confirmation")
        self.assertEqual(result["error"], "invalid_confirmed_plan")
        self.assertEqual(gateway.created, [])

    def test_duplicate_external_id_collision_fails_closed(self):
        duplicate = {
            **HISTORY_ALMUERZO,
            "journal_id": "777",
            "amount": "45000",
            "date": "2026-08-30",
            "source_id": "1",
            "external_id": "hermes-watch:exp_0123456789abcdef0123456789abcdef",
        }
        gateway = FakeGateway(history=[HISTORY_ALMUERZO], existing=[duplicate, {**duplicate, "journal_id": "778"}])
        live_payload, _preflight = confirmed_payload("45 mil almuerzo", gateway)
        result = expense.process_payload(live_payload, gateway, dry_run=False)
        self.assertEqual(result["decision"], "needs_confirmation")
        self.assertEqual(result["error"], "idempotency_collision")
        self.assertEqual(gateway.created, [])

    def test_idempotency_lookup_uses_server_side_external_id_filter(self):
        calls = []
        gateway = object.__new__(expense.FireflyGateway)

        def fake_request(method, path, *, payload=None, params=None):
            calls.append((method, path, payload, params))
            return {"data": [], "meta": {"pagination": {"total_pages": 1}}}

        gateway._request = fake_request
        self.assertEqual(
            gateway.find_by_external_id(
                "hermes-watch:exp_0123456789abcdef0123456789abcdef",
                "2026-08-30",
            ),
            [],
        )
        self.assertEqual(
            calls[0][3]["external_id"],
            "hermes-watch:exp_0123456789abcdef0123456789abcdef",
        )
        self.assertNotIn("start", calls[0][3])
        self.assertNotIn("end", calls[0][3])

    def test_external_id_lookup_exhausts_every_reported_page(self):
        calls = []
        target = "hermes-watch:exp_0123456789abcdef0123456789abcdef"
        gateway = object.__new__(expense.FireflyGateway)

        def fake_request(method, path, *, payload=None, params=None):
            self.assertIsNotNone(params)
            assert params is not None
            calls.append((method, path, payload, dict(params)))
            page = params["page"]
            transactions = []
            if page == 3:
                transactions = [{"transaction_journal_id": "777", "external_id": target}]
            return {
                "data": [{"id": f"group-{page}", "attributes": {"transactions": transactions}}],
                "meta": {"pagination": {"total_pages": 3}},
            }

        gateway._request = fake_request
        matches = gateway.find_by_external_id(target, "2026-08-30")
        self.assertEqual([call[3]["page"] for call in calls], [1, 2, 3])
        self.assertEqual(matches[0]["transaction_journal_id"], "777")

    def test_confirmed_plan_reconciliation_does_not_rebuild_dynamic_plan(self):
        planning_gateway = FakeGateway(history=[HISTORY_ALMUERZO])
        planned = expense.process_payload(payload("45 mil almuerzo"), planning_gateway, dry_run=True)
        existing = {
            **HISTORY_ALMUERZO,
            "journal_id": "777",
            "group_id": "666",
            "amount": "45000",
            "date": planned["plan"]["date"],
            "source_id": planned["plan"]["source_id"],
            "external_id": planned["external_id"],
        }
        gateway = FakeGateway(existing=[existing])
        confirmed = payload("texto que ya no debe reinterpretarse")
        confirmed.update(
            confirmed_plan=planned["plan"],
            expected_plan_hash=planned["plan_hash"],
        )
        result = expense.process_payload(confirmed, gateway, dry_run=False, mode="reconcile")
        self.assertEqual(result["decision"], "duplicate")
        self.assertEqual(result["operation_id"], confirmed["operation_id"])
        self.assertEqual(result["external_id"], planned["external_id"])
        self.assertEqual(gateway.catalog_calls, 0)
        self.assertEqual(gateway.search_calls, 0)
        self.assertEqual(gateway.created, [])

    def test_post_failure_without_canonical_readback_is_explicitly_uncertain(self):
        class FailingGateway(FakeGateway):
            def create_withdrawal(self, transaction):
                self.created.append(dict(transaction))
                raise expense.FireflyError("simulated_failure")

            def find_by_external_id(self, external_id, _date):
                return []

        gateway = FailingGateway(history=[HISTORY_ALMUERZO])
        preflight = expense.process_payload(payload("45 mil almuerzo"), gateway, dry_run=True)
        confirmed = payload("45 mil almuerzo")
        confirmed.update(
            confirmed_plan=preflight["plan"],
            expected_plan_hash=preflight["plan_hash"],
        )
        result = expense.process_payload(confirmed, gateway, dry_run=False, mode="commit")
        self.assertEqual(result["decision"], "uncertain")
        self.assertTrue(result["post_attempted"])
        self.assertEqual(result["operation_id"], confirmed["operation_id"])
        self.assertEqual(len(gateway.created), 1)

    def test_external_id_match_with_changed_type_or_tags_fails_closed(self):
        baseline = {
            **HISTORY_ALMUERZO,
            "journal_id": "777",
            "group_id": "666",
            "amount": "45000",
            "date": "2026-08-30",
            "source_id": "1",
            "source_name": "Cuenta Ahorros Banco Bogotá",
            "external_id": "hermes-watch:exp_0123456789abcdef0123456789abcdef",
        }
        for changed in (
            {**baseline, "type": "deposit"},
            {**baseline, "tags": ["Unexpected"]},
            {**baseline, "tags": "Unexpected"},
            {**baseline, "amount": "45000.99"},
        ):
            with self.subTest(changed=changed):
                gateway = FakeGateway(history=[HISTORY_ALMUERZO], existing=[changed])
                live_payload, _preflight = confirmed_payload("45 mil almuerzo", gateway)
                result = expense.process_payload(live_payload, gateway, dry_run=False)
                self.assertEqual(result["decision"], "needs_confirmation")
                self.assertEqual(result["error"], "idempotency_conflict")
                self.assertEqual(gateway.created, [])

    def test_firefly_token_can_be_loaded_from_private_file(self):
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "firefly-token.txt"
            token_path.write_text("private-test-token\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "FIREFLY_BASE_URL": "https://firefly.example.invalid",
                    "FIREFLY_API_TOKEN": "",
                    "FIREFLY_API_TOKEN_FILE": str(token_path),
                },
                clear=False,
            ):
                gateway = expense.FireflyGateway()
        self.assertEqual(gateway.base_url, "https://firefly.example.invalid")
        self.assertEqual(gateway.token, "private-test-token")

    def test_firefly_gateway_rejects_unsafe_base_urls_before_transport(self):
        unsafe_urls = (
            "http://firefly.example.invalid",
            "ftp://firefly.example.invalid",
            "https://user:password@firefly.example.invalid",
            "https://firefly.example.invalid?token=leak",
            "https://firefly.example.invalid#fragment",
        )
        for base_url in unsafe_urls:
            with self.subTest(base_url=base_url):
                with self.assertRaisesRegex(expense.FireflyError, "firefly_unsafe_base_url"):
                    expense.FireflyGateway(base_url=base_url, token="unit-test-token")

        self.assertEqual(
            expense.FireflyGateway(
                base_url="http://127.0.0.1:8090/",
                token="unit-test-token",
            ).base_url,
            "http://127.0.0.1:8090",
        )
        self.assertEqual(
            expense.FireflyGateway(
                base_url="https://firefly.example.invalid/subpath/",
                token="unit-test-token",
            ).base_url,
            "https://firefly.example.invalid/subpath",
        )

    def test_firefly_request_rejects_empty_or_non_object_json(self):
        class FakeResponse:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return self.body

        gateway = expense.FireflyGateway(
            base_url="https://firefly.example.invalid",
            token="unit-test-token",
        )
        for body in (b"", json.dumps([]).encode(), json.dumps("ok").encode()):
            with self.subTest(body=body):
                with mock.patch.object(
                    expense,
                    "open_url_without_redirects",
                    return_value=FakeResponse(body),
                ):
                    with self.assertRaisesRegex(expense.FireflyError, "firefly_unexpected_response"):
                        gateway._request("GET", "/api/v1/accounts")

    def test_firefly_redirect_is_not_followed(self):
        gateway = expense.FireflyGateway(
            base_url="https://firefly.example.invalid",
            token="unit-test-token",
        )
        headers = email.message.Message()
        headers["Location"] = "https://attacker.invalid/steal"
        redirect = urllib.error.HTTPError(
            "https://firefly.example.invalid/api/v1/accounts",
            302,
            "Found",
            headers,
            None,
        )
        with mock.patch.object(expense, "open_url_without_redirects", side_effect=redirect) as opener:
            with self.assertRaisesRegex(expense.FireflyError, "firefly_http_302"):
                gateway._request("GET", "/api/v1/accounts")
        opener.assert_called_once()

    def test_external_id_lookup_rejects_malformed_success_shapes(self):
        malformed = (
            {},
            {"data": {}},
            {"data": [None]},
            {"data": [{}]},
            {"data": [{"id": "1", "attributes": {}}]},
            {"data": [{"id": "1", "attributes": {"transactions": [None]}}]},
        )
        for response in malformed:
            with self.subTest(response=response):
                gateway = object.__new__(expense.FireflyGateway)
                gateway._request = mock.Mock(return_value=response)
                with self.assertRaisesRegex(expense.FireflyError, "firefly_unexpected_response"):
                    gateway.find_by_external_id(
                        "hermes-watch:exp_0123456789abcdef0123456789abcdef",
                        "2026-08-30",
                    )

    def test_operation_identity_survives_new_delivery_request_ids_and_replays(self):
        operation_id = "exp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        gateway = FakeGateway(history=[HISTORY_ALMUERZO])
        preview_payload = payload(
            "45 mil almuerzo",
            request_id="preview-request-0001",
            operation_id=operation_id,
        )
        preview = expense.process_payload(preview_payload, gateway, dry_run=True)

        confirm_payload = payload(
            "45 mil almuerzo",
            request_id="confirm-request-0002",
            operation_id=operation_id,
        )
        confirm_payload.update(
            confirmed_plan=preview["plan"],
            expected_plan_hash=preview["plan_hash"],
        )
        committed = expense.process_payload(confirm_payload, gateway, dry_run=False)

        replay_payload = payload(
            "45 mil almuerzo",
            request_id="retry-request-0003",
            operation_id=operation_id,
        )
        replay_payload.update(
            confirmed_plan=preview["plan"],
            expected_plan_hash=preview["plan_hash"],
        )
        duplicate = expense.process_payload(replay_payload, gateway, dry_run=False)

        self.assertEqual(preview["plan"]["operation_id"], operation_id)
        self.assertEqual(preview["plan_hash"], committed["plan_hash"])
        self.assertEqual(committed["decision"], "committed")
        self.assertEqual(duplicate["decision"], "duplicate")
        self.assertEqual(len(gateway.created), 1)
        self.assertEqual(
            gateway.created[0]["external_id"],
            "hermes-watch:exp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )

    def test_plan_hash_is_bound_to_operation_not_delivery_request(self):
        operation_id = "exp_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        first = expense.process_payload(
            payload("45 mil almuerzo", request_id="delivery-request-0001", operation_id=operation_id),
            FakeGateway(history=[HISTORY_ALMUERZO]),
            dry_run=True,
        )
        second = expense.process_payload(
            payload("45 mil almuerzo", request_id="delivery-request-0002", operation_id=operation_id),
            FakeGateway(history=[HISTORY_ALMUERZO]),
            dry_run=True,
        )
        self.assertEqual(first["plan_hash"], second["plan_hash"])

    def test_missing_or_malformed_operation_id_fails_before_firefly(self):
        for operation_id in ["", "watch-expense-0001", "exp_not-hex"]:
            with self.subTest(operation_id=operation_id):
                gateway = FakeGateway(history=[HISTORY_ALMUERZO])
                result = expense.process_payload(
                    payload("45 mil almuerzo", operation_id=operation_id),
                    gateway,
                    dry_run=False,
                )
                self.assertEqual(result["decision"], "needs_confirmation")
                self.assertEqual(result["error"], "invalid_operation_id")
                self.assertEqual(gateway.catalog_calls, 0)
                self.assertEqual(gateway.created, [])


if __name__ == "__main__":
    unittest.main()
