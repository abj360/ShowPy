#!/usr/bin/env python3

"""
test_deprecations.py --- checks every sheet name that leaves the library warns and says when

Contains:
    test_leaving_sheet_names_warn: warns DeprecatedWarning on each leaving function call and
        each leaving type's top-level access, and returns what the implementation returns
    test_leaving_functions_are_decorated_as_their_rows_say: matches each wrapper's replacement,
        since and remove to its row
    test_leaving_rows_name_each_removal_release: pins the removal release of every row
    test_leaving_rows_cover_the_decided_names: counts the rows of each kind
    test_every_leaving_row_has_its_expected_message: pins one message ending to each of the rows
    test_every_leaving_row_names_a_replacement: keeps no row without a replacement
"""

import ast
import importlib
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest

import e8s
from e8s._api_table import LEAVING, PUBLIC_API
from e8s.core.deprecation import DeprecatedWarning
from e8s.sheet.models import GuideLine
from e8s.sheet.surfaces import NFHS_HASH
from e8s.sheet.templates import InMemorySheetStore, _blank_sheet

SHEET_SOURCE = Path(e8s.__file__).parent / "sheet"
SHEET = _blank_sheet(10.0, 6.0)
POINT = (1.0, 1.0)
TAPE = GuideLine(id="tape", kind="custom", points=((0.0, 1.0), (1.0, 1.0)))
type LeavingRow = tuple[str, str, str | tuple[str, ...] | None, str, str]


CALLS: dict[str, Callable[[Callable[..., object]], object]] = {
    "sheet_templates": lambda f: f(),
    "get_sheet": lambda f: f("sheet:nfl-football"),
    "customize": lambda f: f(SHEET, name="renamed"),
    "football_field": lambda f: f("test-field", "Test field", NFHS_HASH),
    "gym_floor": lambda f: f(),
    "on_surface": lambda f: f(SHEET, POINT),
    "blank_sheet": lambda f: f(10.0, 6.0),
    "add_guide": lambda f: f(SHEET, TAPE),
    "remove_guide": lambda f: f(SHEET, "center-line"),
    "save_sheet": lambda f: f(InMemorySheetStore(), SHEET),
    "template_ref": lambda f: f("test-field"),
}
FUNCTION_ROWS = [row for row in LEAVING if row[1] == "function"]
TYPE_ROWS = [row for row in LEAVING if row[1] == "type"]
PUBLIC_MODULES = {name: module for name, module, _attribute in PUBLIC_API}
SHEET_SHAPE = (
    "; its replacements `e8s.sheets`, `e8s.sheet`, `e8s.field`, `e8s.floor`, "
    "`e8s.Marking` and `e8s.on_sheet` arrive in 0.7.0."
)


def _single(replacement: str) -> str:
    return f"; its replacement `e8s.{replacement}` arrives in 0.7.0."


ENDINGS: dict[str, str] = {
    "sheet_templates": _single("sheets"),
    "get_sheet": _single("sheet"),
    "customize": SHEET_SHAPE,
    "football_field": _single("field"),
    "gym_floor": _single("floor"),
    "GuideLine": _single("Marking"),
    "on_surface": _single("on_sheet"),
    "blank_sheet": SHEET_SHAPE,
    "add_guide": SHEET_SHAPE,
    "remove_guide": SHEET_SHAPE,
    "save_sheet": SHEET_SHAPE,
    "template_ref": SHEET_SHAPE,
    "SheetStore": SHEET_SHAPE,
    "InMemorySheetStore": SHEET_SHAPE,
}


def _leaving_text(qualified: str, name: str, remove: str) -> str:
    text = f"`{qualified}` is deprecated since 0.4.0 and leaves the library in {remove}"
    return f"{text}{ENDINGS[name]}"


@pytest.mark.parametrize("row", FUNCTION_ROWS, ids=[row[0] for row in FUNCTION_ROWS])
def test_leaving_sheet_names_warn(row: LeavingRow) -> None:
    name, _kind, _replacement, _since, remove = row
    module = importlib.import_module(PUBLIC_MODULES[name])
    public, implementation = getattr(module, name), getattr(module, f"_{name}")
    with pytest.warns(DeprecatedWarning) as caught:
        result = CALLS[name](public)
    assert len(caught) == 1
    assert str(caught[0].message) == _leaving_text(f"{module.__name__}.{name}", name, remove)
    assert caught[0].filename == __file__
    assert result == CALLS[name](implementation)


@pytest.mark.parametrize("row", TYPE_ROWS, ids=[row[0] for row in TYPE_ROWS])
def test_leaving_sheet_types_warn_on_top_level_access(
    row: LeavingRow,
) -> None:
    name, _kind, _replacement, _since, remove = row
    module = importlib.import_module(PUBLIC_MODULES[name])
    for _ in range(2):
        with pytest.warns(DeprecatedWarning) as caught:
            resolved = getattr(e8s, name)
        assert str(caught[0].message) == _leaving_text(f"e8s.{name}", name, remove)
        assert caught[0].filename == __file__
        assert resolved is getattr(module, name)
    assert name not in vars(e8s)


def _decorated_rows() -> dict[str, tuple[str | tuple[str, ...] | None, str, str]]:
    found: dict[str, tuple[str | tuple[str, ...] | None, str, str]] = {}
    for path in sorted(SHEET_SOURCE.glob("*.py")):
        for node in ast.parse(path.read_text()).body:
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and getattr(decorator.func, "id", "") == (
                    "leaving_call"
                ):
                    keywords = {kw.arg: ast.literal_eval(kw.value) for kw in decorator.keywords}
                    assert not decorator.args
                    found[node.name] = (
                        keywords.get("replacement"),
                        keywords["since"],
                        keywords["remove"],
                    )
    return found


def test_leaving_functions_are_decorated_as_their_rows_say() -> None:
    decorated = _decorated_rows()
    rows = {
        name: (replacement, since, remove)
        for name, _kind, replacement, since, remove in FUNCTION_ROWS
    }
    assert decorated == rows


def test_leaving_rows_name_each_removal_release() -> None:
    removal = {name: remove for name, _kind, _replacement, _since, remove in LEAVING}
    assert set(removal.values()) == {"0.7.0"}
    assert {since for *_rest, since, _remove in LEAVING} == {"0.4.0"}


def test_leaving_rows_cover_the_decided_names() -> None:
    groups = Counter(kind for _name, kind, *_rest in LEAVING)
    assert groups == {"function": 11, "type": 3}
    assert len(LEAVING) == 14
    assert len({name for name, *_rest in LEAVING}) == 14


def test_every_leaving_row_has_its_expected_message() -> None:
    assert list(ENDINGS) == [name for name, *_rest in LEAVING]


def test_every_leaving_row_names_a_replacement() -> None:
    assert [name for name, _kind, replacement, *_rest in LEAVING if replacement is None] == []
