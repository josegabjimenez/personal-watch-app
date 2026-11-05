#!/usr/bin/env python3
"""Deterministic, idempotent Apple Watch adapter for simple Firefly expenses.

Only one-movement cash/asset withdrawals are supported. Card/FP, transfers,
deposits, liabilities, installments, and ambiguous captures fail closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BOGOTA = ZoneInfo("America/Bogota")
ADAPTER_VERSION = 3
DEFAULT_SOURCE_ACCOUNT = "Cuenta Ahorros Banco Bogotá"
MAX_AMOUNT_COP = 100_000_000
PAGE_LIMIT = 500
OPERATION_ID_RE = re.compile(r"exp_[0-9a-f]{32}")

MONEY_RE = re.compile(
    r"(?<![\w])(?:\$\s*)?"
    r"(?P<number>\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d{1,2})?)"
    r"\s*(?P<suffix>mil|k|cop|pesos?)?(?![\w])",
    re.I,
)
CARD_OR_MULTIWRITE_RE = re.compile(
    r"\b(?:fp|futuro\s+pago|tc\s*[123]|tarjeta|credito|crédito|cuotas?|"
    r"abono\s+a\s+tarjeta|pago\s+de\s+tarjeta)\b",
    re.I,
)
NON_EXPENSE_FLOW_RE = re.compile(
    r"\b(?:transferencia|transferir|transfiere|deposito|depósito|ingreso|salario|"
    r"prestamo|préstamo|retiro|mover\s+dinero)\b",
    re.I,
)
FOREIGN_CURRENCY_RE = re.compile(
    r"\b(?:usd|eur|gbp|mxn|brl|cad|aud|jpy|cny|chf|pen|clp|ars|uyu|"
    r"d[oó]lares?|euros?|libras?|yenes?|reales|soles|bol[ií]vares|"
    r"pesos\s+(?:mexicanos|chilenos|argentinos|uruguayos)|moneda\s+extranjera)\b|US\$|[€£¥]",
    re.I,
)
UNSAFE_OVERRIDE_RE = re.compile(
    r"\b(?:ignora\s+(?:las\s+)?reglas|crea\s+(?:una\s+)?categor[ií]a|"
    r"crea\s+(?:un\s+)?presupuesto|salta\s+(?:la\s+)?confirmaci[oó]n)\b",
    re.I,
)
EXPLICIT_DATE_RE = re.compile(
    r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|"
    r"\b(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\b",
    re.I,
)
ACCOUNT_CLAUSE_RE = re.compile(
    r"\b(?:por|con|desde|en)\s+(?:mi\s+)?(?:la\s+)?(?:cuenta\s+(?:de\s+)?)?"
    r"(?:nequi|nike|nicky|banco\s+(?:de\s+)?bogot[aá]|efectivo|cash)\b",
    re.I,
)
LEADING_EXPENSE_RE = re.compile(
    r"^\s*(?:registra(?:r)?|anota(?:r)?|gasto(?:\s+de)?|pagu[eé]|pago(?:\s+de)?|"
    r"compr[eé]|compra(?:\s+de)?|gast[eé])\b\s*",
    re.I,
)
STOPWORDS = {
    "a", "al", "con", "de", "del", "el", "en", "la", "las", "lo", "los",
    "mi", "mis", "para", "por", "que", "un", "una", "unos", "unas", "y",
    "hoy", "ayer", "gasto", "compra", "pago",
}

KNOWN_MAPPINGS = (
    {
        "keywords": {"mercado", "supermercado", "d1", "canaveral", "ara", "exito", "jumbo", "fruver"},
        "category": "Home",
        "budget": "Mercado",
        "destination": "Home",
        "tag": "Mercado",
    },
    {
        "keywords": {"uber", "didi", "taxi", "bus", "transporte", "gasolina", "peaje"},
        "category": "Transport",
        "budget": "Transporte",
        "destination": "Transport",
    },
    {
        "keywords": {"almuerzo", "comida", "restaurante", "desayuno", "cena", "sandwich", "hamburguesa"},
        "category": "Food",
        "budget": "Comidas afuera",
        "destination": "Food",
    },
    {
        "keywords": {"chatgpt", "openai", "suscripcion", "netflix", "spotify", "icloud"},
        "category": "Subscriptions",
        "budget": "Suscripciones y Herramientas",
        "destination": "Subscriptions",
    },
    {
        "keywords": {"medicina", "medicamento", "medicamentos", "doctor", "consulta", "farmacia", "salud"},
        "category": "Health",
        "budget": "Salud y Seguros",
        "destination": "Health",
    },
)


class FireflyError(RuntimeError):
    """Sanitized Firefly transport/API failure."""


class RejectHTTPRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def open_url_without_redirects(req: urllib.request.Request, *, timeout: int):
    return urllib.request.build_opener(RejectHTTPRedirects()).open(req, timeout=timeout)


def normalize_firefly_base_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw or any(ord(char) < 33 or ord(char) == 127 for char in raw):
        raise FireflyError("firefly_unsafe_base_url")
    try:
        parsed = urllib.parse.urlsplit(raw)
        # Accessing ``port`` also rejects malformed/out-of-range ports.
        _ = parsed.port
    except ValueError as error:
        raise FireflyError("firefly_unsafe_base_url") from error
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (scheme == "http" and hostname not in {"127.0.0.1", "localhost", "::1"})
    ):
        raise FireflyError("firefly_unsafe_base_url")
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit((scheme, parsed.netloc, path, "", ""))


class FireflyGateway:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: int = 30):
        raw_base_url = base_url if base_url is not None else os.getenv("FIREFLY_BASE_URL", "")
        self.base_url = normalize_firefly_base_url(raw_base_url)
        resolved_token = token if token is not None else os.getenv("FIREFLY_API_TOKEN", "")
        token_file = os.getenv("FIREFLY_API_TOKEN_FILE", "").strip()
        if not resolved_token and token_file:
            try:
                resolved_token = Path(token_file).read_text(encoding="utf-8").strip()
            except OSError as error:
                raise FireflyError("firefly_token_file_unavailable") from error
        self.token = resolved_token
        self.timeout = timeout
        if not self.token:
            raise FireflyError("firefly_not_configured")

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query = ""
        if params:
            clean = {key: value for key, value in params.items() if value not in (None, "")}
            if clean:
                query = "?" + urllib.parse.urlencode(clean)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "Hermes-Mobile-Expense/1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{self.base_url}{path}{query}", data=body, method=method, headers=headers)
        try:
            with open_url_without_redirects(req, timeout=self.timeout) as response:
                raw = response.read()
                if not raw:
                    raise FireflyError("firefly_unexpected_response")
                try:
                    decoded = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise FireflyError("firefly_unexpected_response") from error
                if not isinstance(decoded, dict):
                    raise FireflyError("firefly_unexpected_response")
                return decoded
        except urllib.error.HTTPError as error:
            raise FireflyError(f"firefly_http_{error.code}") from error
        except (OSError, ValueError, urllib.error.URLError) as error:
            raise FireflyError("firefly_unavailable") from error

    @staticmethod
    def _collection(data: dict[str, Any]) -> list[dict[str, Any]]:
        raw_rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(raw_rows, list):
            raise FireflyError("firefly_unexpected_response")
        rows: list[dict[str, Any]] = []
        for row in raw_rows:
            if not isinstance(row, dict) or not str(row.get("id") or ""):
                raise FireflyError("firefly_unexpected_response")
            attrs = row.get("attributes")
            if not isinstance(attrs, dict):
                raise FireflyError("firefly_unexpected_response")
            rows.append({"id": str(row["id"]), **attrs})
        return rows

    @staticmethod
    def _flatten_transactions(data: dict[str, Any]) -> list[dict[str, Any]]:
        raw_groups = data.get("data") if isinstance(data, dict) else None
        if not isinstance(raw_groups, list):
            raise FireflyError("firefly_unexpected_response")
        rows: list[dict[str, Any]] = []
        for group in raw_groups:
            if not isinstance(group, dict):
                raise FireflyError("firefly_unexpected_response")
            group_id = str(group.get("id") or "")
            attrs = group.get("attributes")
            if not group_id or not isinstance(attrs, dict):
                raise FireflyError("firefly_unexpected_response")
            transactions = attrs.get("transactions")
            if not isinstance(transactions, list):
                raise FireflyError("firefly_unexpected_response")
            for tx in transactions:
                if not isinstance(tx, dict) or not str(
                    tx.get("transaction_journal_id") or tx.get("journal_id") or ""
                ):
                    raise FireflyError("firefly_unexpected_response")
                rows.append({"group_id": group_id, **tx})
        return rows

    def load_catalog(self) -> dict[str, list[dict[str, Any]]]:
        accounts = self._collection(self._request("GET", "/api/v1/accounts", params={"limit": 500}))
        currencies = self._collection(self._request("GET", "/api/v1/currencies", params={"limit": 500}))
        categories = self._collection(self._request("GET", "/api/v1/categories", params={"limit": 500}))
        budgets = self._collection(self._request("GET", "/api/v1/budgets", params={"limit": 500}))
        tags_raw = self._collection(self._request("GET", "/api/v1/tags", params={"limit": 500}))
        tags = [{**row, "name": row.get("tag") or row.get("name")} for row in tags_raw]
        return {"accounts": accounts, "currencies": currencies, "categories": categories, "budgets": budgets, "tags": tags}

    def search_transactions(self, query: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        try:
            found.extend(
                self._flatten_transactions(
                    self._request(
                        "GET",
                        "/api/v1/search/transactions",
                        params={"query": query, "limit": 100},
                    )
                )
            )
        except FireflyError:
            pass
        # Firefly text search can miss aliases or tag-only matches. Add a bounded
        # recent window and score locally; this remains GET-only.
        recent = self._request("GET", "/api/v1/transactions", params={"limit": PAGE_LIMIT, "page": 1})
        found.extend(self._flatten_transactions(recent))
        unique: dict[str, dict[str, Any]] = {}
        for row in found:
            key = str(row.get("transaction_journal_id") or row.get("journal_id") or "")
            if key:
                unique[key] = row
        return list(unique.values())

    def find_by_external_id(self, external_id: str, transaction_date: str | None = None) -> list[dict[str, Any]]:
        """Exhaustively resolve one stable operation identity.

        Firefly versions differ in how strongly they apply ``external_id``.
        Keep the server-side filter, but page the complete filtered collection
        and verify every row locally.  If pagination cannot be proven complete,
        fail closed instead of declaring the operation absent.
        """
        matches: list[dict[str, Any]] = []
        page = 1
        prior_page_signature: tuple[str, ...] | None = None
        while True:
            data = self._request(
                "GET",
                "/api/v1/transactions",
                params={
                    "external_id": external_id,
                    "limit": PAGE_LIMIT,
                    "page": page,
                },
            )
            rows = self._flatten_transactions(data)
            matches.extend(row for row in rows if str(row.get("external_id") or "") == external_id)
            meta = data.get("meta")
            if meta is None:
                pagination: dict[str, Any] = {}
            elif not isinstance(meta, dict):
                raise FireflyError("firefly_unexpected_response")
            else:
                raw_pagination = meta.get("pagination")
                if raw_pagination is None:
                    pagination = {}
                elif not isinstance(raw_pagination, dict):
                    raise FireflyError("firefly_unexpected_response")
                else:
                    pagination = raw_pagination
            raw_total_pages = pagination.get("total_pages")
            if raw_total_pages not in (None, ""):
                try:
                    total_pages = int(raw_total_pages)
                except (TypeError, ValueError) as error:
                    raise FireflyError("firefly_pagination_incomplete") from error
                if total_pages < 0:
                    raise FireflyError("firefly_pagination_incomplete")
                if page >= total_pages:
                    break
                page += 1
                continue

            raw_groups = data.get("data", []) if isinstance(data, dict) else []
            if not isinstance(raw_groups, list):
                raise FireflyError("firefly_pagination_incomplete")
            if not raw_groups or len(raw_groups) < PAGE_LIMIT:
                break
            page_signature = tuple(
                str(row.get("id") or "")
                for row in raw_groups
                if isinstance(row, dict)
            )
            if not page_signature or page_signature == prior_page_signature:
                raise FireflyError("firefly_pagination_incomplete")
            prior_page_signature = page_signature
            page += 1
        return matches

    def create_withdrawal(self, transaction: dict[str, Any]) -> dict[str, Any]:
        data = self._request("POST", "/api/v1/transactions", payload={"transactions": [transaction]})
        rows = self._flatten_transactions(data)
        if len(rows) != 1:
            raise FireflyError("firefly_unexpected_create_response")
        return rows[0]


def fold_text(value: Any) -> str:
    folded = "".join(
        char
        for char in unicodedata.normalize("NFKD", str(value or ""))
        if not unicodedata.combining(char)
    ).lower()
    return re.sub(r"\s+", " ", folded).strip()


def _parse_money_token(number: str, suffix: str | None) -> int | None:
    suffix = fold_text(suffix)
    try:
        if suffix in {"mil", "k"}:
            if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", number):
                return None
            value = Decimal(number.replace(",", ".")) * 1000
            if value != value.to_integral_value():
                return None
            amount = int(value)
        elif re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", number):
            amount = int(re.sub(r"[^0-9]", "", number))
        elif re.fullmatch(r"\d+", number):
            amount = int(number)
        else:
            return None
    except (InvalidOperation, ValueError):
        return None
    if amount < 1000 or amount > MAX_AMOUNT_COP:
        return None
    return amount


def amount_candidates(text: str) -> list[int]:
    values: list[int] = []
    for match in MONEY_RE.finditer(text or ""):
        amount = _parse_money_token(match.group("number"), match.group("suffix"))
        if amount is not None:
            values.append(amount)
    return values


def parse_amount_cop(text: str) -> int | None:
    # Count every numeric amount before applying the supported COP range. A
    # second small/large amount must not disappear and authorize the first.
    matches = list(MONEY_RE.finditer(text or ""))
    if len(matches) != 1:
        return None
    match = matches[0]
    return _parse_money_token(match.group("number"), match.group("suffix"))


def unsupported_reason(text: str) -> str | None:
    if UNSAFE_OVERRIDE_RE.search(text or ""):
        return "unsafe_override"
    if CARD_OR_MULTIWRITE_RE.search(text or ""):
        return "card_or_multiwrite_unsupported"
    if NON_EXPENSE_FLOW_RE.search(text or ""):
        return "non_expense_flow_unsupported"
    if FOREIGN_CURRENCY_RE.search(text or ""):
        return "foreign_currency_unsupported"
    return None


def extract_account_name(text: str) -> str:
    normalized = fold_text(text)
    hints: set[str] = set()
    if re.search(r"\b(?:por|con|desde|en)\s+(?:mi\s+)?(?:la\s+)?(?:cuenta\s+(?:de\s+)?)?(?:nequi|nike|nicky)\b", normalized):
        hints.add("Nequi")
    if re.search(r"\b(?:por|con|desde|en)\s+(?:mi\s+)?(?:la\s+)?(?:cuenta\s+(?:de\s+)?)?banco\s+(?:de\s+)?bogota\b", normalized):
        hints.add(DEFAULT_SOURCE_ACCOUNT)
    if re.search(r"\b(?:por|con|desde|en)\s+(?:mi\s+)?(?:el\s+)?(?:efectivo|cash)\b|\ben efectivo\b", normalized):
        hints.add("Cash")
    if len(hints) > 1:
        return ""
    # An explicit payment account never falls back to the default. Merchants
    # after "en" remain concept text unless a bank/account cue is present.
    remaining = ACCOUNT_CLAUSE_RE.sub(" ", normalized)
    if re.search(
        r"\b(?:por|con|desde)\s+\S|\ben\s+mi\s+\S|\b(?:cuenta|banco|bancolombia|daviplata|davivienda|nu|nubank|rappipay|paypal)\b",
        remaining,
    ):
        return ""
    return next(iter(hints), DEFAULT_SOURCE_ACCOUNT)


def account_currency_code(account: dict[str, Any], catalog: dict[str, Any]) -> str | None:
    """Resolve existing account currency and reject contradictory metadata."""
    raw_code = account.get("currency_code")
    code = str(raw_code or "").strip().upper()
    currency_id = str(account.get("currency_id") or "").strip()
    if currency_id:
        matches = [row for row in catalog.get("currencies", []) if str(row.get("id") or "") == currency_id]
        if len(matches) != 1:
            return None
        resolved = str(matches[0].get("code") or matches[0].get("currency_code") or "").strip().upper()
        if not resolved or (code and code != resolved):
            return None
        code = resolved
    return code or None


def extract_concept(text: str) -> str:
    cleaned = MONEY_RE.sub(" ", text or "")
    cleaned = ACCOUNT_CLAUSE_RE.sub(" ", cleaned)
    cleaned = LEADING_EXPENSE_RE.sub("", cleaned)
    cleaned = re.sub(r"\b(?:cop|pesos?|hoy|ayer)\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"^[\s,;:.-]*(?:en|por|para|de)\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"[^\wáéíóúüñÁÉÍÓÚÜÑ -]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -.,")
    return cleaned[:160]


def parse_transaction_date(text: str, created_at: str | None) -> tuple[str | None, str | None]:
    if EXPLICIT_DATE_RE.search(text or ""):
        return None, "explicit_date_requires_confirmation"
    try:
        base = datetime.fromisoformat(str(created_at or "").replace("Z", "+00:00")).astimezone(BOGOTA)
    except ValueError:
        base = datetime.now(BOGOTA)
    if re.search(r"\bayer\b", fold_text(text)):
        base -= timedelta(days=1)
    return base.date().isoformat(), None


def stem_token(token: str) -> str:
    if len(token) > 5 and token.endswith("es"):
        return token[:-2]
    if len(token) > 4 and token.endswith("s"):
        return token[:-1]
    return token


def semantic_tokens(value: Any) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", fold_text(value))
    return {stem_token(token) for token in tokens if token not in STOPWORDS and len(token) > 1}


def similarity_score(concept: str, row: dict[str, Any]) -> int:
    wanted = semantic_tokens(concept)
    if not wanted:
        return 0
    best = 0
    for candidate in (row.get("description"), row.get("destination_name")):
        actual = semantic_tokens(candidate)
        if not actual:
            continue
        if actual == wanted:
            best = max(best, 100)
            continue
        intersection = len(wanted & actual)
        union = len(wanted | actual)
        if intersection == len(wanted) or intersection == len(actual):
            best = max(best, 75 + int(20 * intersection / max(len(wanted), len(actual))))
        elif union:
            best = max(best, int(70 * intersection / union))
    return best


def _find_named(rows: list[dict[str, Any]], name: str, *, row_type: str | None = None) -> dict[str, Any] | None:
    target = fold_text(name)
    matches = [
        row
        for row in rows
        if fold_text(row.get("name")) == target
        and (row_type is None or fold_text(row.get("type")) == fold_text(row_type))
        and row.get("active") is not False
    ]
    return matches[0] if len(matches) == 1 else None


def _resolve_row_id(
    row: dict[str, Any],
    id_key: str,
    name_key: str,
    catalog_rows: list[dict[str, Any]],
    *,
    row_type: str | None = None,
) -> tuple[str, str] | None:
    name = str(row.get(name_key) or "")
    row_id = str(row.get(id_key) or "")
    if row_id:
        for candidate in catalog_rows:
            if str(candidate.get("id") or "") == row_id and candidate.get("active") is not False:
                if row_type is None or fold_text(candidate.get("type")) == fold_text(row_type):
                    return row_id, str(candidate.get("name") or name)
    candidate = _find_named(catalog_rows, name, row_type=row_type)
    return (str(candidate["id"]), str(candidate["name"])) if candidate else None


def infer_from_history(
    concept: str,
    history: list[dict[str, Any]],
    catalog: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, str | None]:
    candidates: list[tuple[int, dict[str, Any]]] = []
    for row in history:
        if fold_text(row.get("type")) != "withdrawal":
            continue
        score = similarity_score(concept, row)
        if score >= 80:
            candidates.append((score, row))
    candidates.sort(key=lambda item: (-item[0], str(item[1].get("transaction_journal_id") or item[1].get("journal_id") or "")))
    if not candidates:
        return None, None

    top_score = candidates[0][0]
    top_rows = [row for score, row in candidates if score == top_score]
    signatures = {
        (
            str(row.get("destination_id") or row.get("destination_name") or ""),
            str(row.get("category_id") or row.get("category_name") or ""),
            str(row.get("budget_id") or row.get("budget_name") or ""),
        )
        for row in top_rows
    }
    if len(signatures) > 1:
        return None, "ambiguous_history"
    row = top_rows[0]
    destination = _resolve_row_id(row, "destination_id", "destination_name", catalog["accounts"], row_type="expense")
    category = _resolve_row_id(row, "category_id", "category_name", catalog["categories"])
    budget = _resolve_row_id(row, "budget_id", "budget_name", catalog["budgets"])
    if not destination or not category or not budget:
        return None, None
    existing_tags = {fold_text(tag.get("name")): str(tag.get("name")) for tag in catalog["tags"] if tag.get("name")}
    tags = [existing_tags[fold_text(tag)] for tag in row.get("tags") or [] if fold_text(tag) in existing_tags]
    return {
        "description": str(row.get("description") or concept)[:160],
        "destination_id": destination[0],
        "destination_name": destination[1],
        "category_id": category[0],
        "category_name": category[1],
        "budget_id": budget[0],
        "budget_name": budget[1],
        "tags": tags,
        "evidence": "history",
        "similar_journal_id": str(row.get("transaction_journal_id") or row.get("journal_id") or "") or None,
    }, None


def infer_known_mapping(concept: str, catalog: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    tokens = semantic_tokens(concept)
    for mapping in KNOWN_MAPPINGS:
        if not (tokens & {stem_token(token) for token in mapping["keywords"]}):
            continue
        destination_name = mapping["destination"]
        normalized = fold_text(concept)
        if "d1" in tokens and _find_named(catalog["accounts"], "Mercado D1", row_type="expense"):
            destination_name = "Mercado D1"
        destination = _find_named(catalog["accounts"], destination_name, row_type="expense")
        category = _find_named(catalog["categories"], mapping["category"])
        budget = _find_named(catalog["budgets"], mapping["budget"])
        if not destination or not category or not budget:
            return None
        tags: list[str] = []
        tag_name = mapping.get("tag")
        if tag_name and _find_named(catalog["tags"], tag_name):
            tags.append(tag_name)
        if destination_name == "Mercado D1":
            description = "Mercado D1"
        elif "mercado" in mapping["keywords"] and "mercado" in normalized:
            description = concept[0].upper() + concept[1:] if concept else "Mercado"
        else:
            description = concept[0].upper() + concept[1:] if concept else mapping["category"]
        return {
            "description": description[:160],
            "destination_id": str(destination["id"]),
            "destination_name": str(destination["name"]),
            "category_id": str(category["id"]),
            "category_name": str(category["name"]),
            "budget_id": str(budget["id"]),
            "budget_name": str(budget["name"]),
            "tags": tags,
            "evidence": "reviewed_mapping",
            "similar_journal_id": None,
        }
    return None


def compute_plan_hash(plan: dict[str, Any]) -> str:
    fields = {
        key: plan.get(key)
        for key in (
            "adapter_version",
            "operation_id",
            "currency_code",
            "amount_cop",
            "date",
            "description",
            "source_id",
            "destination_id",
            "category_id",
            "budget_id",
            "tags",
            "external_id",
        )
    }
    encoded = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def needs_confirmation(
    question: str,
    *,
    error: str,
    transaction_kind: str = "cash_expense",
) -> dict[str, Any]:
    return {
        "ok": True,
        "intent": "expense",
        "decision": "needs_confirmation",
        "transaction_kind": transaction_kind,
        "write_committed": False,
        "question": question,
        "error": error,
    }


def build_plan(payload: dict[str, Any], gateway: Any) -> dict[str, Any]:
    request_id = str(payload.get("request_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", request_id):
        return needs_confirmation("No pude validar el identificador del gasto.", error="invalid_request_id")
    operation_id = str(payload.get("operation_id") or "").strip()
    if OPERATION_ID_RE.fullmatch(operation_id) is None:
        return needs_confirmation(
            "No pude validar la identidad estable del gasto.",
            error="invalid_operation_id",
        )
    capture = payload.get("capture") or {}
    text = str(capture.get("text") or "").strip()
    if not text or len(text.encode("utf-8")) > 4096:
        return needs_confirmation("¿Qué gasto quieres registrar?", error="invalid_text")

    blocked = unsupported_reason(text)
    if blocked:
        return needs_confirmation(
            "Este botón solo registra gastos simples. Usa Megan para tarjetas, FP o movimientos múltiples.",
            error=blocked,
            transaction_kind="unsupported",
        )

    amount = parse_amount_cop(text)
    if amount is None:
        return needs_confirmation("¿Cuál es el único monto exacto del gasto?", error="missing_or_ambiguous_amount")

    transaction_date, date_error = parse_transaction_date(text, payload.get("created_at"))
    if date_error or not transaction_date:
        return needs_confirmation("Indica hoy o ayer; otras fechas requieren confirmación con Megan.", error=date_error or "invalid_date")

    source_name = extract_account_name(text)
    if not source_name:
        return needs_confirmation("¿Fue por Nequi, Banco Bogotá o efectivo?", error="ambiguous_account")
    concept = extract_concept(text)
    if len(semantic_tokens(concept)) == 0:
        return needs_confirmation("¿En qué fue el gasto?", error="missing_concept")

    try:
        catalog = gateway.load_catalog()
    except FireflyError:
        return needs_confirmation("No pude verificar Firefly. Intenta nuevamente.", error="firefly_unavailable")

    source = _find_named(catalog["accounts"], source_name, row_type="asset")
    if not source:
        return needs_confirmation("No encontré esa cuenta activa en Firefly.", error="unknown_source_account")
    if account_currency_code(source, catalog) != "COP":
        return needs_confirmation("Solo puedo registrar gastos desde una cuenta verificada en COP.", error="source_currency_not_verified_cop")

    try:
        history = gateway.search_transactions(concept)
    except FireflyError:
        return needs_confirmation("No pude consultar el historial de Firefly.", error="firefly_history_unavailable")

    metadata, history_error = infer_from_history(concept, history, catalog)
    if metadata is None:
        reviewed_mapping = infer_known_mapping(concept, catalog)
        if reviewed_mapping is not None:
            metadata = reviewed_mapping
        elif history_error:
            return needs_confirmation("Encontré patrones distintos. ¿Qué categoría y presupuesto debo usar?", error=history_error)
    if metadata is None:
        return needs_confirmation("¿Qué categoría y presupuesto existentes debo usar?", error="unknown_category_or_budget")
    destination = next((row for row in catalog["accounts"] if str(row.get("id")) == metadata["destination_id"]), {})
    if (destination.get("currency_code") or destination.get("currency_id")) and account_currency_code(destination, catalog) != "COP":
        return needs_confirmation("La moneda de la cuenta destino necesita revisión con Megan.", error="destination_currency_conflict")

    plan: dict[str, Any] = {
        "adapter_version": ADAPTER_VERSION,
        "operation_id": operation_id,
        "transaction_kind": "cash_expense",
        "currency_code": "COP",
        "amount_cop": amount,
        "date": transaction_date,
        "description": metadata["description"],
        "source_id": str(source["id"]),
        "source_name": str(source["name"]),
        "destination_id": metadata["destination_id"],
        "destination_name": metadata["destination_name"],
        "category_id": metadata["category_id"],
        "category_name": metadata["category_name"],
        "budget_id": metadata["budget_id"],
        "budget_name": metadata["budget_name"],
        "tags": metadata["tags"],
        "external_id": f"hermes-watch:{operation_id}",
        "evidence": metadata["evidence"],
        "similar_journal_id": metadata["similar_journal_id"],
    }
    return {
        "ok": True,
        "intent": "expense",
        "decision": "auto_write",
        "transaction_kind": "cash_expense",
        "write_committed": False,
        "question": None,
        "operation_id": operation_id,
        "external_id": plan["external_id"],
        "plan": plan,
        "plan_hash": compute_plan_hash(plan),
    }


def _row_journal_id(row: dict[str, Any]) -> str:
    return str(row.get("transaction_journal_id") or row.get("journal_id") or "")


def _amount_int(value: Any) -> int | None:
    try:
        amount = Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount != amount.to_integral_value():
        return None
    return int(amount)


def _normalized_tags(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return ()
    if not isinstance(value, list):
        return None
    return tuple(sorted(fold_text(item) for item in value if fold_text(item)))


def existing_matches_plan(row: dict[str, Any], plan: dict[str, Any]) -> bool:
    return (
        fold_text(row.get("type")) == "withdrawal"
        and row.get("currency_code") == "COP"
        and plan.get("currency_code") == "COP"
        and str(row.get("external_id") or "") == plan["external_id"]
        and _amount_int(row.get("amount")) == plan["amount_cop"]
        and str(row.get("date") or "")[:10] == plan["date"]
        and str(row.get("source_id") or "") == plan["source_id"]
        and str(row.get("destination_id") or "") == plan["destination_id"]
        and str(row.get("category_id") or "") == plan["category_id"]
        and str(row.get("budget_id") or "") == plan["budget_id"]
        and fold_text(row.get("description")) == fold_text(plan["description"])
        and _normalized_tags(row.get("tags")) == _normalized_tags(plan["tags"])
    )


def committed_result(
    plan_result: dict[str, Any],
    row: dict[str, Any],
    *,
    duplicate: bool,
    recovered_after_error: bool = False,
) -> dict[str, Any]:
    plan = plan_result["plan"]
    return {
        "ok": True,
        "intent": "expense",
        "decision": "duplicate" if duplicate else "committed",
        "transaction_kind": "cash_expense",
        "write_committed": True,
        "duplicate": duplicate,
        "recovered_after_error": recovered_after_error,
        "post_attempted": not duplicate,
        "operation_id": plan["operation_id"],
        "external_id": plan["external_id"],
        "plan": plan,
        "plan_hash": plan_result["plan_hash"],
        "journal_ids": [_row_journal_id(row)],
        "group_ids": [str(row.get("group_id") or "")],
    }


def confirmed_plan_result(payload: dict[str, Any]) -> dict[str, Any]:
    plan = payload.get("confirmed_plan")
    operation_id = str(payload.get("operation_id") or "")
    expected_hash = str(payload.get("expected_plan_hash") or "")
    expected_external_id = f"hermes-watch:{operation_id}"
    if not isinstance(plan, dict):
        return needs_confirmation("No encontré el plan firmado; revisa el gasto.", error="missing_confirmed_plan")
    if (
        OPERATION_ID_RE.fullmatch(operation_id) is None
        or plan.get("operation_id") != operation_id
        or plan.get("external_id") != expected_external_id
        or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
    ):
        return needs_confirmation("El plan firmado no coincide; Megan debe revisarlo.", error="invalid_confirmed_plan")
    if compute_plan_hash(plan) != expected_hash:
        return needs_confirmation("El plan cambió; revisa el gasto antes de registrarlo.", error="plan_changed")
    required_strings = (
        "date",
        "description",
        "source_id",
        "destination_id",
        "category_id",
        "budget_id",
    )
    if not (
        plan.get("transaction_kind") == "cash_expense"
        and plan.get("currency_code") == "COP"
        and type(plan.get("amount_cop")) is int
        and 1000 <= plan["amount_cop"] <= MAX_AMOUNT_COP
        and all(isinstance(plan.get(key), str) and plan[key].strip() for key in required_strings)
        and isinstance(plan.get("tags"), list)
    ):
        return needs_confirmation("El plan firmado está incompleto; Megan debe revisarlo.", error="invalid_confirmed_plan")
    return {
        "ok": True,
        "intent": "expense",
        "decision": "auto_write",
        "transaction_kind": "cash_expense",
        "write_committed": False,
        "operation_id": operation_id,
        "external_id": expected_external_id,
        "plan": plan,
        "plan_hash": expected_hash,
    }


def reconciliation_result(planned: dict[str, Any], gateway: Any) -> dict[str, Any]:
    plan = planned["plan"]
    try:
        existing = gateway.find_by_external_id(plan["external_id"], plan["date"])
    except FireflyError:
        return {
            "ok": True,
            "intent": "expense",
            "decision": "reconciliation_error",
            "transaction_kind": "cash_expense",
            "write_committed": False,
            "post_attempted": False,
            "operation_id": plan["operation_id"],
            "external_id": plan["external_id"],
            "plan": plan,
            "plan_hash": planned["plan_hash"],
            "error": "idempotency_check_failed",
        }
    if len(existing) > 1:
        result = needs_confirmation("Encontré más de un movimiento con este ID; Megan debe revisarlo.", error="idempotency_collision")
    elif len(existing) == 1 and not existing_matches_plan(existing[0], plan):
        result = needs_confirmation("El ID ya existe con otros datos; Megan debe revisarlo.", error="idempotency_conflict")
    elif len(existing) == 1:
        return committed_result(planned, existing[0], duplicate=True)
    else:
        return {
            "ok": True,
            "intent": "expense",
            "decision": "absent",
            "transaction_kind": "cash_expense",
            "write_committed": False,
            "post_attempted": False,
            "operation_id": plan["operation_id"],
            "external_id": plan["external_id"],
            "plan": plan,
            "plan_hash": planned["plan_hash"],
            "error": None,
        }
    result.update(
        operation_id=plan["operation_id"],
        external_id=plan["external_id"],
        plan=plan,
        plan_hash=planned["plan_hash"],
        post_attempted=False,
    )
    return result


def uncertain_result(planned: dict[str, Any], error: str) -> dict[str, Any]:
    plan = planned["plan"]
    return {
        "ok": True,
        "intent": "expense",
        "decision": "uncertain",
        "transaction_kind": "cash_expense",
        "write_committed": False,
        "post_attempted": True,
        "operation_id": plan["operation_id"],
        "external_id": plan["external_id"],
        "plan": plan,
        "plan_hash": planned["plan_hash"],
        "error": error,
    }


def process_payload(
    payload: dict[str, Any],
    gateway: Any,
    *,
    dry_run: bool,
    mode: str | None = None,
) -> dict[str, Any]:
    selected_mode = mode or ("plan" if dry_run else "commit")
    if selected_mode not in {"plan", "reconcile", "commit"}:
        return needs_confirmation("Modo de operación inválido.", error="invalid_mode")
    if selected_mode != "plan" and payload.get("confirmed_plan") is None:
        operation_id = str(payload.get("operation_id") or "").strip()
        if not OPERATION_ID_RE.fullmatch(operation_id):
            return needs_confirmation("Falta una identidad de operación válida.", error="invalid_operation_id")
        capture_value = payload.get("capture")
        capture: dict[str, Any] = capture_value if isinstance(capture_value, dict) else {}
        blocked_error = unsupported_reason(str(capture.get("text") or capture.get("raw_text") or ""))
        if blocked_error:
            return needs_confirmation(
                "Este botón solo registra gastos simples. Usa Megan para tarjetas, FP o movimientos múltiples.",
                transaction_kind="unsupported",
                error=blocked_error,
            )
    if selected_mode == "plan":
        planned = build_plan(payload, gateway)
    else:
        # Side-effecting and reconciliation calls must use the exact plan that
        # the BFF durably stored and signed. Never reinterpret text here.
        planned = confirmed_plan_result(payload)
    if planned.get("decision") != "auto_write" or selected_mode == "plan":
        return planned

    expected_hash = str(payload.get("expected_plan_hash") or "")
    if not expected_hash or expected_hash != planned.get("plan_hash"):
        return needs_confirmation("El plan cambió; revisa el gasto antes de registrarlo.", error="plan_changed")

    plan = planned["plan"]
    reconciled = reconciliation_result(planned, gateway)
    if reconciled.get("decision") != "absent" or selected_mode == "reconcile":
        return reconciled

    transaction = {
        "type": "withdrawal",
        "currency_code": "COP",
        "amount": str(plan["amount_cop"]),
        "date": plan["date"],
        "description": plan["description"],
        "source_id": plan["source_id"],
        "destination_id": plan["destination_id"],
        "category_id": plan["category_id"],
        "budget_id": plan["budget_id"],
        "external_id": plan["external_id"],
        "internal_reference": f"hermes-watch-v{ADAPTER_VERSION}",
    }
    if plan["tags"]:
        transaction["tags"] = list(plan["tags"])

    try:
        gateway.create_withdrawal(transaction)
    except FireflyError:
        recovered = reconciliation_result(planned, gateway)
        if recovered.get("decision") == "duplicate":
            recovered["decision"] = "committed"
            recovered["duplicate"] = False
            recovered["recovered_after_error"] = True
            recovered["post_attempted"] = True
            return recovered
        return uncertain_result(planned, "firefly_write_failed")

    # Firefly create responses can omit some echoed IDs depending on version;
    # verify through the canonical external-id readback before acknowledging.
    verified = reconciliation_result(planned, gateway)
    if verified.get("decision") == "duplicate":
        verified["decision"] = "committed"
        verified["duplicate"] = False
        verified["post_attempted"] = True
        return verified
    return uncertain_result(planned, "firefly_verification_failed")


def read_payload(path: str) -> dict[str, Any]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Idempotent simple Firefly expense adapter")
    parser.add_argument("--payload-file", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mode", choices=("plan", "reconcile", "commit"))
    args = parser.parse_args()
    try:
        payload = read_payload(args.payload_file)
        gateway = FireflyGateway()
        result = process_payload(payload, gateway, dry_run=args.dry_run, mode=args.mode)
    except (FireflyError, OSError, ValueError, json.JSONDecodeError):
        result = {"ok": False, "intent": "expense", "decision": "error", "write_committed": False, "error": "adapter_failed"}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
