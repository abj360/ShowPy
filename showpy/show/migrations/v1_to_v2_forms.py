"""
v1_to_v2_forms.py --- the forms sub-step of the v1 to v2 format migration (lane G)

A v1 form stores its shape family in `kind` and an untyped JSON `geometry` beside it. v2 stores
one typed `recipe` that carries the kind and `recipe_version: 1`, so this step moves `kind` and
every geometry key into the recipe, checks the result against the v2 recipe of that kind and
stores it with every default written out, as a v2 save writes it.
A geometry that no v2 recipe can hold (an unknown kind, an unknown key, a value out of range)
is refused with SchemaError naming the document, the field and the value; nothing is guessed.

Contains:
    migrate_forms(): rewrites forms documents from the v1 shape to the v2 shape
"""

import re
from typing import TYPE_CHECKING, Final, get_args

from pydantic import TypeAdapter, ValidationError

from showpy.choreo.form_models import FormKind, Recipe
from showpy.choreo.recipe_models import RECIPE_VERSION
from showpy.core.errors import SchemaError, bounded_repr
from showpy.core.json_types import JsonValue

if TYPE_CHECKING:
    from showpy.show.migrate import ReportBuilder

FORMS_DOC: Final = re.compile(r"^/sets/[^/]+/forms\.json$")
KINDS: Final = frozenset(get_args(FormKind.__value__))
RECIPE_ADAPTER: Final[TypeAdapter[Recipe]] = TypeAdapter(Recipe)
V2_ONLY_KEYS: Final = frozenset({"kind", "recipe_version"})


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
    taken = sorted(V2_ONLY_KEYS & geometry.keys())
    if taken:
        key = taken[0]
        raise _refusal(document, f"{where}.geometry.{key}", geometry[key], "is reserved in v2")
    return kind, geometry


def _checked_recipe(
    document: str, index: int, kind: str, geometry: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """Builds the v2 recipe of one v1 form, refusing a geometry the recipe cannot hold."""
    recipe: dict[str, JsonValue] = {"kind": kind, "recipe_version": RECIPE_VERSION, **geometry}
    try:
        checked = RECIPE_ADAPTER.validate_python(recipe)
    except ValidationError as exc:
        error = exc.errors()[0]
        loc = [str(part) for part in error["loc"][1:]]
        field = ".".join([f"[{index}].geometry", *loc])
        value = geometry.get(loc[0]) if loc else geometry
        raise _refusal(document, field, value, f"cannot be stored in v2: {error['msg']}") from exc
    stored: dict[str, JsonValue] = RECIPE_ADAPTER.dump_python(checked, mode="json")
    return stored


def _migrated_form(document: str, index: int, form: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(form, dict):
        raise _refusal(document, f"[{index}]", form, "is not an object")
    kind, geometry = _v1_parts(document, index, form)
    rest = {key: value for key, value in form.items() if key not in ("kind", "geometry")}
    return {**rest, "recipe": _checked_recipe(document, index, kind, geometry)}


def _migrated_document(document: str, forms: JsonValue) -> list[JsonValue]:
    if not isinstance(forms, list):
        raise _refusal(document, "(root)", forms, "is not a list of forms")
    return [_migrated_form(document, index, form) for index, form in enumerate(forms)]


def migrate_forms(docs: dict[str, JsonValue], report: "ReportBuilder") -> dict[str, JsonValue]:
    """Rewrites forms documents from the v1 shape to the v2 shape, one typed recipe per form.

    `kind` and `geometry` become `recipe`; every other document and form field passes through
    as it is, and the input is never mutated.

    Args:
        docs: Stored path to parsed document, without a format header.
        report: Collects the repairs the step makes; moving the fields repairs nothing.

    Returns:
        docs: The documents in the v2 shape.

    Raises:
        SchemaError: A form's kind or geometry cannot be stored in v2; the context names the
            document (`path`), the field and the value.

    >>> from showpy.show.migrate import ReportBuilder
    >>> docs = {"/sets/1/forms.json": [{"id": "f", "kind": "custom",
    ...     "geometry": {"points": [[0, 0]], "n": 1}}]}
    >>> migrate_forms(docs, ReportBuilder(1))["/sets/1/forms.json"][0]["recipe"]["kind"]
    'custom'
    """
    del report
    return {
        path: _migrated_document(path, doc) if FORMS_DOC.match(path) else doc
        for path, doc in docs.items()
    }
