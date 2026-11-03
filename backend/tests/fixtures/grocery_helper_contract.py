"""Exact AST-extracted grocery contract with exclusively synthetic globals.

Provenance: deployed helper SHA256
24be164879b114487516b85b6811cce8239a4ad0744ec1e36bfc575a75a8e7e0.
Only named function/pattern nodes are copied; no destination IDs, credentials,
private requests or local paths are included. The AST digest prevents accidental
fixture edits; a local snapshot, when present, is checked against every node.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

ORIGINAL_SHA256 = "24be164879b114487516b85b6811cce8239a4ad0744ec1e36bfc575a75a8e7e0"
EXTRACTED_AST_SHA256 = '53a2240d9964f4b72ef087a355c822736461a5cdf2b15b0269cf6281e4335b42'

SNIPPETS = {
    'canonicalize_ampm': 'def canonicalize_ampm(s: str) -> str:\n    """Normalize Siri/Apple Watch variants like `P.M.`, `p m`, `PM` -> `pm`."""\n    return re.sub(r"\\b([ap])\\s*\\.?\\s*m\\.?\\b", lambda m: m.group(1).lower() + "m", s or "", flags=re.I)',
    'norm': 'def norm(s: str) -> str:\n    s = canonicalize_ampm(s).strip().lower()\n    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))\n    # Keep separators useful for date/time parsing and common dictation variants.\n    s = re.sub(r"[^a-z0-9ñ\\\\s:/-]", " ", s)\n    s = re.sub(r"\\\\s+", " ", s).strip()\n    return s',
    'notion_token': 'def notion_token() -> str:\n    token = os.environ.get("NOTION_API_TOKEN") or os.environ.get("NOTION_API_KEY")\n    if not token:\n        raise RuntimeError("NOTION_API_TOKEN/NOTION_API_KEY no está configurado")\n    return token',
    'notion_request': 'def notion_request(method: str, path: str, body: Optional[dict] = None) -> dict:\n    url = "https://api.notion.com/v1" + path\n    data = None if body is None else json.dumps(body).encode("utf-8")\n    req = urllib.request.Request(url, data=data, method=method)\n    req.add_header("Authorization", "Bearer " + notion_token())\n    req.add_header("Notion-Version", NOTION_VERSION)\n    req.add_header("Content-Type", "application/json")\n    try:\n        with urllib.request.urlopen(req, timeout=45) as r:\n            raw = r.read().decode("utf-8")\n            return json.loads(raw) if raw else {}\n    except urllib.error.HTTPError as e:\n        detail = e.read().decode("utf-8", "replace")\n        raise RuntimeError(f"Notion HTTP {e.code}: {detail}") from e',
    'capture_operation_lock': '@contextmanager\ndef capture_operation_lock():\n    """Serialize capture/dispatcher transactions that share Notion + state."""\n    CAPTURE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)\n    with CAPTURE_LOCK_PATH.open("a+") as lock_file:\n        CAPTURE_LOCK_PATH.chmod(0o600)\n        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)\n        try:\n            yield\n        finally:\n            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)',
    '_GROCERY_QUANTITY_PATTERN': '_GROCERY_QUANTITY_PATTERN = (\n    r"(?:\\d+|un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|"\n    r"trece|catorce|quince|dieciseis|diecisiete|dieciocho|diecinueve|veinte|media)"\n)',
    '_GROCERY_UNIT_PATTERN': '_GROCERY_UNIT_PATTERN = (\n    r"(?:packs?|paquetes?|unidades?|unds?|docenas?|kilos?|kg|gramos?|gr|libras?|lb|litros?|ml)"\n)',
    '_GROCERY_PRICE_SUFFIX_RE': '_GROCERY_PRICE_SUFFIX_RE = re.compile(\n    r"(?:"\n    r"\\s*=\\s*(?:(?:cop)\\s*)?\\$?\\s*\\d[\\d.,]*(?:\\s*(?:cop|pesos?))?"\n    r"|\\s+(?:(?:cop)\\s*)?\\$\\s*\\d[\\d.,]*(?:\\s*(?:cop|pesos?))?"\n    r"|\\s+\\d{1,3}(?:[.,]\\d{3})+(?:\\s*(?:cop|pesos?))?"\n    r"|\\s+(?:(?:cop)\\s+)?\\d[\\d.,]*\\s+(?:cop|pesos?)"\n    r")\\s*$",\n    re.I,\n)',
    '_GROCERY_DESTINATION_SUFFIX_RE': '_GROCERY_DESTINATION_SUFFIX_RE = re.compile(\n    r"(?:^|\\s+)(?:(?:al|a|en|para)\\s+)?(?:la\\s+|el\\s+)?(?:mi\\s+)?"\n    r"(?:lista(?:\\s+(?:de|del))?(?:\\s+(?:mercado|compras?))?|mercado)\\s*$",\n    re.I,\n)',
    '_grocery_children': 'def _grocery_children(parent_id: str) -> list[dict]:\n    """Return every direct child of *parent_id*, following Notion cursors."""\n    results: list[dict] = []\n    cursor: Optional[str] = None\n    seen_cursors: set[str] = set()\n    while True:\n        parent = urllib.parse.quote(str(parent_id), safe="")\n        path = f"/blocks/{parent}/children?page_size=100"\n        if cursor:\n            path += "&start_cursor=" + urllib.parse.quote(cursor, safe="")\n        data = notion_request("GET", path)\n        results.extend(data.get("results", []))\n        if not data.get("has_more"):\n            return results\n        next_cursor = data.get("next_cursor")\n        if not next_cursor or str(next_cursor) in seen_cursors:\n            # A partial walk is unsafe here: it could miss an existing item and\n            # create a duplicate. Fail closed instead of silently truncating.\n            raise RuntimeError(f"Paginación inválida en hijos de bloque {parent_id}")\n        cursor = str(next_cursor)\n        seen_cursors.add(cursor)',
    'get_all_grocery_blocks': 'def get_all_grocery_blocks() -> list[dict]:\n    """Depth-first walk of the complete grocery page block tree.\n\n    Private traversal metadata lets upsert preserve store grouping and choose a\n    deterministic canonical item without changing the Notion block payload.\n    """\n    results: list[dict] = []\n\n    def visit(parent_id: str, depth: int) -> None:\n        for raw_block in _grocery_children(parent_id):\n            block = dict(raw_block)\n            block["_grocery_parent_id"] = parent_id\n            block["_grocery_depth"] = depth\n            block["_grocery_order"] = len(results)\n            results.append(block)\n            block_id = block.get("id")\n            if block.get("has_children") and block_id:\n                visit(str(block_id), depth + 1)\n\n    visit(GROCERY_PAGE, 0)\n    return results',
    '_block_text': 'def _block_text(block: dict) -> str:\n    block_type = block.get("type")\n    value = block.get(block_type, {}) if block_type else {}\n    rich_text = value.get("rich_text", []) if isinstance(value, dict) else []\n    return "".join(x.get("plain_text", "") for x in rich_text if isinstance(x, dict))',
    'grocery_text': 'def grocery_text(block: dict) -> str:\n    return _block_text(block) if block.get("type") == "to_do" else ""',
    '_clean_grocery_item': 'def _clean_grocery_item(text: str) -> str:\n    """Remove capture-destination wording without changing product wording."""\n    value = _GROCERY_DESTINATION_SUFFIX_RE.sub("", (text or "").strip())\n    return value.strip(" .,:;")',
    'grocery_semantic_key': 'def grocery_semantic_key(text: str) -> str:\n    """Return a conservative product key, not a fuzzy/stemmed match.\n\n    Only known capture verbs/destinations and unambiguous structured list\n    metadata (quantity/unit and a trailing ``=price``) are removed. Product\n    tokens must otherwise remain an exact normalized match, so ``servilletas``\n    never matches ``servilletero``.\n    """\n    value = _clean_grocery_item(_GROCERY_PRICE_SUFFIX_RE.sub("", text or ""))\n    key = norm(value)\n    key = re.sub(\n        r"^(?:(?:hay\\s+que|necesito|necesitamos)\\s+)?"\n        r"(?:comprar|compra|compre|agregar|agrega|anadir|anade|falta|faltan)\\s+",\n        "",\n        key,\n    )\n    key = re.sub(\n        rf"^-?\\s*(?:{_GROCERY_QUANTITY_PATTERN}\\s*(?:x\\s*)?)?{_GROCERY_UNIT_PATTERN}"\n        rf"\\b(?:\\s+de)?\\s+",\n        "",\n        key,\n    )\n    # Canonical lists also use a bullet-like leading hyphen with a quantity but\n    # no explicit unit. Requiring the hyphen avoids stripping product numbers.\n    key = re.sub(rf"^-\\s*{_GROCERY_QUANTITY_PATTERN}\\s+", "", key)\n    key = re.sub(r"^(?:el|la|los|las|un|una|unos|unas)\\s+", "", key)\n    tokens = [\n        token[:-1] if len(token) > 4 and token.endswith(("as", "os")) else token\n        for token in key.split()\n    ]\n    return re.sub(r"\\s+", " ", " ".join(tokens)).strip(" -:/")',
    '_rich_grocery_item': 'def _rich_grocery_item(text: str) -> bool:\n    """Whether text carries canonical quantity/unit or price metadata."""\n    if _GROCERY_PRICE_SUFFIX_RE.search(text or ""):\n        return True\n    key = norm(text or "")\n    return bool(\n        re.match(\n            rf"^-?\\s*{_GROCERY_QUANTITY_PATTERN}\\s*(?:x\\s*)?{_GROCERY_UNIT_PATTERN}\\b",\n            key,\n        )\n        or re.match(rf"^-\\s*{_GROCERY_QUANTITY_PATTERN}\\s+", key)\n    )',
    '_grocery_order': 'def _grocery_order(block: dict, fallback: int) -> int:\n    try:\n        return int(block.get("_grocery_order", fallback))\n    except (TypeError, ValueError):\n        return fallback',
    '_grocery_depth': 'def _grocery_depth(block: dict) -> int:\n    try:\n        return max(0, int(block.get("_grocery_depth", 0)))\n    except (TypeError, ValueError):\n        return 0',
    '_grocery_match_sort_key': 'def _grocery_match_sort_key(block: dict, text: str, item: str, fallback_order: int) -> tuple:\n    """Prefer grouped canonical metadata, then exact text and tree order."""\n    nested = _grocery_depth(block) > 0\n    exact = norm(text) == norm(item)\n    rich = _rich_grocery_item(text)\n    normalized_text = norm(text)\n    return (\n        -int(nested),\n        -int(rich),\n        -int(exact),\n        -len(normalized_text.split()),\n        -len(normalized_text),\n        _grocery_order(block, fallback_order),\n        str(block.get("id") or ""),\n    )',
    '_other_grocery_toggle': 'def _other_grocery_toggle(blocks: list[dict]) -> Optional[dict]:\n    candidates = [\n        (index, block)\n        for index, block in enumerate(blocks)\n        if block.get("type") == "toggle" and norm(_block_text(block)) == "otros" and block.get("id")\n    ]\n    if not candidates:\n        return None\n    _, selected = min(\n        candidates,\n        key=lambda pair: (\n            _grocery_depth(pair[1]),\n            _grocery_order(pair[1], pair[0]),\n            str(pair[1].get("id") or ""),\n        ),\n    )\n    return selected',
    'upsert_grocery': 'def upsert_grocery(item: str, checked: bool = False) -> dict:\n    clean_item = _clean_grocery_item(item)\n    target_key = grocery_semantic_key(clean_item)\n    if not target_key:\n        raise RuntimeError("No identifiqué un artículo de mercado válido; no hice cambios")\n    blocks = get_all_grocery_blocks()\n    matches: list[tuple[int, dict, str]] = []\n    if target_key:\n        for index, block in enumerate(blocks):\n            text = grocery_text(block)\n            if text and grocery_semantic_key(text) == target_key:\n                matches.append((index, block, text))\n\n    match_count = len(matches)\n    duplicate_count = max(0, match_count - 1)\n    if matches:\n        _, block, text = min(\n            matches,\n            key=lambda match: _grocery_match_sort_key(match[1], match[2], clean_item, match[0]),\n        )\n        notion_request("PATCH", f"/blocks/{block[\'id\']}", {"to_do": {"checked": checked}})\n        return {\n            "action": "updated",\n            "block_id": block["id"],\n            "parent_block_id": block.get("_grocery_parent_id"),\n            "item": text,\n            "checked": checked,\n            "semantic_match_count": match_count,\n            "semantic_duplicate_count": duplicate_count,\n            "url": f"https://app.notion.com/{GROCERY_PAGE.replace(\'-\', \'\')}",\n        }\n\n    other_toggle = _other_grocery_toggle(blocks)\n    if other_toggle is None:\n        raise RuntimeError("No encontré el grupo \'Otros\' en Lista de Compras; no agregué el artículo")\n    parent_id = str(other_toggle["id"])\n    body = {\n        "children": [{\n            "object": "block",\n            "type": "to_do",\n            "to_do": {\n                "rich_text": [{"type": "text", "text": {"content": clean_item}}],\n                "checked": checked,\n                "color": "default",\n            },\n        }],\n    }\n    created = notion_request("PATCH", f"/blocks/{parent_id}/children", body)\n    bid = created.get("results", [{}])[0].get("id")\n    return {\n        "action": "created",\n        "block_id": bid,\n        "parent_block_id": parent_id,\n        "item": clean_item,\n        "checked": checked,\n        "semantic_match_count": 0,\n        "semantic_duplicate_count": 0,\n        "url": f"https://app.notion.com/{GROCERY_PAGE.replace(\'-\', \'\')}",\n    }',
}


def extracted_ast():
    tree = ast.parse("\n\n".join(SNIPPETS.values()))
    digest = hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()
    if digest != EXTRACTED_AST_SHA256:
        raise AssertionError("extracted deployed-helper AST changed")
    return tree


def assert_matches_deployed(path: Path):
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ORIGINAL_SHA256
    original = ast.parse(raw)
    selected = []
    for node in original.body:
        name = node.name if isinstance(node, ast.FunctionDef) else (
            node.targets[0].id if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) else None
        )
        if name in SNIPPETS:
            selected.append(node)
    expected = ast.Module(body=selected, type_ignores=[])
    assert ast.dump(expected, include_attributes=False) == ast.dump(extracted_ast(), include_attributes=False)


def source() -> str:
    extracted_ast()
    header = """from __future__ import annotations
import fcntl
import json
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
HERMES_HOME = Path(os.environ["AURA_CAPTURE_HOME"])
CAPTURE_LOCK_PATH = HERMES_HOME / "state" / "synthetic-capture.lock"
GROCERY_PAGE = "synthetic-official-list"
NOTION_VERSION = "2025-09-03"
"""
    return header + "\n\n".join(SNIPPETS.values()) + "\n"
