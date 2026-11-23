"""
v1_to_v2_forms.py --- the forms sub-step of the v1 to v2 format migration (lane G)

A v1 form stores its shape family in `kind` and an untyped JSON `geometry` beside it. v2 stores
one typed `recipe` that carries the kind and `recipe_version: 1`, so this step moves `kind` and
every geometry key into the recipe, checks the result against the v2 recipe of that kind and
stores it with every default written out, as a v2 save writes it. An svg outline's `curve`
becomes `curves`, one curve per subpath: a v1 composite that joined several subpaths across
their gaps is split into its parts.
A v1 form kept its members compact while its spots' placements recorded the slot each stood on,
so a form with open slots had gaps in those orders and a rebuild moved spots. v2 stores
`members` slot by slot, with None at each open slot: when every member of a form has a placement
on it, with distinct orders that are not 0, 1, 2 and so on, the members are laid out at those
orders and the repair is recorded.
A geometry that no v2 recipe can hold (an unknown kind, an unknown key, a value out of range,
a curve its own model refuses such as an arc of zero sweep, a negative extra gap in the
spacing) is refused with SchemaError naming
the document, the field and the value; nothing is guessed.

Contains:
    migrate_forms(): rewrites forms documents from the v1 shape to the v2 shape
"""

import re
from typing import TYPE_CHECKING, Final, get_args

from pydantic import TypeAdapter, ValidationError

from showpy.choreo.form_models import FormKind, Recipe
from showpy.choreo.recipe_models import RECIPE_VERSION
from showpy.core.errors import SchemaError, ShowPyError, bounded_repr
from showpy.core.json_types import JsonValue

if TYPE_CHECKING:
    from showpy.show.migrate import ReportBuilder

FORMS_DOC: Final = re.compile(r"^/sets/[^/]+/forms\.json$")
KINDS: Final = frozenset(get_args(FormKind.__value__))
RECIPE_ADAPTER: Final[TypeAdapter[Recipe]] = TypeAdapter(Recipe)
V2_ONLY_KEYS: Final = frozenset({"kind", "recipe_version"})
SVG_V1_CURVE: Final = "curve"
SVG_V2_CURVES: Final = "curves"
FORMS_NAME: Final = "forms.json"
SET_DOC_NAME: Final = "set.json"
GAP_PAIR_LENGTH: Final = 2


def _set_document(forms_path: str) -> str:
    return forms_path.removesuffix(FORMS_NAME) + SET_DOC_NAME


def _refusal(document: str, field: str, value: object, problem: str) -> SchemaError:
    return SchemaError(
        f"{document}: form field {field} {problem}",
        hint="fix the stored form, or rebuild it with its form constructor before migrating",
        context={"path": document, "field": field, "value": bounded_repr(value)},
    )


def _v1_parts(
    document: str, index: int, form: dict[str, JsonValue]
) -> tuple[str, dict[str, JsonValue]]:
    """Returns a v1 form's kind and geometry, refusing a form that has no valid pair."""
    where = f"[{index}]"
    kind, geometry = form.get("kind"), form.get("geometry")
    if not isinstance(kind, str) or kind not in KINDS:
        raise _refusal(document, f"{where}.kind", kind, f"is not one of {sorted(KINDS)}")
    if not isinstance(geometry, dict):
        raise _refusal(document, f"{where}.geometry", geometry, "is not an object")
    reserved = V2_ONLY_KEYS | ({SVG_V2_CURVES} if kind == "svg" else set())
    taken = sorted(reserved & geometry.keys())
    if taken:
        key = taken[0]
        raise _refusal(document, f"{where}.geometry.{key}", geometry[key], "is reserved in v2")
    return kind, geometry


