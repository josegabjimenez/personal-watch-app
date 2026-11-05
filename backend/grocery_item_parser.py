"""Deterministic, additions-only parsing for the dedicated Mercado surface.

No helper or external service runs here. Ambiguity rejects the entire utterance;
callers must finish parsing before attempting any item writes.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
import unicodedata

MAX_TEXT_LENGTH = 1000
MAX_ITEMS = 10
MAX_ITEM_LENGTH = 120
MAX_QUANTITY = Decimal("10000")


class GroceryParseError(ValueError):
    def __init__(self, code: str, message: str, question: str | None = None):
        self.code = code
        self.message = message
        self.question = question or message
        super().__init__(message)


def _fold(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(char))


# These are route wrappers, not arbitrary words to remove from product names.
_PREFIX = re.compile(
    r"^(?:por\s+favor\s+)?(?:(?:agrega|agregar|agregame|agrégame|anade|añade|anadir|añadir|"
    r"incluye|incluir|necesito)\s+|(?:(?:me\s+)?(?:falta|faltan))\s+)", re.I)
_SUFFIX = re.compile(
    r"(?:^|\s+)(?:al\s+mercado|a\s+(?:la|mi)\s+lista\s+de(?:l)?\s+(?:mercado|compras))$", re.I)
_INJECTION = re.compile(
    r"\b(?:ignora|ignorar|ignore|disregard|instrucciones|instructions|reglas|rules|"
    r"system|developer|assistant|asistente|override|bypass|omite|omitir|salta|saltate|"
    r"revela|reveal|secretos?|secrets?|tokens?|password|contrasena|ejecuta|ejecutar|execute|"
    r"eval|curl|wget|sudo|chmod|scripts?|comandos?|commands?|python|import)\b", re.I)
_UNSUPPORTED = re.compile(
    r"\b(?:quita|quitar|elimina|eliminar|borra|borrar|retira|retirar|saca|sacar|"
    r"marca|marcar|desmarca|completa|completar|termina|terminar|cancela|cancelar|"
    r"comprad[oa]s?|disponibles?|tengo|tenemos|compre|compramos|no|nunca|ningun[oa]?|"
    r"excepto|pero|en\s+vez|reemplaza|sustituye|cambia|cambiar|"
    r"anota|anotar|notas?|guarda|guardar|ideas?|escribe|escribir|redacta|"
    r"recuerdame|recuerda|recordar|recordatorios?|manana|hoy|lunes|martes|miercoles|jueves|"
    r"viernes|sabado|domingo|llama|llamar|envia|enviar|agenda|agendar|"
    r"enciende|encender|prende|apaga|apagar|abre|abrir|cierra|cerrar|sube|baja|ajusta|"
    r"termostato|cortinas?|luces?|puertas?|alarmas?|"
    r"gastos?|pague|pagar|pagos?|cobrar|transferencias?|transferir|depositos?|salario|"
    r"ingresos?|categorias?|presupuestos?|cuentas?|tarjetas?|tc[123]|fp|nequi|daviplata|"
    r"bancolombia|firefly|usd|cop|pesos|dolares|euros|"
    r"compra|comprar|registra(?:r)?|transfiere|deposita(?:r)?|pon|poner|coloca(?:r)?|cerrar|"
    r"ajustar|encender|prender|subir|bajar|ventilador|calefaccion|aire\s+acondicionado|"
    r"investiga(?:r)?|busca(?:r)?|consulta(?:r)?|muestra(?:me)?|dime|programa(?:r)?|"
    r"disena(?:r)?|crea(?:r)?|construye|implementa|revisa(?:r)?|prepara(?:r)?|haz|hacer|lee|leer|"
    r"delete|remove|complete|remind|turn\s+(?:on|off)|pay|transfer)\b", re.I)

# A finite vocabulary is evidence for conjunction-separated lists, not a limit
# on single products or explicitly comma-separated products.
_ATOMIC_PRODUCT = re.compile(r"^aceite de oliva(?: extra virgen| virgen)?$")
_PRODUCT_HEADS = frozenset("""
leche leches huevo huevos arroz pan panes queso quesos cafe azucar sal pimienta
aceite aceites harina pasta pollo carne pescado atun lenteja lentejas frijol
frijoles garbanzos cebolla cebollas tomate tomates papa papas patata
patatas ajo zanahoria zanahorias lechuga pepino brocoli limon limones naranja
naranjas manzana manzanas banano bananos banana bananas platano platanos fresa
fresas aguacate aguacates uva uvas higo higos agua jugo yogur yogurt avena
cereal cereales galleta galletas mantequilla crema papel jabon detergente champu
shampoo servilleta servilletas bolsa bolsas panales suavizante chocolate miel
jamon pescado pavo cerdo atun salsa mostaza vinagre mayonesa nuez nueces mani
tofu miso tempeh vitamina vitaminas
""".split())

_NUMBER = r"(?:[0-9]+(?:[.,\ue002][0-9]+)?(?:/[0-9]+)?|[¼½¾]|un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|cero|medio|media)"
_UNIT = r"(?:kilogramos?|kilos?|kg|gramos?|gr|g|litros?|l|mililitros?|ml|libras?|lb|onzas?|paquetes?|bolsas?|botellas?|latas?|cajas?|docenas?|unidades?|barras?|tarros?|vasos?|tazas?|bandejas?|rollos?)"
_AMOUNT = re.compile(r"^(?P<amount>" + _NUMBER + r")(?![\w])", re.I)
_ATTACHED_AMOUNT = re.compile(r"^(?P<amount>[0-9]+(?:[.,][0-9]+)?)(?=" + _UNIT + r"\b)", re.I)
_UNIT_START = re.compile(r"^" + _UNIT + r"\b", re.I)
_HALF = re.compile(r"^\s*,?\s*y\s+(?:medio|media)\b", re.I)
_MORE_MEASURE = re.compile(r"^\s*,\s*(?P<amount>" + _NUMBER + r")\s*(?:" + _UNIT + r")\b", re.I)
_HALF_SPAN = re.compile(r"\b(?:" + _NUMBER + r")\s*(?:" + _UNIT + r")\s*,?\s*(?P<join>y)\s+(?:medio|media)\b", re.I)
_MEASURE_COMMA = re.compile(r"\b(?:" + _NUMBER + r")\s*(?:" + _UNIT + r")\s*(?P<comma>,)\s*(?=(?:" + _NUMBER + r")\s*(?:" + _UNIT + r")\b)", re.I)
_POSTFIX_MEASURE = re.compile(r",\s*(?:" + _NUMBER + r")\s*(?:" + _UNIT + r")(?:\s+y\s+(?:medio|media))?\s*(?=$|,|\s+[ye]\s+(?!(?:medio|media)\b))", re.I)
_CONJUNCTION = re.compile(r"\s+([ye])\s+", re.I)
_COMPOUNDS = (
    re.compile(r"\bsal\s+(?P<join>y)\s+pimienta\b", re.I),
    re.compile(r"\bvitamina\s+(?P<join>e)\b", re.I),
)
_PROTECTED = {"\ue000": "y", "\ue001": "e", "\ue002": ",", "\ue003": "Y", "\ue004": "E"}


def _restore(value: str) -> str:
    for marker, original in _PROTECTED.items():
        value = value.replace(marker, original)
    return value


def _protect_group(text: str, expression: re.Pattern[str], group: str) -> str:
    # Replace separators only, retaining every product and quantity character.
    spans = [(match.start(group), match.end(group)) for match in expression.finditer(text)]
    for start, end in reversed(spans):
        original = text[start:end]
        marker = {",": "\ue002", "y": "\ue000", "e": "\ue001", "Y": "\ue003", "E": "\ue004"}[original]
        text = text[:start] + marker + text[end:]
    return text


def _check_quantity(amount: str) -> None:
    if _fold(amount) == "cero":
        raise GroceryParseError("invalid_quantity", "Indica una cantidad positiva para cada artículo.")
    if amount[0].isdigit():
        try:
            numerator, *denominator = amount.replace(",", ".").split("/")
            numeric = Decimal(numerator)
            if denominator:
                numeric /= Decimal(denominator[0])
            if not (0 < numeric <= MAX_QUANTITY):
                raise GroceryParseError("invalid_quantity", "Indica una cantidad positiva y razonable para cada artículo.")
        except (InvalidOperation, ZeroDivisionError):
            raise GroceryParseError("invalid_quantity", "Indica una cantidad válida para cada artículo.") from None


def _quantity_product(value: str) -> tuple[str, bool]:
    """Remove a leading supported quantity for validation, never for output."""
    match = _AMOUNT.match(value) or _ATTACHED_AMOUNT.match(value)
    if match is None:
        return value, False
    _check_quantity(match.group("amount"))
    remaining = value[match.end():].lstrip()
    unit = _UNIT_START.match(remaining)
    if unit:
        remaining = remaining[unit.end():]
        half = _HALF.match(remaining)
        if half:
            remaining = remaining[half.end():]
        while more := _MORE_MEASURE.match(remaining):
            _check_quantity(more.group("amount"))
            remaining = remaining[more.end():]
        remaining = remaining.lstrip()
    remaining = re.sub(r"^de\s+", "", remaining, flags=re.I)
    remaining = re.sub(r"^(?:el|la|los|las)\s+", "", remaining, flags=re.I)
    return remaining.strip(), True


def _list_evidence(value: str) -> tuple[bool, bool, bool]:
    product, quantified = _quantity_product(value)
    product = _fold(product)
    words = product.split()
    known = bool(words) and words[0] in _PRODUCT_HEADS
    modifier = re.search(r"\b(?:de|con)\b", product) is not None and _ATOMIC_PRODUCT.fullmatch(product) is None
    return known, quantified, modifier


def _validate_item(value: str) -> str:
    if len(value) > MAX_ITEM_LENGTH:
        raise GroceryParseError("item_too_long", "Usa un nombre más corto para cada artículo.")
    if re.search(r"(?:^|\s)[ye](?:$|\s)", value, flags=re.I):
        # Known quantity/compound conjunctions are the only permitted survivors.
        masked = value
        for expression in (*_COMPOUNDS, _HALF_SPAN):
            masked = _protect_group(masked, expression, "join")
        if re.search(r"(?:^|\s)[ye](?:$|\s)", masked, flags=re.I):
            raise GroceryParseError("ambiguous_items", "Separa los artículos con comas para confirmar cuáles son distintos.")
    product, _ = _quantity_product(value)
    for measurement in _POSTFIX_MEASURE.finditer(value):
        _quantity_product(measurement.group().lstrip(", "))
    if (not product or not any(char.isalpha() for char in product) or _UNIT_START.fullmatch(product)
            or _fold(product) in {"de", "el", "la", "los", "las", "al", "mercado", "lista"}):
        raise GroceryParseError("invalid_quantity", "Indica el artículo además de su cantidad.")
    if re.match(r"^[+\-−]\s*\d|^[0-9]+(?:[.,/][0-9]*){2,}", value):
        raise GroceryParseError("invalid_quantity", "Indica una cantidad positiva para cada artículo.")
    if re.match(r"^(?:agrega(?:r|me)?|anade|anadir|incluye|incluir|necesito|falta|faltan)\b", _fold(value)):
        raise GroceryParseError("unsupported_action", "Indica solo nombres y cantidades después de agregar.")
    if re.search(r"[.!?]\s+[^\W\d_]", value) or any(char in value for char in "!?;:&"):
        raise GroceryParseError("ambiguous_items", "Separa los artículos con comas y evita instrucciones adicionales.")
    return value


def parse_grocery_items(text: str) -> list[str]:
    """Return all intended additions, or reject the entire ambiguous request.

    Commas explicitly separate items, except protected decimal/measurement
    commas. Conjunctions require familiar product heads; ingredient modifiers
    before a conjunction require an explicit quantity on the following item.
    Unknown single/comma-separated product names remain valid. Duplicate product
    descriptions prompt for one clear quantity instead of being merged/dropped.
    """
    if not isinstance(text, str):
        raise GroceryParseError("invalid_type", "Dicta los artículos de mercado como texto.")
    if len(text) > MAX_TEXT_LENGTH:
        raise GroceryParseError("text_too_long", "Divide la lista en capturas más cortas.")
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Co", "Cn"} for char in text):
        raise GroceryParseError("invalid_characters", "Dicta una lista sencilla, sin caracteres de control.")
    value = " ".join(text.split())
    if not value:
        raise GroceryParseError("empty_input", "¿Qué artículos quieres agregar al mercado?")
    folded = _fold(value)
    if _INJECTION.search(folded) or re.search(r"https?://|www\.|[{}\[\]<>`\\]|[$€£]", folded):
        raise GroceryParseError("unsafe_instruction", "Solo puedo agregar artículos de mercado, sin instrucciones adicionales.")
    if _UNSUPPORTED.search(folded):
        raise GroceryParseError("unsupported_action", "Solo agrega faltantes de mercado; separa cualquier otra acción.")
    if re.search(r"\b[ou]\b|\s[&+]\s", folded):
        raise GroceryParseError("ambiguous_items", "Indica los artículos exactos; usa comas para separarlos.")
    if any(not (char.isalnum() or char.isspace() or unicodedata.category(char).startswith("M")
                or char in ",.-'’()/%") for char in value):
        raise GroceryParseError("invalid_characters", "Usa nombres y cantidades sencillos para los artículos.")
    value = _PREFIX.sub("", value, count=1).removesuffix(".").rstrip()
    value = re.sub(r"\s+por\s+favor$", "", value, flags=re.I)
    value = _SUFFIX.sub("", value, count=1).strip().removesuffix(".").rstrip()
    if not value:
        raise GroceryParseError("empty_input", "¿Qué artículos quieres agregar al mercado?")

    masked = re.sub(r"(?<=[0-9]),(?=[0-9])", "\ue002", value)
    masked = _protect_group(masked, _MEASURE_COMMA, "comma")
    masked = _POSTFIX_MEASURE.sub(lambda match: match.group().replace(",", "\ue002"), masked)
    masked = _HALF_SPAN.sub(lambda match: _protect_group(match.group(), _HALF_SPAN, "join").replace(",", "\ue002"), masked)
    for expression in _COMPOUNDS:
        masked = _protect_group(masked, expression, "join")
    comma_parts = masked.split(",")
    items: list[str] = []
    keys: set[str] = set()
    for index, part in enumerate(comma_parts):
        part = part.strip()
        if index > 0 and index == len(comma_parts) - 1:
            part = re.sub(r"^[ye]\s+", "", part, count=1, flags=re.I)
        pieces = _CONJUNCTION.split(part)
        candidates = [piece.strip() for piece in pieces[::2]]
        if any(not candidate for candidate in candidates):
            raise GroceryParseError("ambiguous_items", "Indica un artículo entre cada separador de la lista.")
        restored = [_restore(candidate) for candidate in candidates]
        if len(restored) > 1:
            evidence = [_list_evidence(candidate) for candidate in restored]
            if not all(known for known, _, _ in evidence) or any(
                evidence[position][2] and not evidence[position + 1][1]
                for position in range(len(evidence) - 1)
            ):
                raise GroceryParseError("ambiguous_items", "Separa los artículos con comas para confirmar cuáles son distintos.")
        for candidate in restored:
            item = _validate_item(candidate)
            base, _ = _quantity_product(_POSTFIX_MEASURE.sub("", item).strip())
            key = _fold(base)
            if key in keys:
                raise GroceryParseError("duplicate_item", "El artículo aparece repetido; indica una sola cantidad para él.")
            keys.add(key)
            items.append(item)
            if len(items) > MAX_ITEMS:
                raise GroceryParseError("too_many_items", f"Agrega como máximo {MAX_ITEMS} artículos por captura.")
    return items
