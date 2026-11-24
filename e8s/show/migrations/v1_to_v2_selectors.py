"""
v1_to_v2_selectors.py --- the selectors sub-step of the v1 to v2 format migration

Stored selector text is a zone's `applies_to` (sheet and show zones) or a facing override's
`entities`. Version 1 text could glue `+`, `&` and a leading `-` to words, and combined `&` with
`+` and `-` left to right at one precedence; version 2 needs spaced operators and parentheses
wherever `&` mixes with `+` or `-`. Each such text is rewritten to the v2 text that selects the
same spots, and each rewrite is recorded with `report.repair()`. Text v2 already reads the same
way, and selectors stored as ID lists or selections, stay as they are. Text that never parsed is
refused with SchemaError naming the document, the field and the value.

Contains:
    FIELDS_BY_DOC: maps each document to its stored selector fields as (list field, item key)
    migrate_selectors(): rewrites stored selector text from the v1 grammar to the v2 grammar
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

from e8s.cast.select import normalize_v1_selector
from e8s.core.errors import SchemaError
from e8s.core.json_types import JsonValue
from e8s.core.readonly import ReadOnlyMap

if TYPE_CHECKING:
    from e8s.show.migrate import ReportBuilder

SHOW_DOC: Final = "/show/show.json"
SHEET_DOC: Final = "/show/sheet.json"
FIELDS_BY_DOC: Final[Mapping[str, tuple[tuple[str, str], ...]]] = ReadOnlyMap(
    {
        SHEET_DOC: (("zones", "applies_to"),),
        SHOW_DOC: (("zones", "applies_to"), ("facing_overrides", "entities")),
    }
)


def _normalized(document: str, field: str, text: str) -> str:
    try:
        return normalize_v1_selector(text)
    except SchemaError as error:
        raise SchemaError(
            f"{document} field {field} holds selector {text!r}, which does not parse: "
            f"{error.message}",
            hint=f"edit {field} in {document} into a selector such as T1 + T2, or remove it",
            context={"path": document, "field": field, "value": text},
        ) from error


def _migrated_item(
    item: JsonValue, key: str, label: str, document: str, report: "ReportBuilder"
) -> JsonValue:
    if not isinstance(item, dict) or not isinstance(text := item.get(key), str):
        return item
    rewritten = _normalized(document, label, text)
    if rewritten == text:
        return item
    report.repair(f"{document} {label}: rewrote {text!r} as {rewritten!r}")
    return item | {key: rewritten}


def _migrated_document(path: str, document: JsonValue, report: "ReportBuilder") -> JsonValue:
    if not isinstance(document, dict):
        return document
    migrated = document
    for list_field, key in FIELDS_BY_DOC.get(path, ()):
        items = document.get(list_field)
        if not isinstance(items, list):
            continue
        rewritten: JsonValue = [
            _migrated_item(item, key, f"{list_field}[{index}].{key}", path, report)
            for index, item in enumerate(items)
        ]
        if rewritten != items:
            migrated = migrated | {list_field: rewritten}
    return migrated


def migrate_selectors(docs: dict[str, JsonValue], report: "ReportBuilder") -> dict[str, JsonValue]:
    """Rewrites stored v1 selector text into v2 grammar with the same meaning, reporting each.

    Args:
        docs: Stored path to parsed document; never mutated.
        report: Records each rewritten selector.

    Returns:
        migrated: The documents with every stored selector text in v2 grammar.

    >>> from e8s.show.migrate import ReportBuilder
    >>> report = ReportBuilder(1)
    >>> sheet = {"zones": [{"id": "z", "applies_to": "T1+T2&T2"}]}
    >>> migrate_selectors({"/show/sheet.json": sheet}, report)["/show/sheet.json"]
    {'zones': [{'id': 'z', 'applies_to': '(T1 + T2) & T2'}]}
    >>> report.build().repairs
    ("/show/sheet.json zones[0].applies_to: rewrote 'T1+T2&T2' as '(T1 + T2) & T2'",)
    """
    return {path: _migrated_document(path, document, report) for path, document in docs.items()}
