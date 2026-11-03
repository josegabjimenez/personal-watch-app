"""Opt-in, add-only adapter for the pinned official grocery helper.

The external helper supplies traversal, grocery normalization, the Notion
transport and its shared capture lock. Its generic capture router and event
cache are deliberately outside this adapter's call graph. SQLite commits a
dispatch claim before a mutation; an interrupted claim is never replayed.
Only an acknowledged block ID can be reconciled using read-only requests.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

PINNED_HELPER_SHA256 = "24be164879b114487516b85b6811cce8239a4ad0744ec1e36bfc575a75a8e7e0"
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_OPERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")
_PLAN_FIELDS = frozenset({
    "version", "helper_sha256", "destination", "item", "key", "action",
    "target_id", "parent_id", "expected_text", "expected_checked", "plan_hash",
})
_STATES = {"prepared", "dispatching", "uncertain", "committed", "rejected"}
_QUANTITY_NUMBER = (
    r"(?:[0-9]+(?:[.,][0-9]+)?(?:/[0-9]+)?|[¼½¾]|"
    r"un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|"
    r"trece|catorce|quince|dieciseis|diecisiete|dieciocho|diecinueve|veinte|medio|media)"
)
_MEASURE_UNIT = (
    r"(?:kilogramos?|kilos?|kg|gramos?|gr|g|litros?|l|mililitros?|ml|libras?|lb|onzas?|"
    r"packs?|paquetes?|bolsas?|botellas?|latas?|cajas?|docenas?|unidades?|unds?|"
    r"barras?|tarros?|vasos?|tazas?|bandejas?|rollos?)"
)
_LEADING_AMOUNT = re.compile(r"^(?:" + _QUANTITY_NUMBER + r")(?!\w)")
_ATTACHED_AMOUNT = re.compile(r"^[0-9]+(?:[.,][0-9]+)?(?=" + _MEASURE_UNIT + r"\b)")
_MEASURE_START = re.compile(r"^" + _MEASURE_UNIT + r"\b")
_MEASURE_HALF = re.compile(r"^\s*,?\s*y\s+(?:medio|media)\b")
_ADDITIONAL_MEASURE = re.compile(r"^\s*,\s*" + _QUANTITY_NUMBER + r"\s*" + _MEASURE_UNIT + r"\b")
_POSTFIX_MEASURE = re.compile(r",\s*" + _QUANTITY_NUMBER + r"\s*" + _MEASURE_UNIT + r"(?:\s+y\s+(?:medio|media))?\s*$")


class GroceryAdapterError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        self.needs_input = code in {
            "invalid_items", "invalid_item", "unclean_item", "duplicate_item",
            "ambiguous_item", "quantity_conflict", "ambiguous_destination",
        }
        super().__init__(message)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _resource_id(value: Any) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _operation_id(value: Any) -> str:
    if not isinstance(value, str) or not _OPERATION_ID.fullmatch(value):
        raise GroceryAdapterError("invalid_operation_id", "La identidad de la operación no es válida")
    return value


def child_operation_id(parent_id: str, item_plan: dict) -> str:
    """Bind the full parent identity and full frozen item plan without truncation."""
    _operation_id(parent_id)
    if not isinstance(item_plan, dict):
        raise GroceryAdapterError("invalid_plan", "El plan de mercado no es válido")
    return "grocery-" + _hash({"parent_id": parent_id, "item_plan": item_plan})


def _load_helper(path: Path):
    # Compile exactly the verified bytes, avoiding a stale or substituted .pyc.
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != PINNED_HELPER_SHA256:
        raise GroceryAdapterError("helper_fingerprint_mismatch", "El helper de mercado cambió; no hice cambios")
    spec = importlib.util.spec_from_file_location("_pinned_grocery_helper", path)
    if spec is None or spec.loader is None:
        raise GroceryAdapterError("helper_unavailable", "No pude cargar el helper de mercado")
    helper = importlib.util.module_from_spec(spec)
    exec(compile(source, str(path), "exec"), helper.__dict__)
    return helper


class GroceryAdapter:
    def __init__(self, helper_path: Path, journal_path: Path):
        self.helper_path = Path(helper_path)
        self.journal_path = Path(journal_path)
        try:
            self.helper = _load_helper(self.helper_path)
            self.destination = self.helper.GROCERY_PAGE
            if not _resource_id(self.destination):
                raise ValueError("invalid destination")
            self._init_journal()
        except GroceryAdapterError:
            raise
        except Exception as exc:
            raise GroceryAdapterError("adapter_unavailable", "No pude abrir el adaptador de mercado") from exc

    @contextmanager
    def _db(self):
        conn = None
        try:
            conn = sqlite3.connect(self.journal_path, timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA synchronous=FULL")
            yield conn
        except sqlite3.Error as exc:
            raise GroceryAdapterError("journal_unavailable", "El diario de mercado no está disponible; no reintenté cambios") from exc
        finally:
            if conn is not None:
                conn.close()

    def _init_journal(self):
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS grocery_operations (
                operation_id TEXT PRIMARY KEY, plan_hash TEXT NOT NULL,
                plan_json TEXT NOT NULL, destination TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('prepared','dispatching','uncertain','committed','rejected')),
                candidate_id TEXT, receipt_json TEXT, error_code TEXT
            )""")
        self.journal_path.chmod(0o600)

    def _check_helper(self):
        try:
            actual = hashlib.sha256(self.helper_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise GroceryAdapterError("helper_unavailable", "No pude verificar el helper de mercado") from exc
        if actual != PINNED_HELPER_SHA256 or self.helper.GROCERY_PAGE != self.destination:
            raise GroceryAdapterError("helper_fingerprint_mismatch", "El helper o el destino de mercado cambió; no hice cambios")

    def _validate_plan(self, plan: dict) -> dict:
        if not isinstance(plan, dict) or set(plan) != _PLAN_FIELDS:
            raise GroceryAdapterError("invalid_plan", "El plan de mercado no es válido")
        value = {key: item for key, item in plan.items() if key != "plan_hash"}
        if (
            type(plan["version"]) is not int or plan["version"] != 1
            or plan["helper_sha256"] != PINNED_HELPER_SHA256
            or plan["destination"] != self.destination
            or not _resource_id(plan["parent_id"])
            or not isinstance(plan["action"], str) or plan["action"] not in {"created", "updated", "already_present"}
            or not isinstance(plan["item"], str) or not 1 <= len(plan["item"]) <= 1800
            or not isinstance(plan["expected_text"], str) or not plan["expected_text"]
            or not isinstance(plan["key"], str) or not plan["key"]
            or type(plan["expected_checked"]) is not bool
            or (plan["action"] == "created" and (plan["target_id"] is not None or plan["expected_checked"] is not False or plan["expected_text"] != plan["item"]))
            or (plan["action"] != "created" and not _resource_id(plan["target_id"]))
            or (plan["action"] == "updated" and plan["expected_checked"] is not True)
            or (plan["action"] == "already_present" and plan["expected_checked"] is not False)
            or plan["plan_hash"] != _hash(value)
        ):
            raise GroceryAdapterError("invalid_plan", "El plan o su destino cambió; no hice cambios")
        return json.loads(_json(plan))

    def _blocks(self) -> list[dict]:
        request = self.helper.notion_request
        pages = 0

        def checked_request(method, path, body=None):
            nonlocal pages
            if method != "GET" or not path.startswith("/blocks/") or "/children?" not in path or body is not None:
                raise ValueError("unexpected grocery traversal request")
            pages += 1
            if pages > 1000:
                raise ValueError("grocery traversal exceeds safe bound")
            data = request(method, path)
            if (
                not isinstance(data, dict) or data.get("object") != "list"
                or not isinstance(data.get("results"), list) or len(data["results"]) > 100
                or not all(isinstance(block, dict) for block in data["results"])
                or type(data.get("has_more")) is not bool
                or (data["has_more"] and (not isinstance(data.get("next_cursor"), str) or not 1 <= len(data["next_cursor"]) <= 2000))
                or (not data["has_more"] and data.get("next_cursor") is not None)
            ):
                raise ValueError("invalid grocery child-list response")
            return data

        # Keep the real helper's traversal and cursor handling, while refusing
        # malformed responses that it would otherwise treat as a partial list.
        # Every caller holds the shared capture lock during this scoped guard.
        self.helper.notion_request = checked_request
        try:
            blocks = self.helper.get_all_grocery_blocks()
        except Exception as exc:
            raise GroceryAdapterError("invalid_list", "No pude verificar la lista de mercado completa") from exc
        finally:
            self.helper.notion_request = request
        if not isinstance(blocks, list):
            raise GroceryAdapterError("invalid_list", "No pude verificar la lista de mercado completa")
        seen = set()
        for block in blocks:
            if not isinstance(block, dict) or not _resource_id(block.get("id")) or block["id"] in seen:
                raise GroceryAdapterError("invalid_list", "La lista de mercado contiene bloques ambiguos")
            seen.add(block["id"])
            if not _resource_id(block.get("_grocery_parent_id")):
                raise GroceryAdapterError("invalid_list", "No pude verificar el destino de un bloque de mercado")
            parent = block.get("parent", {})
            if (
                block.get("object") != "block" or not isinstance(block.get("type"), str)
                or type(block.get("has_children")) is not bool or not isinstance(parent, dict)
                or parent.get("type") not in {"page_id", "block_id"}
                or parent.get(parent["type"]) != block["_grocery_parent_id"]
            ):
                raise GroceryAdapterError("invalid_list", "No pude verificar la estructura de la lista de mercado")
        return blocks

    def _quantity_product(self, text: str) -> tuple[str, bool]:
        """Extract only bounded quantity syntax for conservative collisions.

        Work before legacy normalization, which removes decimal separators.
        Product conjunctions and qualifiers survive; only a recognized measure
        permits consuming its immediate ``y medio`` suffix. Never use this
        extraction as display text or a replacement for the pinned contract.
        """
        value = self.helper._GROCERY_PRICE_SUFFIX_RE.sub("", text).strip().lstrip("- ")
        value = "".join(char for char in unicodedata.normalize("NFD", value.lower()) if not unicodedata.combining(char))
        value = re.sub(r"\s+", " ", value)
        postfix = _POSTFIX_MEASURE.search(value)
        quantified = postfix is not None
        if postfix:
            value = value[:postfix.start()].rstrip()
        amount = _LEADING_AMOUNT.match(value) or _ATTACHED_AMOUNT.match(value)
        if amount:
            quantified = True
            remaining = value[amount.end():].lstrip()
            # The legacy list also supports an optional multiplication marker.
            remaining = re.sub(r"^x\s+", "", remaining)
            unit = _MEASURE_START.match(remaining)
            if unit:
                remaining = remaining[unit.end():]
                half = _MEASURE_HALF.match(remaining)
                if half:
                    remaining = remaining[half.end():]
                for _ in range(4):
                    more = _ADDITIONAL_MEASURE.match(remaining)
                    if more is None:
                        break
                    remaining = remaining[more.end():]
            value = re.sub(r"^de\s+", "", remaining.lstrip()).strip()
        return value, quantified

    def _keys(self, text: str) -> set[str]:
        keys = {self.helper.grocery_semantic_key(text)}
        product, quantified = self._quantity_product(text)
        if quantified and product:
            keys.add(self.helper.grocery_semantic_key(product))
        return keys - {""}

    def _item_plan(self, item: str, blocks: list[dict]) -> dict:
        if not isinstance(item, str) or not item.strip() or len(item) > 1800 or any(ord(c) < 32 for c in item):
            raise GroceryAdapterError("invalid_item", "Necesito un artículo de mercado válido")
        item = item.strip()
        if self.helper._clean_grocery_item(item) != item:
            raise GroceryAdapterError("unclean_item", "Necesito el nombre del artículo sin instrucciones de destino")
        keys = self._keys(item)
        if not keys:
            raise GroceryAdapterError("invalid_item", "Necesito un artículo de mercado válido")
        matches = [block for block in blocks if self.helper.grocery_text(block) and keys & self._keys(self.helper.grocery_text(block))]
        if len(matches) > 1:
            raise GroceryAdapterError("ambiguous_item", "Hay varios artículos equivalentes en la lista; necesito que aclares cuál usar")
        if matches:
            target = matches[0]
            text = self.helper.grocery_text(target)
            if (self._quantity_product(item)[1] or self.helper._rich_grocery_item(item)) and self.helper.norm(item) != self.helper.norm(text):
                raise GroceryAdapterError("quantity_conflict", "La cantidad solicitada difiere del artículo existente; necesito que la confirmes")
            checked = target.get("to_do", {}).get("checked")
            if type(checked) is not bool or target.get("archived") is True or target.get("in_trash") is True:
                raise GroceryAdapterError("invalid_target", "No pude verificar el artículo existente")
            action, target_id, parent_id = ("updated" if checked else "already_present"), target["id"], target["_grocery_parent_id"]
        else:
            others = [block for block in blocks if block.get("type") == "toggle" and self.helper.norm(self.helper._block_text(block)) == "otros"]
            selected = self.helper._other_grocery_toggle(blocks)
            if selected is None or len(others) != 1 or selected.get("archived") is True or selected.get("in_trash") is True:
                raise GroceryAdapterError("ambiguous_destination", "Necesito un único grupo Otros en la lista oficial")
            action, target_id, parent_id, text, checked = "created", None, selected["id"], item, False
        value = {
            "version": 1, "helper_sha256": PINNED_HELPER_SHA256,
            "destination": self.destination, "item": item,
            "key": self.helper.grocery_semantic_key(item), "action": action,
            "target_id": target_id, "parent_id": parent_id,
            "expected_text": text, "expected_checked": checked,
        }
        return dict(value, plan_hash=_hash(value))

    def prepare(self, items: list[str]) -> dict:
        """Read and freeze the entire batch before any journal claim or write."""
        if not isinstance(items, list) or not 1 <= len(items) <= 30:
            raise GroceryAdapterError("invalid_items", "Necesito entre uno y treinta artículos de mercado")
        self._check_helper()
        with self.helper.capture_operation_lock():
            blocks = self._blocks()
            plans = [self._item_plan(item, blocks) for item in items]
            seen: set[str] = set()
            for item in items:
                keys = self._keys(item)
                if seen & keys:
                    raise GroceryAdapterError("duplicate_item", "El lote contiene artículos equivalentes; necesito que los aclares")
                seen.update(keys)
        return {"version": 1, "helper_sha256": PINNED_HELPER_SHA256, "destination": self.destination, "items": plans}

    def _row(self, conn, operation_id: str, plan: dict):
        row = conn.execute("SELECT * FROM grocery_operations WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None:
            return None
        if row["plan_hash"] != plan["plan_hash"] or row["plan_json"] != _json(plan) or row["destination"] != plan["destination"]:
            raise GroceryAdapterError("operation_conflict", "La operación ya pertenece a otro artículo o destino")
        if row["status"] not in _STATES or (row["candidate_id"] is not None and not _resource_id(row["candidate_id"])):
            raise GroceryAdapterError("journal_corrupt", "El diario de mercado contiene un estado no válido")
        if (row["status"] in {"prepared", "rejected"} and row["candidate_id"] is not None) or (
            row["candidate_id"] is not None and plan["target_id"] is not None and row["candidate_id"] != plan["target_id"]
        ):
            raise GroceryAdapterError("journal_corrupt", "El diario de mercado contiene una identidad de bloque incompatible")
        if row["status"] == "committed":
            try:
                receipt = json.loads(row["receipt_json"])
                self._validate_receipt(receipt, operation_id, plan, row["candidate_id"])
            except (TypeError, ValueError, KeyError) as exc:
                raise GroceryAdapterError("journal_corrupt", "No pude verificar el recibo guardado de mercado") from exc
        elif row["receipt_json"] is not None:
            raise GroceryAdapterError("journal_corrupt", "El diario de mercado contiene un recibo sin confirmar")
        return row

    def _validate_receipt(self, receipt: dict, operation_id: str, plan: dict, candidate: str | None):
        expected = self._receipt(operation_id, plan, candidate)
        if not _resource_id(candidate) or _json(receipt) != _json(expected):
            raise ValueError("invalid receipt")

    def _receipt(self, operation_id: str, plan: dict, block_id: str | None) -> dict:
        return {
            "operation_id": operation_id, "plan_hash": plan["plan_hash"],
            "action": plan["action"], "write_committed": plan["action"] != "already_present",
            "block_id": block_id, "item": plan["expected_text"], "requested_item": plan["item"],
            "destination": plan["destination"], "parent_id": plan["parent_id"], "checked": False,
        }

    def _result(self, row) -> dict:
        if row is None:
            return {"status": "prepared", "write_committed": False, "code": "not_started"}
        status = row["status"]
        if status == "committed":
            receipt = json.loads(row["receipt_json"])
            return {"status": "committed", "write_committed": receipt["write_committed"], "receipt": receipt}
        if status in {"dispatching", "uncertain"}:
            return {"status": "uncertain", "write_committed": None, "code": row["error_code"] or "dispatch_interrupted"}
        return {"status": status, "write_committed": False, "code": row["error_code"] or "not_dispatched"}

    def _read(self, operation_id: str, plan: dict):
        with self._db() as conn:
            return self._row(conn, operation_id, plan)

    def register_operations(self, operations: list[tuple[str, dict]]) -> None:
        """Atomically persist every prepared child before its batch is frozen.

        This method only touches the local journal. A frozen batch can then
        distinguish a lost operation row from a child it has not dispatched.
        The caller must reject missing rows when resuming that frozen batch.
        """
        if not isinstance(operations, list) or not 1 <= len(operations) <= 30:
            raise GroceryAdapterError("invalid_operations", "El lote de operaciones de mercado no es válido")
        validated = []
        for operation in operations:
            if not isinstance(operation, (tuple, list)) or len(operation) != 2:
                raise GroceryAdapterError("invalid_operations", "El lote de operaciones de mercado no es válido")
            validated.append((_operation_id(operation[0]), self._validate_plan(operation[1])))
        with self._db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for operation_id, plan in validated:
                if self._row(conn, operation_id, plan) is None:
                    conn.execute("INSERT INTO grocery_operations VALUES (?, ?, ?, ?, 'prepared', NULL, NULL, NULL)",
                                 (operation_id, plan["plan_hash"], _json(plan), plan["destination"]))
            conn.commit()

    def _mark(self, operation_id: str, status: str, code: str | None = None, candidate: str | None = None):
        with self._db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Never downgrade a receipt, including if the caller lost its ACK.
            conn.execute("""UPDATE grocery_operations SET status=?, error_code=?,
                candidate_id=COALESCE(?, candidate_id) WHERE operation_id=? AND status!='committed'""",
                (status, code, candidate, operation_id))
            conn.commit()

    def _store_receipt(self, operation_id: str, plan: dict, block_id: str):
        receipt = self._receipt(operation_id, plan, block_id)
        with self._db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._row(conn, operation_id, plan)
            conn.execute("""UPDATE grocery_operations SET status='committed', candidate_id=?,
                receipt_json=?, error_code=NULL WHERE operation_id=? AND status!='rejected'""",
                (block_id, _json(receipt), operation_id))
            conn.commit()

    def _validate_block(self, block: dict, block_id: str, plan: dict):
        if not isinstance(block, dict):
            raise ValueError("invalid block")
        parent = block.get("parent", {})
        parent_id = parent.get(parent.get("type")) if isinstance(parent, dict) else None
        if (
            block.get("object") != "block" or block.get("id") != block_id
            or block.get("type") != "to_do" or self.helper.grocery_text(block) != plan["expected_text"]
            or block.get("to_do", {}).get("checked") is not False
            or block.get("archived") is True or block.get("in_trash") is True
            or parent.get("type") not in {"page_id", "block_id"}
            or parent_id != plan["parent_id"]
        ):
            raise ValueError("block readback does not match frozen plan")

    def _readback(self, block_id: str, plan: dict, *, verify_tree: bool = True):
        if verify_tree:
            matches = [block for block in self._blocks() if block["id"] == block_id]
            if len(matches) != 1 or matches[0]["_grocery_parent_id"] != plan["parent_id"]:
                raise ValueError("acknowledged block is outside the official destination")
        block = self.helper.notion_request("GET", "/blocks/" + quote(block_id, safe=""))
        self._validate_block(block, block_id, plan)

    def execute(self, operation_id: str, item_plan: dict) -> dict:
        operation_id, plan = _operation_id(operation_id), self._validate_plan(item_plan)
        with self.helper.capture_operation_lock():
            with self._db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = self._row(conn, operation_id, plan)
                if row is None:
                    conn.execute("INSERT INTO grocery_operations VALUES (?, ?, ?, ?, 'prepared', NULL, NULL, NULL)",
                                 (operation_id, plan["plan_hash"], _json(plan), plan["destination"]))
                elif row["status"] != "prepared":
                    conn.commit()
                    return self._result(row)
                conn.commit()
            try:
                self._check_helper()
                if self._item_plan(plan["item"], self._blocks()) != plan:
                    raise GroceryAdapterError("destination_or_item_drift", "El artículo o su grupo cambió; no hice cambios")
                if plan["action"] == "already_present":
                    self._readback(plan["target_id"], plan)
                    self._store_receipt(operation_id, plan, plan["target_id"])
                    return self._result(self._read(operation_id, plan))
            except Exception as exc:
                code = exc.code if isinstance(exc, GroceryAdapterError) else "preflight_failed"
                self._mark(operation_id, "rejected", code)
                return self._result(self._read(operation_id, plan))
            with self._db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._row(conn, operation_id, plan)
                claimed = conn.execute("UPDATE grocery_operations SET status='dispatching' WHERE operation_id=? AND status='prepared'", (operation_id,)).rowcount
                conn.commit()
            if claimed != 1:
                return self._result(self._read(operation_id, plan))
            try:
                if plan["action"] == "updated":
                    response = self.helper.notion_request("PATCH", "/blocks/" + quote(plan["target_id"], safe=""), {"to_do": {"checked": False}})
                    candidate = response.get("id") if isinstance(response, dict) else None
                    if candidate != plan["target_id"]:
                        raise ValueError("invalid update acknowledgement")
                else:
                    response = self.helper.notion_request("PATCH", "/blocks/" + quote(plan["parent_id"], safe="") + "/children", {
                        "children": [{"object": "block", "type": "to_do", "to_do": {
                            "rich_text": [{"type": "text", "text": {"content": plan["item"]}}],
                            "checked": False, "color": "default",
                        }}],
                    })
                    results = response.get("results") if isinstance(response, dict) else None
                    candidate = results[0].get("id") if isinstance(results, list) and len(results) == 1 and isinstance(results[0], dict) else None
                if not _resource_id(candidate):
                    raise ValueError("missing acknowledged block identity")
                self._mark(operation_id, "dispatching", candidate=candidate)
                self._readback(candidate, plan)
                self._store_receipt(operation_id, plan, candidate)
            except Exception:
                self._mark(operation_id, "uncertain", "mutation_or_readback_uncertain")
            return self._result(self._read(operation_id, plan))

    def status(self, operation_id: str, item_plan: dict) -> dict:
        """Return a durable receipt, or reconcile an ACKed ID without mutations."""
        operation_id, plan = _operation_id(operation_id), self._validate_plan(item_plan)
        row = self._read(operation_id, plan)
        if row is None or row["status"] not in {"dispatching", "uncertain"} or row["candidate_id"] is None:
            return self._result(row)
        with self.helper.capture_operation_lock():
            row = self._read(operation_id, plan)
            if row["status"] not in {"dispatching", "uncertain"}:
                return self._result(row)
            try:
                self._check_helper()
                self._readback(row["candidate_id"], plan, verify_tree=True)
                self._store_receipt(operation_id, plan, row["candidate_id"])
            except Exception:
                return self._result(row)
        return self._result(self._read(operation_id, plan))
