"""Offline durable adapter tests against exact extracted legacy helper nodes."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, unquote, urlparse

import grocery_helper_adapter as adapter
from fixtures import grocery_helper_contract as contract


def block(block_id, text, *, kind="to_do", parent="synthetic-others", checked=False):
    value = {
        "object": "block", "id": block_id, "type": kind, "has_children": kind == "toggle",
        "parent": {"type": "page_id" if parent == "synthetic-official-list" else "block_id",
                   "page_id" if parent == "synthetic-official-list" else "block_id": parent},
        "archived": False, "in_trash": False,
        kind: {"rich_text": [{"type": "text", "text": {"content": text}, "plain_text": text}]},
    }
    if kind == "to_do":
        value[kind]["checked"] = checked
    return value


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self):
        return json.dumps(self.value, ensure_ascii=False).encode()


class FakeNotion:
    def __init__(self):
        self.blocks = {}
        self.children = {"synthetic-official-list": [], "synthetic-others": []}
        self.calls = []
        self.writes = []
        self.after_mutation_error = None
        self.fail_readback = False
        self.bad_cursor = False
        self.before_mutation = None
        self.add(block("synthetic-others", "Otros", kind="toggle", parent="synthetic-official-list"))

    def add(self, value):
        self.blocks[value["id"]] = value
        parent = value["parent"][value["parent"]["type"]]
        self.children.setdefault(parent, []).append(value["id"])
        if value.get("has_children"):
            self.children.setdefault(value["id"], [])

    def open(self, req, timeout):
        parsed = urlparse(req.full_url)
        assert parsed.scheme == "https" and parsed.netloc == "api.notion.com"
        assert timeout == 45
        assert req.get_header("Authorization") == "Bearer synthetic-token"
        assert req.get_header("Notion-version") == "2025-09-03"
        method, path = req.get_method(), parsed.path.removeprefix("/v1")
        body = json.loads(req.data) if req.data else None
        self.calls.append((method, path, parse_qs(parsed.query), body))
        assert path.startswith("/blocks/"), "cross-domain request"
        parts = path.split("/")
        resource = unquote(parts[2])
        if method == "GET" and path.endswith("/children"):
            query = parse_qs(parsed.query)
            assert query["page_size"] == ["100"]
            offset = int(query.get("start_cursor", ["0"])[0])
            ids = self.children.get(resource, [])
            selected = ids[offset:offset + 2]
            more = offset + 2 < len(ids)
            cursor = str(offset + 2) if more else None
            if self.bad_cursor and resource == "synthetic-official-list":
                more, cursor = True, None
            return Response({"object": "list", "results": [copy.deepcopy(self.blocks[key]) for key in selected],
                             "has_more": more, "next_cursor": cursor})
        if method == "GET":
            if self.fail_readback:
                raise TimeoutError("synthetic readback timeout")
            return Response(copy.deepcopy(self.blocks[resource]))
        assert method == "PATCH"
        if self.before_mutation:
            self.before_mutation()
        self.writes.append((method, path, body))
        if path.endswith("/children"):
            assert len(body["children"]) == 1
            child = body["children"][0]
            assert child["type"] == "to_do" and child["to_do"]["checked"] is False
            text = child["to_do"]["rich_text"][0]["text"]["content"]
            created = block("synthetic-created-" + str(len(self.writes)), text, parent=resource)
            self.add(created)
            result = {"object": "list", "results": [copy.deepcopy(created)]}
        else:
            assert body == {"to_do": {"checked": False}}
            self.blocks[resource]["to_do"]["checked"] = False
            result = copy.deepcopy(self.blocks[resource])
        if self.after_mutation_error:
            raise self.after_mutation_error
        return Response(result)


class GroceryAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.source = contract.source()
        self.helper_path = self.directory / "synthetic_helper.py"
        self.helper_path.write_text(self.source)
        self.journal = self.directory / "operations.sqlite3"
        self.fake = FakeNotion()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(adapter, "PINNED_HELPER_SHA256", hashlib.sha256(self.source.encode()).hexdigest()).start()
        mock.patch.dict("os.environ", {"AURA_CAPTURE_HOME": str(self.directory / "fake-home"),
                                      "NOTION_API_TOKEN": "synthetic-token"}, clear=True).start()
        mock.patch("urllib.request.urlopen", side_effect=self.fake.open).start()
        self.gateway = adapter.GroceryAdapter(self.helper_path, self.journal)

    def plan(self, item="leche"):
        return self.gateway.prepare([item])["items"][0]

    def execute(self, item="leche", operation="parent:1"):
        plan = self.plan(item)
        child = adapter.child_operation_id(operation, plan)
        return self.gateway.execute(child, plan), child, plan

    def semantic(self, *selections):
        return self.gateway.prepare_semantic(list(selections), self.gateway.catalog())["items"]

    def register_semantic(self, *selections, parent="semantic-parent"):
        plans = self.semantic(*selections)
        operations = [(adapter.child_operation_id(parent, plan), plan) for plan in plans]
        self.gateway.register_operations(operations)
        return operations

    def test_semantic_catalog_is_complete_bounded_and_contains_only_exact_todos(self):
        self.fake.add(block("synthetic-milk", "Leche Colanta", checked=True))
        self.fake.add(block("synthetic-nested-toggle", "Lácteos", kind="toggle", parent="synthetic-official-list"))
        self.fake.add(block("synthetic-yogurt", "yogur", parent="synthetic-nested-toggle"))
        self.fake.add(block("synthetic-paragraph", "unrelated", kind="paragraph"))
        self.fake.add(block("synthetic-another-paragraph", "unrelated", kind="paragraph"))
        self.assertEqual(self.gateway.catalog(), [
            {"id": "synthetic-milk", "text": "Leche Colanta", "checked": True, "parent_id": "synthetic-others"},
            {"id": "synthetic-yogurt", "text": "yogur", "checked": False, "parent_id": "synthetic-nested-toggle"},
        ])
        self.assertTrue(any(call[2].get("start_cursor") for call in self.fake.calls))
        self.assertEqual(self.fake.writes, [])

    def test_semantic_catalog_refuses_overflow_without_silently_truncating(self):
        for index in range(201):
            self.fake.add(block(f"synthetic-item-{index}", f"producto {index}"))
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            self.gateway.catalog()
        self.assertEqual(caught.exception.code, "catalog_too_large")
        self.assertTrue(caught.exception.needs_input)
        self.assertEqual(self.fake.writes, [])

    def test_semantic_catalog_rejects_invalid_archived_oversize_or_unreadable_candidates(self):
        original = block("synthetic-bad", "leche")
        for field, value in (("archived", True), ("in_trash", True), ("to_do", {"checked": "false", "rich_text": []}),
                             ("to_do", {"checked": False, "rich_text": [{"plain_text": 123}]}),
                             ("to_do", {"checked": False, "rich_text": [{"plain_text": "x" * 1801}]})):
            with self.subTest(field=field, value=str(value)[:80]):
                self.fake.blocks["synthetic-bad"] = copy.deepcopy(original)
                self.fake.children["synthetic-others"] = ["synthetic-bad"]
                self.fake.blocks["synthetic-bad"][field] = value
                with self.assertRaises(adapter.GroceryAdapterError) as caught:
                    self.gateway.catalog()
                self.assertTrue(caught.exception.needs_input)
        self.fake.blocks["synthetic-bad"] = copy.deepcopy(original)
        self.fake.blocks["synthetic-bad"]["id"] = "../bad"
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            self.gateway.catalog()
        self.assertTrue(caught.exception.needs_input)
        self.assertEqual(self.fake.writes, [])

    def test_semantic_selected_id_retains_exact_title_and_handles_plural_accent_synonym_and_typo(self):
        for requested, existing in (("tomates", "tomate"), ("bananas", "banano"), ("patata", "papa"),
                                    ("limones", "limón"), ("lece", "- 1 litro leche = 5.000")):
            with self.subTest(requested=requested):
                self.fake.blocks["synthetic-selected"] = block("synthetic-selected", existing)
                self.fake.children["synthetic-others"] = ["synthetic-selected"]
                plan = self.semantic({"text": requested, "candidate_id": "synthetic-selected"})[0]
                self.assertEqual(plan["version"], 2)
                self.assertEqual(plan["action"], "already_present")
                self.assertEqual(plan["expected_text"], existing)
                self.assertFalse(plan["expected_checked"])
                result = self.gateway.execute("semantic-existing-" + requested, plan)
                self.assertEqual(result["status"], "committed")
                self.assertEqual(result["receipt"]["item"], existing)
                self.assertEqual(result["receipt"]["block_id"], "synthetic-selected")
                self.assertEqual(result["receipt"]["requested_item"], requested)
                self.assertFalse(result["write_committed"])
        self.assertEqual(self.fake.writes, [])

    def test_semantic_lactose_equivalence_preserves_other_diet_brand_and_variety_constraints(self):
        for request, existing in (("leche sin lactosa", "leche deslactosada"),
                                  ("leche deslactosado", "leche sin lactosa"),
                                  ("leche sin lactosa colanta", "leche colanta deslactosada")):
            with self.subTest(request=request, existing=existing):
                self.fake.blocks["synthetic-milk"] = block("synthetic-milk", existing)
                self.fake.children["synthetic-others"] = ["synthetic-milk"]
                plan = self.semantic({"text": request, "candidate_id": "synthetic-milk"})[0]
                self.assertEqual(plan["action"], "already_present")
                self.assertEqual(plan["expected_text"], existing)
        for request in ("leche light", "leche descremada", "leche semidescremada", "leche vegetal",
                        "leche deslactosada alqueria", "leche deslactosada entera"):
            with self.subTest(request=request):
                with self.assertRaises(adapter.GroceryAdapterError) as caught:
                    self.semantic({"text": request, "candidate_id": "synthetic-milk"})
                self.assertEqual(caught.exception.code, "constraint_conflict")
        self.assertEqual(self.fake.writes, [])

    def test_semantic_unknown_brand_qualifiers_are_never_plural_stemmed(self):
        for request, existing in (("leche Parmalat", "leche Parmalats"),
                                  ("champú Head & Shoulders", "champú Head & Shoulder"),
                                  ("leche MarcaSol", "leche MarcaSols"),
                                  ("MarcaSol", "MarcaSols")):
            with self.subTest(request=request, existing=existing):
                self.fake.blocks["synthetic-brand"] = block("synthetic-brand", existing)
                self.fake.children["synthetic-others"] = ["synthetic-brand"]
                with self.assertRaises(adapter.GroceryAdapterError) as caught:
                    self.semantic({"text": request, "candidate_id": "synthetic-brand"})
                self.assertEqual(caught.exception.code, "constraint_conflict")
        self.fake.blocks["synthetic-brand"] = block("synthetic-brand", "tomate chonto")
        plan = self.semantic({"text": "tomates chontos", "candidate_id": "synthetic-brand"})[0]
        self.assertEqual(plan["action"], "already_present")
        self.assertEqual(self.fake.writes, [])

    def test_semantic_unknown_or_invented_candidate_is_never_created_or_rematched(self):
        self.fake.add(block("synthetic-milk", "leche"))
        for candidate_id in ("synthetic-unknown", "../bad", 17, None):
            with self.subTest(candidate_id=candidate_id):
                with self.assertRaises(adapter.GroceryAdapterError) as caught:
                    self.semantic({"text": "lece", "candidate_id": candidate_id})
                self.assertEqual(caught.exception.code, "invalid_candidate")
        self.assertEqual(self.fake.writes, [])

    def test_semantic_constraints_and_quantity_conflicts_abort_whole_batch(self):
        cases = (("leche alqueria", "leche colanta", "constraint_conflict"),
                 ("leche deslactosada", "leche entera", "constraint_conflict"),
                 ("leche sin lactosa", "leche", "constraint_conflict"),
                 ("arroz integral", "arroz blanco", "constraint_conflict"),
                 ("leche 1 litro", "leche 2 litros", "constraint_conflict"),
                 ("tomate chonto", "tomate de árbol", "constraint_conflict"),
                 ("galletas oreo", "galletas festival", "constraint_conflict"),
                 ("servilletas", "servilletero", "constraint_conflict"),
                 ("pollo", "polvo", "constraint_conflict"),
                 ("coco", "coca", "constraint_conflict"),
                 ("colanta", "colanto", "constraint_conflict"),
                 ("leche sin azucar con grasa", "leche con azucar sin grasa", "constraint_conflict"),
                 ("galletas chocolate negro relleno blanco", "galletas chocolate blanco relleno negro", "constraint_conflict"),
                 ("leche no deslactosada entera", "leche deslactosada no entera", "constraint_conflict"),
                 ("2 kg arroz", "1 kg arroz", "quantity_conflict"),
                 ("un litro y medio de leche", "leche", "quantity_conflict"))
        for request, existing, code in cases:
            with self.subTest(request=request, existing=existing):
                self.fake.blocks["synthetic-selected"] = block("synthetic-selected", existing)
                self.fake.children["synthetic-others"] = ["synthetic-selected"]
                with self.assertRaises(adapter.GroceryAdapterError) as caught:
                    self.semantic({"text": "aguacate", "candidate_id": None},
                                  {"text": request, "candidate_id": "synthetic-selected"})
                self.assertEqual(caught.exception.code, code)
                self.assertTrue(caught.exception.needs_input)
        with sqlite3.connect(self.journal) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM grocery_operations").fetchone()[0], 0)
        self.assertEqual(self.fake.writes, [])

    def test_semantic_bare_product_variants_and_ambiguous_head_typo_require_clarification(self):
        self.fake.add(block("synthetic-whole", "leche entera"))
        self.fake.add(block("synthetic-light", "leche deslactosada"))
        for candidate_id in ("synthetic-whole", "synthetic-light", None):
            with self.assertRaises(adapter.GroceryAdapterError) as caught:
                self.semantic({"text": "leche", "candidate_id": candidate_id})
            self.assertEqual(caught.exception.code, "ambiguous_item")
        self.fake.blocks["synthetic-light"]["to_do"]["rich_text"][0]["plain_text"] = "lece entera"
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            self.semantic({"text": "lece entera", "candidate_id": "synthetic-whole"})
        self.assertEqual(caught.exception.code, "ambiguous_item")
        self.assertEqual(self.fake.writes, [])

    def test_semantic_bought_candidate_preserves_status_and_requires_clarification(self):
        self.fake.add(block("synthetic-bought", "huevos", checked=True))
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            self.semantic({"text": "huevo", "candidate_id": "synthetic-bought"})
        self.assertEqual(caught.exception.code, "bought_candidate")
        self.assertTrue(caught.exception.needs_input)
        self.assertTrue(self.fake.blocks["synthetic-bought"]["to_do"]["checked"])
        self.assertEqual(self.fake.writes, [])

    def test_semantic_distinct_existing_rows_keep_individual_identity_and_titles(self):
        self.fake.add(block("synthetic-a", "leche entera"))
        self.fake.add(block("synthetic-b", "leche deslactosada"))
        operations = self.register_semantic({"text": "leche entera", "candidate_id": "synthetic-a"},
                                            {"text": "leche deslactosada", "candidate_id": "synthetic-b"})
        receipts = [self.gateway.execute(operation, plan)["receipt"] for operation, plan in operations]
        self.assertEqual([receipt["block_id"] for receipt in receipts], ["synthetic-a", "synthetic-b"])
        self.assertEqual([receipt["item"] for receipt in receipts], ["leche entera", "leche deslactosada"])
        self.assertEqual(self.fake.writes, [])

    def test_semantic_duplicate_selected_ids_and_duplicate_new_synonyms_are_rejected(self):
        self.fake.add(block("synthetic-milk", "leche"))
        for selections in (({"text": "leche", "candidate_id": "synthetic-milk"},
                            {"text": "lece", "candidate_id": "synthetic-milk"}),
                           ({"text": "papa", "candidate_id": None}, {"text": "patata", "candidate_id": None})):
            with self.assertRaises(adapter.GroceryAdapterError) as caught:
                self.semantic(*selections)
            self.assertEqual(caught.exception.code, "duplicate_item")
        self.assertEqual(self.fake.writes, [])

    def test_semantic_catalog_change_during_inference_fails_before_journal_or_mutation(self):
        catalog = self.gateway.catalog()
        self.fake.add(block("synthetic-race", "banana"))
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            self.gateway.prepare_semantic([{"text": "banano", "candidate_id": None}], catalog)
        self.assertEqual(caught.exception.code, "catalog_drift")
        self.assertTrue(caught.exception.needs_input)
        self.assertEqual(self.fake.writes, [])

    def test_semantic_new_text_is_grounded_unchanged_and_complete_registered_batch_commits(self):
        selections = ({"text": "mango Tommy", "candidate_id": None}, {"text": "arroz integral", "candidate_id": None})
        operations = self.register_semantic(*selections)
        self.gateway.register_operations(operations)
        results = [self.gateway.execute(operation, plan) for operation, plan in operations]
        self.assertTrue(all(result["status"] == "committed" for result in results))
        self.assertEqual([result["receipt"]["item"] for result in results], [item["text"] for item in selections])
        self.assertTrue(all(result["receipt"]["checked"] is False for result in results))
        for operation, plan in operations:
            self.assertEqual(self.gateway.execute(operation, plan), self.gateway.status(operation, plan))
        self.assertEqual(len(self.fake.writes), 2)

    def test_semantic_registered_batch_continues_after_already_present_sibling(self):
        self.fake.add(block("synthetic-eggs", "huevos"))
        operations = self.register_semantic({"text": "huevo", "candidate_id": "synthetic-eggs"},
                                            {"text": "arroz", "candidate_id": None})
        self.assertEqual([self.gateway.execute(operation, plan)["status"] for operation, plan in operations],
                         ["committed", "committed"])
        self.assertEqual(len(self.fake.writes), 1)

    def test_semantic_unregistered_partial_or_out_of_order_batch_cannot_write(self):
        plans = self.semantic({"text": "arroz", "candidate_id": None}, {"text": "papa", "candidate_id": None})
        operations = [(adapter.child_operation_id("out-of-order", plan), plan) for plan in plans]
        for invalid in (operations[:1], list(reversed(operations))):
            with self.assertRaises(adapter.GroceryAdapterError) as caught:
                self.gateway.register_operations(invalid)
            self.assertEqual(caught.exception.code, "invalid_operations")
        self.assertEqual(self.gateway.execute("unregistered", plans[0])["status"], "rejected")
        self.gateway.register_operations(operations)
        self.assertEqual(self.gateway.execute(*operations[1])["status"], "rejected")
        self.assertEqual(self.fake.writes, [])

    def test_semantic_preflight_rejects_all_snapshot_drift_and_never_rematches_retry(self):
        self.fake.add(block("synthetic-milk", "leche"))
        self.fake.add(block("synthetic-eggs", "huevos"))
        plan = self.semantic({"text": "lece", "candidate_id": "synthetic-milk"})[0]
        # Even unrelated candidate drift invalidates the inference snapshot.
        self.fake.blocks["synthetic-eggs"]["to_do"]["checked"] = True
        result = self.gateway.execute("semantic-drift", plan)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["code"], "catalog_drift")
        self.fake.blocks["synthetic-eggs"]["to_do"]["checked"] = False
        self.assertEqual(self.gateway.execute("semantic-drift", plan), result)
        self.assertEqual(self.fake.writes, [])

    def test_semantic_selected_text_parent_status_and_destination_drift_fail_closed(self):
        self.fake.add(block("synthetic-milk", "leche"))
        for change in ("text", "status", "parent"):
            with self.subTest(change=change):
                self.fake.blocks["synthetic-milk"] = block("synthetic-milk", "leche")
                self.fake.children["synthetic-others"] = ["synthetic-milk"]
                self.fake.children["synthetic-official-list"] = ["synthetic-others"]
                plan = self.semantic({"text": "leche", "candidate_id": "synthetic-milk"})[0]
                if change == "text":
                    self.fake.blocks["synthetic-milk"]["to_do"]["rich_text"][0]["plain_text"] = "leche entera"
                elif change == "status":
                    self.fake.blocks["synthetic-milk"]["to_do"]["checked"] = True
                else:
                    self.fake.blocks["synthetic-milk"]["parent"] = {"type": "page_id", "page_id": "synthetic-official-list"}
                    self.fake.children["synthetic-others"] = []
                    self.fake.children["synthetic-official-list"].append("synthetic-milk")
                self.assertEqual(self.gateway.execute("semantic-target-drift-" + change, plan)["status"], "rejected")
        create = self.semantic({"text": "arroz", "candidate_id": None})[0]
        self.fake.blocks["synthetic-others"]["toggle"]["rich_text"][0]["plain_text"] = "otro grupo"
        self.assertEqual(self.gateway.execute("semantic-parent-drift", create)["status"], "rejected")
        self.assertEqual(self.fake.writes, [])

    def test_semantic_serial_requests_reject_stale_synonym_creation(self):
        first = self.semantic({"text": "papa", "candidate_id": None})[0]
        second = self.semantic({"text": "patata", "candidate_id": None})[0]
        self.assertEqual(self.gateway.execute("first-request", first)["status"], "committed")
        result = self.gateway.execute("second-request", second)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["code"], "catalog_drift")
        self.assertEqual(len(self.fake.writes), 1)

    def test_semantic_cross_request_receipts_cannot_excuse_catalog_drift(self):
        selections = ({"text": "papa", "candidate_id": None}, {"text": "arroz", "candidate_id": None})
        first = self.register_semantic(*selections, parent="first-parent")
        second = self.register_semantic(*selections, parent="second-parent")
        self.assertEqual(self.gateway.execute(*first[0])["status"], "committed")
        self.assertEqual(self.gateway.execute(*second[0])["status"], "rejected")
        self.assertEqual(self.gateway.execute(*second[1])["status"], "rejected")
        self.assertEqual(self.gateway.execute(*first[1])["status"], "committed")
        self.assertEqual(len(self.fake.writes), 2)

    def test_semantic_uncertain_create_does_not_replay_and_blocks_next_sibling(self):
        operations = self.register_semantic({"text": "papa", "candidate_id": None}, {"text": "arroz", "candidate_id": None})
        self.fake.after_mutation_error = TimeoutError("lost acknowledgement")
        self.assertEqual(self.gateway.execute(*operations[0])["status"], "uncertain")
        self.fake.after_mutation_error = None
        self.assertEqual(self.gateway.execute(*operations[0])["status"], "uncertain")
        self.assertEqual(self.gateway.execute(*operations[1])["status"], "rejected")
        self.assertEqual(len(self.fake.writes), 1)

    def test_semantic_acknowledged_create_reconciles_read_only_then_batch_can_continue(self):
        operations = self.register_semantic({"text": "papa", "candidate_id": None}, {"text": "arroz", "candidate_id": None})
        self.fake.fail_readback = True
        self.assertEqual(self.gateway.execute(*operations[0])["status"], "uncertain")
        self.fake.fail_readback = False
        self.assertEqual(self.gateway.status(*operations[0])["status"], "committed")
        self.assertEqual(len(self.fake.writes), 1)
        self.assertEqual(self.gateway.execute(*operations[1])["status"], "committed")
        self.assertEqual(len(self.fake.writes), 2)

    def test_semantic_plan_tampering_never_changes_selected_target_or_status(self):
        self.fake.add(block("synthetic-milk", "leche"))
        plan = self.semantic({"text": "leche", "candidate_id": "synthetic-milk"})[0]
        changes = ({"target_id": "synthetic-other"}, {"expected_checked": True}, {"action": "updated"},
                   {"version": 1}, {"batch_index": 1}, {"batch_hash": "bad"}, {"catalog_snapshot": []})
        for changed in changes:
            with self.subTest(changed=changed):
                with self.assertRaises(adapter.GroceryAdapterError) as caught:
                    self.gateway.execute("semantic-tamper", dict(plan, **changed))
                self.assertEqual(caught.exception.code, "invalid_plan")
        self.assertEqual(self.fake.writes, [])

    def test_semantic_duplicate_qualified_equivalent_rows_require_clarification(self):
        self.fake.add(block("synthetic-milk-a", "leche entera"))
        self.fake.add(block("synthetic-milk-b", "leche entera"))
        for candidate in ("synthetic-milk-a", "synthetic-milk-b", None):
            with self.subTest(candidate=candidate):
                with self.assertRaises(adapter.GroceryAdapterError) as caught:
                    self.semantic({"text": "leche entera", "candidate_id": candidate})
                self.assertEqual(caught.exception.code, "ambiguous_item")
                self.assertTrue(caught.exception.needs_input)
        self.assertEqual(self.fake.writes, [])

    def test_semantic_bare_product_with_single_qualified_row_cannot_create_ambiguous_generic(self):
        self.fake.add(block("synthetic-milk", "leche deslactosada"))
        for candidate in ("synthetic-milk", None):
            with self.subTest(candidate=candidate):
                with self.assertRaises(adapter.GroceryAdapterError) as caught:
                    self.semantic({"text": "leche", "candidate_id": candidate})
                self.assertEqual(caught.exception.code, "ambiguous_item")
        self.assertEqual(self.fake.writes, [])

    def test_semantic_explicit_missing_unchecks_exact_bought_target_and_replay_preserves_later_status(self):
        self.fake.add(block("synthetic-bought", "huevos", checked=True))
        selection = [{"text": "huevo", "candidate_id": "synthetic-bought"}]
        plan = self.gateway.prepare_semantic(selection, self.gateway.catalog(), mark_missing=True)["items"][0]
        self.assertTrue(plan["mark_missing"])
        self.assertTrue(plan["expected_checked"])
        self.assertEqual(plan["action"], "updated")
        result = self.gateway.execute("explicit-missing", plan)
        self.assertEqual(result["status"], "committed")
        self.assertTrue(result["write_committed"])
        self.assertEqual(result["receipt"]["item"], "huevos")
        self.assertFalse(self.fake.blocks["synthetic-bought"]["to_do"]["checked"])
        self.assertEqual(self.fake.writes, [("PATCH", "/blocks/synthetic-bought", {"to_do": {"checked": False}})])
        # A later human check must survive retries of the old capture.
        self.fake.blocks["synthetic-bought"]["to_do"]["checked"] = True
        self.assertEqual(self.gateway.execute("explicit-missing", plan), result)
        self.assertEqual(self.gateway.status("explicit-missing", plan), result)
        self.assertTrue(self.fake.blocks["synthetic-bought"]["to_do"]["checked"])
        self.assertEqual(len(self.fake.writes), 1)

    def test_semantic_missing_intent_is_trusted_bool_and_cannot_come_from_model_item(self):
        self.fake.add(block("synthetic-bought", "huevos", checked=True))
        catalog = self.gateway.catalog()
        selection = {"text": "huevo", "candidate_id": "synthetic-bought"}
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            self.gateway.prepare_semantic([dict(selection, mark_missing=True)], catalog)
        self.assertEqual(caught.exception.code, "invalid_items")
        for mark_missing in (1, "true", None):
            with self.subTest(mark_missing=mark_missing):
                with self.assertRaises(adapter.GroceryAdapterError) as caught:
                    self.gateway.prepare_semantic([selection], catalog, mark_missing=mark_missing)
                self.assertEqual(caught.exception.code, "invalid_items")
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            self.gateway.prepare_semantic([selection], catalog)
        self.assertEqual(caught.exception.code, "bought_candidate")
        self.assertEqual(self.fake.writes, [])

    def test_semantic_mixed_update_create_batch_accepts_only_its_confirmed_sibling_changes(self):
        self.fake.add(block("synthetic-eggs", "huevos", checked=True))
        self.fake.add(block("synthetic-milk", "leche", checked=True))
        selections = [{"text": "huevo", "candidate_id": "synthetic-eggs"},
                      {"text": "arroz", "candidate_id": None},
                      {"text": "lece", "candidate_id": "synthetic-milk"}]
        plans = self.gateway.prepare_semantic(selections, self.gateway.catalog(), mark_missing=True)["items"]
        operations = [(adapter.child_operation_id("mixed-missing", plan), plan) for plan in plans]
        self.gateway.register_operations(operations)
        results = [self.gateway.execute(*operation) for operation in operations]
        self.assertEqual([result["status"] for result in results], ["committed", "committed", "committed"])
        self.assertEqual([result["receipt"]["action"] for result in results], ["updated", "created", "updated"])
        self.assertFalse(self.fake.blocks["synthetic-eggs"]["to_do"]["checked"])
        self.assertFalse(self.fake.blocks["synthetic-milk"]["to_do"]["checked"])
        self.assertEqual(len(self.fake.writes), 3)

    def test_semantic_later_sibling_rejects_rechecked_explicit_missing_target(self):
        self.fake.add(block("synthetic-eggs", "huevos", checked=True))
        selections = [{"text": "huevo", "candidate_id": "synthetic-eggs"}, {"text": "arroz", "candidate_id": None}]
        plans = self.gateway.prepare_semantic(selections, self.gateway.catalog(), mark_missing=True)["items"]
        operations = [(adapter.child_operation_id("rechecked-missing", plan), plan) for plan in plans]
        self.gateway.register_operations(operations)
        self.assertEqual(self.gateway.execute(*operations[0])["status"], "committed")
        self.fake.blocks["synthetic-eggs"]["to_do"]["checked"] = True
        result = self.gateway.execute(*operations[1])
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["code"], "catalog_drift")
        self.assertEqual(len(self.fake.writes), 1)

    def test_semantic_missing_intent_is_frozen_and_quantity_conflicts_still_clarify(self):
        self.fake.add(block("synthetic-eggs", "huevos", checked=True))
        plan = self.gateway.prepare_semantic([{"text": "huevo", "candidate_id": "synthetic-eggs"}],
                                              self.gateway.catalog(), mark_missing=True)["items"][0]
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            self.gateway.execute("missing-tamper", dict(plan, mark_missing=False))
        self.assertEqual(caught.exception.code, "invalid_plan")
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            self.gateway.prepare_semantic([{"text": "6 huevos", "candidate_id": "synthetic-eggs"}],
                                           self.gateway.catalog(), mark_missing=True)
        self.assertEqual(caught.exception.code, "quantity_conflict")
        self.assertEqual(self.fake.writes, [])

    def test_semantic_quantity_only_fragments_abort_entire_batch_before_any_journal_or_write(self):
        for text in ("1", "2", "1,5", "1/2", "½", "2 litros", "un kilo", "un kilo y medio", "medio",
                     "litros", "kg", "kilos de", "litros y medio", "2ml", "2 litros, 500 ml", "1 5 kilos de arroz"):
            with self.subTest(text=text):
                with self.assertRaises(adapter.GroceryAdapterError) as caught:
                    self.semantic({"text": "aguacate", "candidate_id": None}, {"text": text, "candidate_id": None})
                self.assertEqual(caught.exception.code, "invalid_item")
                self.assertTrue(caught.exception.needs_input)
        # This is the malformed decimal split that must never become two rows.
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            self.semantic({"text": "1", "candidate_id": None}, {"text": "5 kilos de arroz", "candidate_id": None})
        self.assertEqual(caught.exception.code, "invalid_item")
        with sqlite3.connect(self.journal) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM grocery_operations").fetchone()[0], 0)
        self.assertEqual(self.fake.writes, [])

    def test_semantic_actual_products_keep_quantity_and_numeric_brand_names(self):
        for text in ("5 kilos de arroz", "7up", "7 Up", "rollos de canela"):
            with self.subTest(text=text):
                plan = self.semantic({"text": text, "candidate_id": None})[0]
                self.assertEqual(plan["action"], "created")
                self.assertEqual(plan["item"], text)
                self.assertEqual(plan["expected_text"], text)
        self.assertEqual(self.fake.writes, [])

    def test_fixture_is_exact_ast_and_contains_only_synthetic_destination(self):
        contract.extracted_ast()
        self.assertIn("synthetic-official-list", contract.source())
        snapshot = Path(__file__).resolve().parents[2] / ".private/setup/multi-grocery/apple_watch_capture.deployed.py"
        if snapshot.exists():
            contract.assert_matches_deployed(snapshot)
        self.assertEqual(contract.ORIGINAL_SHA256, "24be164879b114487516b85b6811cce8239a4ad0744ec1e36bfc575a75a8e7e0")

    def test_pinned_source_is_checked_before_import_and_import_has_no_io(self):
        self.helper_path.write_text("raise AssertionError('must not execute')")
        with mock.patch("urllib.request.urlopen") as transport, mock.patch.object(Path, "home", side_effect=AssertionError("real home read")):
            with self.assertRaises(adapter.GroceryAdapterError) as caught:
                adapter.GroceryAdapter(self.helper_path, self.directory / "other.sqlite3")
            self.assertEqual(caught.exception.code, "helper_fingerprint_mismatch")
            transport.assert_not_called()

    def test_production_snapshot_import_is_offline_with_fake_home_and_environment(self):
        snapshot = Path(__file__).resolve().parents[2] / ".private/setup/multi-grocery/apple_watch_capture.deployed.py"
        if not snapshot.exists():
            self.skipTest("private pinned snapshot is optional in CI")
        with mock.patch.object(adapter, "PINNED_HELPER_SHA256", contract.ORIGINAL_SHA256), mock.patch.object(Path, "home", return_value=self.directory / "fake-home"), mock.patch("urllib.request.urlopen") as network:
            helper = adapter._load_helper(snapshot)
            self.assertTrue(callable(helper.get_all_grocery_blocks))
            self.assertEqual(helper.HERMES_HOME, self.directory / "fake-home")
            network.assert_not_called()

    def test_prepare_traverses_real_recursive_pagination_without_mutations(self):
        for index in range(5):
            self.fake.add(block(f"synthetic-top-{index}", f"grupo {index}", kind="toggle", parent="synthetic-official-list"))
        self.fake.add(block("synthetic-nested", "arroz", parent="synthetic-top-4"))
        frozen = self.gateway.prepare(["arroz", "leche"])
        self.assertEqual(frozen["items"][0]["parent_id"], "synthetic-top-4")
        self.assertEqual(frozen["items"][1]["parent_id"], "synthetic-others")
        self.assertTrue(any(call[2].get("start_cursor") for call in self.fake.calls))
        self.assertEqual(self.fake.writes, [])

    def test_malformed_pagination_fails_closed(self):
        self.fake.bad_cursor = True
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            self.plan()
        self.assertEqual(caught.exception.code, "invalid_list")
        self.assertEqual(self.fake.writes, [])

    def test_repeated_cursor_and_missing_list_shape_fail_closed(self):
        for malformed in ({"object": "list", "results": []},
                          {"object": "list", "results": [], "has_more": True, "next_cursor": "same-cursor"}):
            with self.subTest(malformed=malformed):
                with mock.patch("urllib.request.urlopen", return_value=Response(malformed)):
                    with self.assertRaises(adapter.GroceryAdapterError) as caught:
                        self.plan()
                self.assertEqual(caught.exception.code, "invalid_list")
        self.assertEqual(self.fake.writes, [])

    def test_existing_rich_canonical_and_compound_use_real_normalization(self):
        self.fake.add(block("synthetic-rice", "- 1 kg arroz = 5.000"))
        self.fake.add(block("synthetic-compound", "leche y huevos"))
        plan = self.plan("arroz")
        self.assertEqual(plan["action"], "already_present")
        result = self.gateway.execute("existing-rice", plan)
        self.assertEqual(result["receipt"]["item"], "- 1 kg arroz = 5.000")
        self.assertFalse(result["write_committed"])
        self.assertEqual(self.plan("leche")["action"], "created")
        self.assertEqual(self.plan("leche y huevos")["target_id"], "synthetic-compound")
        self.assertEqual(self.fake.writes, [])

    def test_quantity_conflicts_abort_entire_batch_before_writes(self):
        self.fake.add(block("synthetic-rice", "- 1 kg arroz = 5.000"))
        for request in ("2 kg arroz", "2 arroz", "- 2 kg arroz"):
            with self.subTest(request=request):
                with self.assertRaises(adapter.GroceryAdapterError) as caught:
                    self.gateway.prepare(["leche", request])
                self.assertEqual(caught.exception.code, "quantity_conflict")
                self.assertTrue(caught.exception.needs_input)
        self.assertEqual(self.fake.writes, [])

    def test_extended_quantity_collisions_abort_whole_batch_without_mutations(self):
        cases = (
            ("un litro y medio de leche", "leche"),
            ("un litro y medio de leche", "1 litro de leche"),
            ("un litro, y medio de leche", "leche"),
            ("1,5 litros de leche", "leche"),
            ("1.5 litros de leche", "1 litro de leche"),
            ("1,5 litros de leche", "2 litros de leche"),
            ("6 huevos", "huevos"),
            ("6 huevos", "2 huevos"),
            ("2kg de arroz", "arroz"),
            ("1/2 kilo de arroz", "arroz"),
            ("½ kilo de arroz", "arroz"),
            ("1 litro, 500 ml de leche", "1 litro de leche"),
            ("leche, 2 litros", "leche"),
        )
        for request, existing in cases:
            with self.subTest(request=request, existing=existing):
                self.fake.blocks = {"synthetic-others": self.fake.blocks["synthetic-others"]}
                self.fake.children["synthetic-others"] = []
                self.fake.add(block("synthetic-quantity", existing, checked=True))
                with self.assertRaises(adapter.GroceryAdapterError) as caught:
                    self.gateway.prepare(["aguacate", request])
                self.assertEqual(caught.exception.code, "quantity_conflict")
                self.assertEqual(self.fake.writes, [])
                self.assertTrue(self.fake.blocks["synthetic-quantity"]["to_do"]["checked"])
        with sqlite3.connect(self.journal) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM grocery_operations").fetchone()[0], 0)

    def test_quantity_collision_keys_preserve_full_compound_product_names(self):
        self.fake.add(block("synthetic-milk", "leche"))
        self.fake.add(block("synthetic-salt", "sal"))
        self.fake.add(block("synthetic-oil", "aceite de oliva"))
        requested = ["2 paquetes de sal y pimienta", "un litro de leche de almendras", "aceite de oliva y girasol", "polvo de estrellas y esencia lunar"]
        prepared = self.gateway.prepare(requested)
        self.assertEqual([plan["item"] for plan in prepared["items"]], requested)
        self.assertTrue(all(plan["action"] == "created" for plan in prepared["items"]))
        self.assertEqual(self.fake.writes, [])

    def test_extended_quantity_canonical_is_reused_when_request_has_no_quantity(self):
        self.fake.add(block("synthetic-milk", "un litro y medio de leche"))
        self.fake.add(block("synthetic-eggs", "6 huevos"))
        plans = self.gateway.prepare(["leche", "huevos"])["items"]
        self.assertEqual([plan["target_id"] for plan in plans], ["synthetic-milk", "synthetic-eggs"])
        self.assertTrue(all(plan["action"] == "already_present" for plan in plans))
        self.assertEqual(plans[0]["expected_text"], "un litro y medio de leche")
        self.assertEqual(plans[1]["expected_text"], "6 huevos")
        self.assertEqual(self.fake.writes, [])

    def test_ambiguous_canonicals_and_others_require_input(self):
        self.fake.add(block("synthetic-rice-1", "arroz"))
        self.fake.add(block("synthetic-rice-2", "- 1 kg arroz"))
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            self.plan("arroz")
        self.assertEqual(caught.exception.code, "ambiguous_item")
        self.fake.add(block("synthetic-others-2", "Otros", kind="toggle", parent="synthetic-official-list"))
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            self.plan("leche")
        self.assertEqual(caught.exception.code, "ambiguous_destination")
        self.assertEqual(self.fake.writes, [])

    def test_create_preserves_requested_quantity_and_validates_readback(self):
        with mock.patch.object(self.gateway.helper, "upsert_grocery", side_effect=AssertionError("generic upsert")):
            result, child, plan = self.execute("2 litros de leche")
        self.assertEqual(result["status"], "committed")
        self.assertTrue(result["write_committed"])
        self.assertEqual(result["receipt"]["item"], "2 litros de leche")
        self.assertEqual(result["receipt"]["destination"], "synthetic-official-list")
        self.assertEqual(result["receipt"]["parent_id"], "synthetic-others")
        self.assertEqual(len(self.fake.writes), 1)
        self.assertEqual(self.gateway.status(child, plan), result)

    def test_committed_retry_does_not_read_or_reuncheck_user_checked_item(self):
        self.fake.add(block("synthetic-existing", "leche", checked=True))
        result, child, plan = self.execute()
        self.assertEqual(result["receipt"]["action"], "updated")
        self.fake.blocks["synthetic-existing"]["to_do"]["checked"] = True
        count = len(self.fake.calls)
        self.assertEqual(self.gateway.execute(child, plan), result)
        self.assertEqual(self.gateway.status(child, plan), result)
        self.assertEqual(len(self.fake.calls), count)
        self.assertTrue(self.fake.blocks["synthetic-existing"]["to_do"]["checked"])
        self.assertEqual(len(self.fake.writes), 1)

    def test_claim_is_committed_and_no_sqlite_transaction_crosses_network(self):
        def inspect():
            with sqlite3.connect(self.journal, timeout=0.1) as conn:
                self.assertEqual(conn.execute("SELECT status FROM grocery_operations").fetchone()[0], "dispatching")
                conn.execute("BEGIN IMMEDIATE")
                conn.rollback()
        self.fake.before_mutation = inspect
        self.assertEqual(self.execute()[0]["status"], "committed")
        with self.gateway._db() as conn:
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)

    def test_timeout_after_write_without_ack_never_replays_or_infers_from_text(self):
        self.fake.after_mutation_error = TimeoutError("synthetic lost acknowledgement")
        result, child, plan = self.execute()
        self.assertEqual(result["status"], "uncertain")
        self.assertIsNone(result["write_committed"])
        self.fake.after_mutation_error = None
        for _ in range(2):
            self.assertEqual(self.gateway.execute(child, plan)["status"], "uncertain")
            self.assertEqual(self.gateway.status(child, plan)["status"], "uncertain")
        self.assertEqual(len(self.fake.writes), 1)
        self.assertEqual(len(self.fake.children["synthetic-others"]), 1)

    def test_crash_after_durable_claim_never_replays(self):
        self.fake.after_mutation_error = SystemExit("synthetic process crash")
        plan = self.plan()
        with self.assertRaises(SystemExit):
            self.gateway.execute("crashed", plan)
        with sqlite3.connect(self.journal) as conn:
            self.assertEqual(conn.execute("SELECT status FROM grocery_operations").fetchone()[0], "dispatching")
        self.fake.after_mutation_error = None
        restarted = adapter.GroceryAdapter(self.helper_path, self.journal)
        self.assertEqual(restarted.execute("crashed", plan)["status"], "uncertain")
        self.assertEqual(restarted.status("crashed", plan)["status"], "uncertain")
        self.assertEqual(len(self.fake.writes), 1)

    def test_candidate_ack_reconciles_read_only_after_restart(self):
        self.fake.fail_readback = True
        result, child, plan = self.execute()
        self.assertEqual(result["status"], "uncertain")
        with sqlite3.connect(self.journal) as conn:
            self.assertTrue(conn.execute("SELECT candidate_id FROM grocery_operations").fetchone()[0])
        self.fake.fail_readback = False
        restarted = adapter.GroceryAdapter(self.helper_path, self.journal)
        self.assertEqual(restarted.status(child, plan)["status"], "committed")
        self.assertEqual(len(self.fake.writes), 1)

    def test_candidate_readback_rejects_wrong_text_checked_parent_type_or_destination(self):
        for field in ("text", "checked", "parent", "type", "tree"):
            with self.subTest(field=field):
                plan = self.plan("producto " + field)
                self.fake.fail_readback = True
                result = self.gateway.execute("candidate-" + field, plan)
                self.assertEqual(result["status"], "uncertain")
                candidate = self.fake.children["synthetic-others"][-1]
                value = self.fake.blocks[candidate]
                original = copy.deepcopy(value)
                if field == "text":
                    value["to_do"]["rich_text"][0]["plain_text"] = "other"
                elif field == "checked":
                    value["to_do"]["checked"] = True
                elif field == "parent":
                    value["parent"]["block_id"] = "synthetic-elsewhere"
                elif field == "type":
                    value["type"] = "paragraph"
                else:
                    self.fake.children["synthetic-others"].remove(candidate)
                self.fake.fail_readback = False
                before = len(self.fake.writes)
                self.assertEqual(self.gateway.status("candidate-" + field, plan)["status"], "uncertain")
                self.assertEqual(len(self.fake.writes), before)
                self.fake.blocks[candidate] = original
                if candidate not in self.fake.children["synthetic-others"]:
                    self.fake.children["synthetic-others"].append(candidate)

    def test_lost_ack_after_receipt_commit_recovers_without_more_writes(self):
        original = self.gateway._store_receipt
        def save_then_lose_ack(*args):
            original(*args)
            raise TimeoutError("synthetic caller lost receipt ACK")
        with mock.patch.object(self.gateway, "_store_receipt", side_effect=save_then_lose_ack):
            result, child, plan = self.execute()
        self.assertEqual(result["status"], "committed")
        self.assertEqual(self.gateway.status(child, plan), result)
        self.assertEqual(self.gateway.execute(child, plan), result)
        self.assertEqual(len(self.fake.writes), 1)

    def test_concurrent_execute_has_single_mutation(self):
        plan = self.plan()
        barrier = threading.Barrier(4)
        def execute(_):
            barrier.wait()
            return self.gateway.execute("concurrent-operation", plan)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(execute, range(4)))
        self.assertTrue(all(result["status"] == "committed" for result in results))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(self.fake.writes), 1)

    def test_separate_adapter_instances_share_helper_lock_and_journal_claim(self):
        second = adapter.GroceryAdapter(self.helper_path, self.journal)
        plan = self.plan()
        barrier = threading.Barrier(2)
        def execute(gateway):
            barrier.wait()
            return gateway.execute("shared-operation", plan)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(execute, (self.gateway, second)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]["status"], "committed")
        self.assertEqual(len(self.fake.writes), 1)

    def test_destination_is_validated_again_after_mutation(self):
        original = self.fake.open
        def move_group_after_write(req, timeout):
            result = original(req, timeout)
            if req.get_method() == "PATCH":
                self.fake.children["synthetic-official-list"].remove("synthetic-others")
            return result
        with mock.patch("urllib.request.urlopen", side_effect=move_group_after_write):
            result, child, plan = self.execute()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(self.gateway.status(child, plan)["status"], "uncertain")
        self.assertEqual(len(self.fake.writes), 1)

    def test_conflicting_operation_ids_and_tampered_plans_are_rejected(self):
        result, child, plan = self.execute()
        other = self.plan("huevos")
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            self.gateway.execute(child, other)
        self.assertEqual(caught.exception.code, "operation_conflict")
        for field, value in (("item", "huevos"), ("destination", "synthetic-other-list"), ("plan_hash", "bad")):
            changed = dict(plan, **{field: value})
            with self.assertRaises(adapter.GroceryAdapterError):
                self.gateway.execute("tamper", changed)
        for operation in ("x" * 201, "../bad", "", 123):
            with self.assertRaises(adapter.GroceryAdapterError):
                self.gateway.execute(operation, plan)
        self.assertEqual(len(self.fake.writes), 1)
        self.assertNotEqual(adapter.child_operation_id("parent1", plan), adapter.child_operation_id("parent2", plan))

    def test_pre_dispatch_item_destination_and_helper_drift_fail_closed(self):
        plan = self.plan()
        self.fake.blocks["synthetic-others"]["toggle"]["rich_text"][0]["plain_text"] = "otro grupo"
        result = self.gateway.execute("destination-drift", plan)
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["write_committed"])
        self.fake.blocks["synthetic-others"]["toggle"]["rich_text"][0]["plain_text"] = "Otros"
        self.fake.add(block("synthetic-existing", "leche", checked=False))
        self.assertEqual(self.gateway.execute("item-drift", plan)["status"], "rejected")
        self.helper_path.write_text(self.source + "\n# changed\n")
        self.assertEqual(self.gateway.execute("helper-drift", plan)["code"], "helper_fingerprint_mismatch")
        self.assertEqual(self.fake.writes, [])

    def test_status_for_unstarted_operation_is_read_only(self):
        plan = self.plan()
        before = len(self.fake.calls)
        self.assertEqual(self.gateway.status("not-started", plan), {"status": "prepared", "write_committed": False, "code": "not_started"})
        self.assertEqual(len(self.fake.calls), before)
        with sqlite3.connect(self.journal) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM grocery_operations").fetchone()[0], 0)

    def test_register_operations_atomically_prepares_every_child_without_notion_io(self):
        plans = self.gateway.prepare(["leche", "huevos", "arroz"])["items"]
        operations = [(adapter.child_operation_id("registered-parent", plan), plan) for plan in plans]
        before = len(self.fake.calls)
        with mock.patch.object(self.gateway.helper, "capture_operation_lock", side_effect=AssertionError("helper lock unnecessary")), \
                mock.patch.object(self.gateway.helper, "notion_request", side_effect=AssertionError("registration must be local")):
            self.gateway.register_operations(operations)
            self.gateway.register_operations(operations)
        self.assertEqual(len(self.fake.calls), before)
        with sqlite3.connect(self.journal) as conn:
            rows = conn.execute("SELECT operation_id, status, candidate_id, receipt_json FROM grocery_operations").fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual({row[0] for row in rows}, {operation[0] for operation in operations})
        self.assertTrue(all(row[1:] == ("prepared", None, None) for row in rows))
        for operation_id, plan in operations:
            self.assertEqual(self.gateway.status(operation_id, plan), {"status": "prepared", "write_committed": False, "code": "not_dispatched"})
        self.assertEqual(self.fake.writes, [])

    def test_register_operations_rolls_back_entire_batch_on_conflict(self):
        plans = self.gateway.prepare(["leche", "huevos", "arroz"])["items"]
        self.gateway.register_operations([("existing-operation", plans[0])])
        before = len(self.fake.calls)
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            self.gateway.register_operations([("new-operation", plans[1]), ("existing-operation", plans[2])])
        self.assertEqual(caught.exception.code, "operation_conflict")
        with sqlite3.connect(self.journal) as conn:
            self.assertEqual(conn.execute("SELECT operation_id FROM grocery_operations").fetchall(), [("existing-operation",)])
        self.assertEqual(len(self.fake.calls), before)
        self.assertEqual(self.fake.writes, [])

    def test_register_operations_validates_all_inputs_before_any_insert(self):
        plans = self.gateway.prepare(["leche", "huevos"])["items"]
        for invalid in (("x" * 201, plans[1]), ("bad-plan", dict(plans[1], item="changed"))):
            with self.subTest(invalid_id=invalid[0]):
                with self.assertRaises(adapter.GroceryAdapterError):
                    self.gateway.register_operations([("valid-operation", plans[0]), invalid])
        with sqlite3.connect(self.journal) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM grocery_operations").fetchone()[0], 0)
        self.assertEqual(self.fake.writes, [])

    def test_register_operations_checks_corrupt_rows_and_rolls_back_new_children(self):
        plans = self.gateway.prepare(["leche", "huevos"])["items"]
        self.gateway.register_operations([("corrupt-operation", plans[0])])
        with sqlite3.connect(self.journal) as conn:
            conn.execute("UPDATE grocery_operations SET candidate_id='unexpected-candidate' WHERE operation_id='corrupt-operation'")
        before = len(self.fake.calls)
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            self.gateway.register_operations([("new-operation", plans[1]), ("corrupt-operation", plans[0])])
        self.assertEqual(caught.exception.code, "journal_corrupt")
        with sqlite3.connect(self.journal) as conn:
            self.assertEqual(conn.execute("SELECT operation_id FROM grocery_operations").fetchall(), [("corrupt-operation",)])
        self.assertEqual(len(self.fake.calls), before)
        self.assertEqual(self.fake.writes, [])

    def test_corrupt_receipt_and_database_fail_closed(self):
        _, child, plan = self.execute()
        with sqlite3.connect(self.journal) as conn:
            conn.execute("UPDATE grocery_operations SET receipt_json='{}' WHERE operation_id=?", (child,))
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            self.gateway.execute(child, plan)
        self.assertEqual(caught.exception.code, "journal_corrupt")
        self.assertEqual(len(self.fake.writes), 1)
        corrupt = self.directory / "corrupt.sqlite3"
        corrupt.write_bytes(b"not sqlite")
        with self.assertRaises(adapter.GroceryAdapterError) as caught:
            adapter.GroceryAdapter(self.helper_path, corrupt)
        self.assertEqual(caught.exception.code, "journal_unavailable")


if __name__ == "__main__":
    unittest.main()