def _svg_fields(geometry: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Moves a v1 svg outline into `curves`, one curve per subpath.

    v1 joined several subpaths into one composite curve that jumped the gaps between them; v2
    stores the composite's parts as separate curves, and a one-subpath outline as a list of one.
    """
    if SVG_V1_CURVE not in geometry:
        return geometry
    curve = geometry[SVG_V1_CURVE]
    rest = {key: value for key, value in geometry.items() if key != SVG_V1_CURVE}
    parts = (
        curve.get("parts") if isinstance(curve, dict) and curve.get("kind") == "composite" else None
    )
    return {**rest, SVG_V2_CURVES: parts if isinstance(parts, list) else [curve]}


def _checked_recipe(
    document: str, index: int, kind: str, geometry: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """Builds the v2 recipe of one v1 form, refusing a geometry the recipe cannot hold."""
    fields = _svg_fields(geometry) if kind == "svg" else geometry
    recipe: dict[str, JsonValue] = {"kind": kind, "recipe_version": RECIPE_VERSION, **fields}
    try:
        checked = RECIPE_ADAPTER.validate_python(recipe)
    except ValidationError as exc:
        error = exc.errors()[0]
        loc = [str(part) for part in error["loc"][1:]]
        if loc[:1] == [SVG_V2_CURVES]:
            loc = [SVG_V1_CURVE]
        field = ".".join([f"[{index}].geometry", *loc])
        value = geometry.get(loc[0]) if loc else geometry
        raise _refusal(document, field, value, f"cannot be stored in v2: {error['msg']}") from exc
    except ShowPyError as exc:
        where = f"[{index}].geometry"
        raise _refusal(document, where, geometry, f"cannot be stored in v2: {exc.message}") from exc
    stored: dict[str, JsonValue] = RECIPE_ADAPTER.dump_python(checked, mode="json")
    return stored


def _slot_orders(set_doc: JsonValue) -> dict[str, dict[str, int]]:
    """Returns each form's spot-to-slot orders from a set document's placements."""
    placements = set_doc.get("placements") if isinstance(set_doc, dict) else None
    orders: dict[str, dict[str, int]] = {}
    for row in placements if isinstance(placements, list) else []:
        if not isinstance(row, dict):
            continue
        form, entity, order = row.get("form"), row.get("entity"), row.get("order")
        if isinstance(form, str) and isinstance(entity, str) and type(order) is int:
            orders.setdefault(form, {})[entity] = order
    return orders


def _slotted_members(members: list[JsonValue], orders: dict[str, int]) -> list[JsonValue] | None:
    """Lays members out at their placement orders; None when they already sit on 0, 1, 2, ..."""
    if not all(isinstance(m, str) and m in orders for m in members):
        return None
    slots = [orders[m] for m in members if isinstance(m, str)]
    if (
        len(set(slots)) != len(slots)
        or min(slots, default=0) < 0
        or slots == list(range(len(slots)))
    ):
        return None
    laid: list[JsonValue] = [None] * (max(slots) + 1)
    for member, slot in zip(members, slots, strict=True):
        laid[slot] = member
    return laid


def _with_open_slots(
    document: str,
    form: dict[str, JsonValue],
    orders: dict[str, dict[str, int]],
    report: "ReportBuilder",
) -> dict[str, JsonValue]:
    """Returns the form with gapped placement orders stored as open slots, reporting the repair."""
    members, form_id = form.get("members"), form.get("id")
    if not isinstance(members, list) or not isinstance(form_id, str) or form_id not in orders:
        return form
    laid = _slotted_members(members, orders[form_id])
    if laid is None:
        return form
    open_slots = [slot for slot, member in enumerate(laid) if member is None]
    report.repair(
        f"{document}: form {form_id} stood on gapped slots; stored slots {open_slots} as open slots"
    )
    return {**form, "members": laid}


def _require_gaps_not_negative(document: str, index: int, spacing: JsonValue) -> None:
    """Refuses a stored spacing with a negative extra gap, which v2 cannot hold."""
    gaps = spacing.get("gaps") if isinstance(spacing, dict) else None
    for slot, gap in enumerate(gaps if isinstance(gaps, list) else []):
        amount = gap[1] if isinstance(gap, list) and len(gap) == GAP_PAIR_LENGTH else None
        if isinstance(amount, int | float) and amount < 0:
            raise _refusal(
                document, f"[{index}].spacing.gaps[{slot}]", gap, "has a negative extra gap"
            )


def _migrated_form(
    document: str,
    index: int,
    form: JsonValue,
    orders: dict[str, dict[str, int]],
    report: "ReportBuilder",
) -> dict[str, JsonValue]:
    if not isinstance(form, dict):
        raise _refusal(document, f"[{index}]", form, "is not an object")
    kind, geometry = _v1_parts(document, index, form)
    rest = {key: value for key, value in form.items() if key not in ("kind", "geometry")}
    recipe = _checked_recipe(document, index, kind, geometry)
    _require_gaps_not_negative(document, index, rest.get("spacing"))
    _require_members_fit(document, index, rest.get("members"), recipe.get("n"))
    stored = _with_open_slots(document, rest, orders, report)
    _require_members_fit(document, index, stored.get("members"), recipe.get("n"))
    return {**stored, "recipe": recipe}


def _require_members_fit(document: str, index: int, members: JsonValue, slots: JsonValue) -> None:
    """Refuses a form that stores more members than its slot count, which v2 cannot hold."""
    if isinstance(members, list) and isinstance(slots, int) and len(members) > slots:
        raise _refusal(
            document, f"[{index}].members", members, f"holds more members than its {slots} slots"
        )


def _migrated_document(
    document: str, forms: JsonValue, set_doc: JsonValue, report: "ReportBuilder"
) -> list[JsonValue]:
    if not isinstance(forms, list):
        raise _refusal(document, "(root)", forms, "is not a list of forms")
    orders = _slot_orders(set_doc)
    return [_migrated_form(document, i, form, orders, report) for i, form in enumerate(forms)]


def migrate_forms(docs: dict[str, JsonValue], report: "ReportBuilder") -> dict[str, JsonValue]:
    """Rewrites forms documents from the v1 shape to the v2 shape, one typed recipe per form.

    `kind` and `geometry` become `recipe`; every other document and form field passes through
    as it is, and the input is never mutated. A form whose spots stood on gapped slots stores
    them slot by slot with None at each open slot, read from the placements of the sibling
    `set.json`.

    Args:
        docs: Stored path to parsed document, without a format header.
        report: Collects the repairs the step makes; moving the fields repairs nothing, laying
            gapped members out as open slots records one repair per form.

    Returns:
        docs: The documents in the v2 shape.

    Raises:
        SchemaError: A form's kind, geometry or spacing cannot be stored in v2, or its members
            outnumber its slots; the context names the document (`path`), the field and the value.

    >>> from showpy.show.migrate import ReportBuilder
    >>> docs = {"/sets/1/forms.json": [{"id": "f", "kind": "custom",
    ...     "geometry": {"points": [[0, 0]], "n": 1}}]}
    >>> migrate_forms(docs, ReportBuilder(1))["/sets/1/forms.json"][0]["recipe"]["kind"]
    'custom'
    """
    return {
        path: _migrated_document(path, doc, docs.get(_set_document(path)), report)
        if FORMS_DOC.match(path)
        else doc
        for path, doc in docs.items()
    }
