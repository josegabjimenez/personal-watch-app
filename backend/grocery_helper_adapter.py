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
_SEMANTIC_PLAN_FIELDS = _PLAN_FIELDS | {"catalog_snapshot", "batch_items", "batch_index", "batch_hash", "mark_missing"}
_CATALOG_FIELDS = frozenset({"id", "text", "checked", "parent_id"})
_GENERIC_HEADS = frozenset({
    "leche", "arroz", "huevo", "tomate", "banano", "banana", "palta", "aguacate", "papa", "patata", "azucar",
    "aceite", "frijol", "zanahoria", "cebolla", "yogur", "papel", "servilleta", "limon", "pollo", "polvo",
    "pavo", "coco", "coca", "harina", "avena", "mantequilla", "galleta", "queso", "detergente", "jabon",
    "cafe", "sal", "pan", "lenteja", "manzana", "pera", "fresa", "uva", "mango", "durazno", "mora", "platano", "champu",
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
            "invalid_catalog", "catalog_too_large", "catalog_drift", "invalid_candidate",
            "constraint_conflict", "bought_candidate",
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
            conn.execute("""CREATE TABLE IF NOT EXISTS grocery_semantic_batches (
                operation_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL,
                batch_index INTEGER NOT NULL
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
        if isinstance(plan, dict) and type(plan.get("version")) is int and plan["version"] == 2:
            return self._validate_semantic_plan(plan)
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

    def _validate_catalog(self, catalog: Any) -> list[dict]:
        """Validate a complete, bounded snapshot; never silently truncate it."""
        if not isinstance(catalog, list):
            raise GroceryAdapterError("invalid_catalog", "No pude verificar el catálogo de mercado completo")
        if len(catalog) > 200:
            raise GroceryAdapterError("catalog_too_large", "La lista tiene más de doscientos artículos; necesito acotar la consulta")
        seen = set()
        for candidate in catalog:
            if (
                not isinstance(candidate, dict) or set(candidate) != _CATALOG_FIELDS
                or not _resource_id(candidate.get("id")) or candidate["id"] in seen
                or not _resource_id(candidate.get("parent_id"))
                or type(candidate.get("checked")) is not bool
                or not isinstance(candidate.get("text"), str)
                or not 1 <= len(candidate["text"]) <= 1800 or not candidate["text"].strip()
                or any(ord(char) < 32 for char in candidate["text"])
            ):
                raise GroceryAdapterError("invalid_catalog", "La lista contiene un artículo que no pude verificar; necesito aclararlo")
            seen.add(candidate["id"])
        return sorted((dict(candidate) for candidate in catalog), key=lambda candidate: candidate["id"])

    def _catalog_from_blocks(self, blocks: list[dict]) -> list[dict]:
        candidates = []
        for block in blocks:
            if block["type"] != "to_do":
                continue
            value = block.get("to_do")
            if (
                not isinstance(value, dict) or type(value.get("checked")) is not bool
                or block.get("archived") is not False or block.get("in_trash") is not False
                or not isinstance(value.get("rich_text"), list)
                or not all(isinstance(part, dict) and isinstance(part.get("plain_text"), str)
                           for part in value["rich_text"])
            ):
                raise GroceryAdapterError("invalid_catalog", "La lista contiene un artículo archivado o no verificable")
            candidates.append({"id": block["id"], "text": self.helper.grocery_text(block),
                               "checked": value["checked"], "parent_id": block["_grocery_parent_id"]})
        return self._validate_catalog(candidates)

    def catalog(self) -> list[dict]:
        """Return every grocery to-do candidate without making any mutations."""
        self._check_helper()
        with self.helper.capture_operation_lock():
            try:
                return self._catalog_from_blocks(self._blocks())
            except GroceryAdapterError as exc:
                if exc.code == "invalid_list":
                    raise GroceryAdapterError("invalid_catalog", "No pude verificar el catálogo de mercado completo") from exc
                raise

    @staticmethod
    def _one_typo(left: str, right: str) -> bool:
        # Only a product head may tolerate one edit. Qualifiers never do.
        if min(len(left), len(right)) < 4 or abs(len(left) - len(right)) > 1:
            return False
        if len(left) == len(right):
            differences = [index for index in range(len(left)) if left[index] != right[index]]
            return len(differences) <= 1 or (len(differences) == 2
                and differences[1] == differences[0] + 1
                and left[differences[0]] == right[differences[1]]
                and left[differences[1]] == right[differences[0]])
        shorter, longer = sorted((left, right), key=len)
        return any(longer[:index] + longer[index + 1:] == shorter for index in range(len(longer)))

    def _product_signature(self, text: str) -> tuple[str, tuple[str, ...]]:
        product, _ = self._quantity_product(text)
        product = re.sub(r"\bsin lactosa\b", "deslactosada", product)
        product = re.sub(r"\bdeslactosados?\b", "deslactosada", product)
        tokens = re.findall(r"[a-z0-9]+", product)
        while tokens and tokens[0] in {"el", "la", "los", "las", "un", "una"}:
            tokens.pop(0)
        singular = {"huevos": "huevo", "papas": "papa", "patatas": "patata",
                    "bananas": "banana", "bananos": "banano", "panes": "pan",
                    "limones": "limon", "frijoles": "frijol", "frijolitos": "frijol"}
        if not tokens:
            return "", ()
        head = tokens[0]
        tokens[0] = singular.get(head, head[:-1] if head.endswith("s") and head[:-1] in _GENERIC_HEADS else head)
        # Product plurals are distinct from brands. Never stem arbitrary
        # qualifiers: Parmalat/Parmalats and Shoulders/Shoulder stay distinct.
        reviewed_qualifiers = {"enteras": "entera", "enteros": "entero", "chontos": "chonto",
                               "deslactosadas": "deslactosada", "deslactosados": "deslactosada"}
        tokens[1:] = [reviewed_qualifiers.get(token, token) for token in tokens[1:]]
        # This is a veto guard after the model's ID proposal, never a selector.
        # Unknown synonyms deliberately need clarification. Every remaining
        # token (brand, diet, variety, size, conjunction) must be preserved.
        aliases = {"patata": "papa", "banana": "banano", "jitomate": "tomate",
                   "aguacate": "palta"}
        qualifiers = tokens[1:]
        # Keep modifier associations: "chocolate negro relleno blanco" and
        # "chocolate blanco relleno negro" are different products. The known
        # lactose attribute may move past a brand only without negation or
        # relation words that could change what the attribute describes.
        if not ({"no", "sin", "con", "ni", "excepto", "de", "y"} & set(qualifiers)):
            lactose = [token for token in qualifiers if token == "deslactosada"]
            qualifiers = [token for token in qualifiers if token != "deslactosada"] + lactose
        return aliases.get(tokens[0], tokens[0]), tuple(qualifiers)

    def _same_product_head(self, left: str, right: str) -> bool:
        if left == right:
            return True
        # A one-edit proposal is only verifiable against a known generic
        # product head. Brand-only names and two distinct known products must
        # stay exact, even when the model considers them similar.
        known = left if left in _GENERIC_HEADS else right if right in _GENERIC_HEADS else ""
        return bool(known and len(known) >= 5 and (left in _GENERIC_HEADS) != (right in _GENERIC_HEADS)
                    and self._one_typo(left, right))

    def _semantic_equivalent(self, left: str, right: str) -> bool:
        left_head, left_qualifiers = self._product_signature(left)
        right_head, right_qualifiers = self._product_signature(right)
        return bool(left_head and right_head and self._same_product_head(left_head, right_head)
                    and left_qualifiers == right_qualifiers)

    def _semantic_destination(self, blocks: list[dict]) -> str:
        others = [block for block in blocks if block.get("type") == "toggle"
                  and self.helper.norm(self.helper._block_text(block)) == "otros"]
        if (len(others) != 1 or others[0].get("archived") is not False
                or others[0].get("in_trash") is not False):
            raise GroceryAdapterError("ambiguous_destination", "Necesito un único grupo Otros en la lista oficial")
        return others[0]["id"]

    def _semantic_item_core(self, selection: dict, catalog: list[dict], new_parent: str, *, mark_missing: bool = False) -> dict:
        if (not isinstance(selection, dict) or set(selection) != {"text", "candidate_id"}
                or not isinstance(selection.get("text"), str)):
            raise GroceryAdapterError("invalid_items", "La selección de artículos de mercado no es válida")
        item, candidate_id = selection["text"], selection["candidate_id"]
        if (not item.strip() or item != item.strip() or len(item) > 1800
                or any(ord(char) < 32 for char in item) or not self._keys(item)):
            raise GroceryAdapterError("invalid_item", "Necesito un artículo de mercado válido")
        if self.helper._clean_grocery_item(item) != item:
            raise GroceryAdapterError("unclean_item", "Necesito el nombre del artículo sin instrucciones de destino")
        product, _ = self._quantity_product(item)
        head, qualifiers = self._product_signature(item)
        product_words = re.findall(r"[a-z0-9]+", product)
        has_product = any(re.search(r"[a-z]", word)
                          and not _MEASURE_START.fullmatch(word)
                          and not _LEADING_AMOUNT.fullmatch(word)
                          and word not in {"de", "y", "el", "la", "los", "las", "unos", "unas"}
                          for word in product_words)
        if not re.search(r"[a-z]", head) or not has_product:
            raise GroceryAdapterError("invalid_item", "Necesito el producto además de su cantidad o unidad")
        family = [candidate for candidate in catalog
                  if self._same_product_head(head, self._product_signature(candidate["text"])[0])]
        equivalents = [candidate for candidate in catalog if self._semantic_equivalent(item, candidate["text"])]
        if len(equivalents) > 1:
            raise GroceryAdapterError("ambiguous_item", "Hay varios artículos equivalentes; necesito que aclares cuál usar")
        if not qualifiers and any(self._product_signature(candidate["text"])[1] for candidate in family):
            raise GroceryAdapterError("ambiguous_item", "La lista tiene una presentación de ese producto; necesito que indiques cuál")
        if candidate_id is None:
            if equivalents:
                raise GroceryAdapterError("invalid_candidate", "Ese producto puede existir en la lista; necesito confirmar el artículo")
            action, parent_id, text, checked = "created", new_parent, item, False
        else:
            if not _resource_id(candidate_id):
                raise GroceryAdapterError("invalid_candidate", "La identidad del artículo seleccionado no es válida")
            matches = [candidate for candidate in catalog if candidate["id"] == candidate_id]
            if len(matches) != 1:
                raise GroceryAdapterError("invalid_candidate", "El artículo seleccionado no pertenece al catálogo verificado")
            target = matches[0]
            text = target["text"]
            if not self._semantic_equivalent(item, text):
                raise GroceryAdapterError("constraint_conflict", "La marca, variedad o presentación no coincide; necesito que la aclares")
            if (self._quantity_product(item)[1] or self.helper._rich_grocery_item(item)) and self.helper.norm(item) != self.helper.norm(text):
                raise GroceryAdapterError("quantity_conflict", "La cantidad solicitada difiere del artículo existente; necesito que la confirmes")
            checked = target["checked"]
            if checked and not mark_missing:
                raise GroceryAdapterError("bought_candidate", "Ese artículo figura como comprado; necesito que confirmes si vuelve a faltar")
            action, parent_id = ("updated" if checked else "already_present"), target["parent_id"]
        return {"version": 2, "mark_missing": mark_missing, "helper_sha256": PINNED_HELPER_SHA256,
                "destination": self.destination, "item": item,
                "key": self.helper.grocery_semantic_key(item), "action": action,
                "target_id": candidate_id, "parent_id": parent_id,
                "expected_text": text, "expected_checked": checked}

    def _semantic_batch(self, items: Any, catalog: list[dict], new_parent: str, *, mark_missing: bool = False) -> list[dict]:
        if not isinstance(items, list) or not 1 <= len(items) <= 30:
            raise GroceryAdapterError("invalid_items", "Necesito entre uno y treinta artículos de mercado")
        if type(mark_missing) is not bool:
            raise GroceryAdapterError("invalid_items", "La confirmación de artículos faltantes no es válida")
        cores = [self._semantic_item_core(item, catalog, new_parent, mark_missing=mark_missing) for item in items]
        if len(catalog) + sum(core["action"] == "created" for core in cores) > 200:
            raise GroceryAdapterError("catalog_too_large", "El pedido supera el límite verificable del catálogo; necesito acotarlo")
        for index, current in enumerate(cores):
            for prior in cores[:index]:
                if ((current["target_id"] is not None and current["target_id"] == prior["target_id"])
                        or (current["target_id"] is None and prior["target_id"] is None
                            and self._semantic_equivalent(current["item"], prior["item"]))):
                    raise GroceryAdapterError("duplicate_item", "El lote repite un artículo; necesito que lo aclares")
        batch_hash = _hash({"catalog": catalog, "items": items, "mark_missing": mark_missing})
        plans = []
        for index, core in enumerate(cores):
            value = dict(core, catalog_snapshot=catalog, batch_items=items,
                         batch_index=index, batch_hash=batch_hash)
            plans.append(dict(value, plan_hash=_hash(value)))
        return plans

    def prepare_semantic(self, items: list[dict], catalog: list[dict], *, mark_missing: bool = False) -> dict:
        """Freeze grounded model proposals against the exact complete catalog.

        This method performs no inference. The caller validates that every text
        is an exact substring of the request before proposing an existing ID.
        Only the trusted caller may attest explicit missing intent using
        mark_missing; the model's item schema cannot provide that flag.
        """
        snapshot = self._validate_catalog(catalog)
        self._check_helper()
        with self.helper.capture_operation_lock():
            blocks = self._blocks()
            if self._catalog_from_blocks(blocks) != snapshot:
                raise GroceryAdapterError("catalog_drift", "La lista cambió durante la consulta; necesito revisar el pedido de nuevo")
            # A destination is only needed if the proposal actually adds a row.
            creates = isinstance(items, list) and any(isinstance(item, dict) and item.get("candidate_id") is None for item in items)
            new_parent = self._semantic_destination(blocks) if creates else self.destination
            plans = self._semantic_batch(items, snapshot, new_parent, mark_missing=mark_missing)
        return {"version": 2, "helper_sha256": PINNED_HELPER_SHA256,
                "destination": self.destination, "items": plans}

    def _validate_semantic_plan(self, plan: dict) -> dict:
        try:
            if (set(plan) != _SEMANTIC_PLAN_FIELDS or type(plan["batch_index"]) is not int
                    or type(plan["mark_missing"]) is not bool):
                raise ValueError("invalid semantic plan fields")
            snapshot = self._validate_catalog(plan["catalog_snapshot"])
            if snapshot != plan["catalog_snapshot"] or not _resource_id(plan["parent_id"]):
                raise ValueError("invalid semantic snapshot")
            plans = self._semantic_batch(plan["batch_items"], snapshot, plan["parent_id"], mark_missing=plan["mark_missing"])
            if not 0 <= plan["batch_index"] < len(plans) or plans[plan["batch_index"]] != plan:
                raise ValueError("semantic plan changed")
        except (GroceryAdapterError, TypeError, ValueError, KeyError, IndexError) as exc:
            raise GroceryAdapterError("invalid_plan", "El plan semántico de mercado no es válido") from exc
        return json.loads(_json(plan))

    def _semantic_preflight(self, operation_id: str, plan: dict, blocks: list[dict]) -> None:
        expected = list(plan["catalog_snapshot"])
        with self._db() as conn:
            membership = conn.execute("SELECT * FROM grocery_semantic_batches WHERE operation_id=?", (operation_id,)).fetchone()
            if membership is None and len(plan["batch_items"]) > 1:
                raise GroceryAdapterError("catalog_drift", "El lote completo debe registrarse antes de ejecutar sus artículos")
            if membership is not None:
                if membership["batch_index"] != plan["batch_index"]:
                    raise GroceryAdapterError("journal_corrupt", "El diario contiene un orden de lote incompatible")
                siblings = conn.execute("""SELECT o.* FROM grocery_operations o
                    JOIN grocery_semantic_batches b ON b.operation_id=o.operation_id
                    WHERE b.batch_id=? AND b.batch_index<? ORDER BY b.batch_index""",
                    (membership["batch_id"], plan["batch_index"])).fetchall()
                # An interrupted sibling must be reconciled before continuing.
                if len(siblings) != plan["batch_index"]:
                    raise GroceryAdapterError("catalog_drift", "No pude verificar los artículos anteriores del lote")
                for sibling in siblings:
                    sibling_plan = self._validate_plan(json.loads(sibling["plan_json"]))
                    checked_row = self._row(conn, sibling["operation_id"], sibling_plan)
                    if (sibling_plan.get("batch_hash") != plan["batch_hash"] or checked_row["status"] != "committed"):
                        raise GroceryAdapterError("catalog_drift", "Necesito confirmar los artículos anteriores del lote")
                    if sibling_plan["action"] == "created":
                        expected.append({"id": checked_row["candidate_id"], "text": sibling_plan["expected_text"],
                                         "checked": False, "parent_id": sibling_plan["parent_id"]})
                    elif sibling_plan["action"] == "updated":
                        matches = [candidate for candidate in expected if candidate["id"] == sibling_plan["target_id"]]
                        if len(matches) != 1 or matches[0]["checked"] is not True:
                            raise GroceryAdapterError("catalog_drift", "No pude verificar el artículo que volvió a faltar")
                        expected = [dict(candidate, checked=False) if candidate["id"] == sibling_plan["target_id"] else candidate
                                    for candidate in expected]
        if self._catalog_from_blocks(blocks) != sorted(expected, key=lambda candidate: candidate["id"]):
            raise GroceryAdapterError("catalog_drift", "La lista de mercado cambió; no hice más cambios")
        if plan["action"] == "created" and self._semantic_destination(blocks) != plan["parent_id"]:
            raise GroceryAdapterError("catalog_drift", "El grupo de destino cambió; no hice cambios")

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
        semantic = any(plan["version"] == 2 for _, plan in validated)
        if semantic:
            first = validated[0][1]
            if (any(plan["version"] != 2 for _, plan in validated)
                    or len({operation_id for operation_id, _ in validated}) != len(validated)
                    or len(validated) != len(first["batch_items"])
                    or any(plan["batch_index"] != index or plan["batch_hash"] != first["batch_hash"]
                           for index, (_, plan) in enumerate(validated))
                    or any(plan["action"] == "created" and plan["parent_id"] != next(
                        (candidate["parent_id"] for _, candidate in validated if candidate["action"] == "created"), plan["parent_id"])
                        for _, plan in validated)):
                raise GroceryAdapterError("invalid_operations", "Necesito registrar el lote semántico completo y en orden")
            batch_id = _hash({"operations": [(operation_id, plan["plan_hash"]) for operation_id, plan in validated]})
        with self._db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for operation_id, plan in validated:
                if self._row(conn, operation_id, plan) is None:
                    conn.execute("INSERT INTO grocery_operations VALUES (?, ?, ?, ?, 'prepared', NULL, NULL, NULL)",
                                 (operation_id, plan["plan_hash"], _json(plan), plan["destination"]))
                if semantic:
                    membership = conn.execute("SELECT * FROM grocery_semantic_batches WHERE operation_id=?", (operation_id,)).fetchone()
                    if membership is not None and (membership["batch_id"] != batch_id or membership["batch_index"] != plan["batch_index"]):
                        raise GroceryAdapterError("operation_conflict", "La operación ya pertenece a otro lote semántico")
                    conn.execute("INSERT OR IGNORE INTO grocery_semantic_batches VALUES (?, ?, ?)",
                                 (operation_id, batch_id, plan["batch_index"]))
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
                blocks = self._blocks()
                if plan["version"] == 2:
                    self._semantic_preflight(operation_id, plan, blocks)
                elif self._item_plan(plan["item"], blocks) != plan:
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
