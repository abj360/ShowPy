#!/usr/bin/env python3

"""
test_deprecation.py --- tests the helpers that warn when a name leaves the library

Contains:
    test_deprecated_warning_is_a_deprecation_warning: checks the base class
    test_deprecations_are_removed_on_time: fails when a leaving row outlives its removal version
    test_every_deprecation_call_is_removed_on_time: fails when a leaving_call or warn_leaving
        call in e8s outlives its removal version
    test_deprecation_scan_flags_overdue_and_non_literal_calls: proves the scan can fail
    is_scan_skipped(): tells whether the scan skips a module (only the root __init__.py)
    test_deprecation_scan_*(): attribute calls, malformed `remove`, scoped local defs, skips
    test_warn_leaving_words_any_number_of_replacements: words none, one, two and three names
    test_leaving_call_names_several_replacements: lists each name in its given order
"""

import ast
import re
from pathlib import Path

import pytest

import e8s
import e8s.core.errors
from e8s._api_table import LEAVING
from e8s.core.deprecation import DeprecatedWarning, leaving_call, warn_leaving

VERSION_PREFIX = re.compile(r"^(\d+)\.(\d+)")
RELEASE_PATTERN = re.compile(r"\d+\.\d+\.\d+")
SOURCE_ROOT = Path(e8s.__file__).parent
SCAN_SKIPPED = ("__init__.py", "core/deprecation.py")
REQUIRED_KEYWORDS = {
    "leaving_call": ("since", "remove"),
    "warn_leaving": ("since", "remove"),
}


def test_deprecated_warning_is_a_deprecation_warning() -> None:
    assert issubclass(DeprecatedWarning, DeprecationWarning)
    assert e8s.DeprecatedWarning is DeprecatedWarning
    assert e8s.core.errors.DeprecatedWarning is DeprecatedWarning


def test_deprecations_are_removed_on_time() -> None:
    match = VERSION_PREFIX.match(e8s.__version__)
    assert match is not None
    current = (int(match[1]), int(match[2]))
    overdue = overdue_leaving_rows(LEAVING, current)
    assert not overdue, f"E: deprecated names past their removal version: {', '.join(overdue)}"


def overdue_leaving_rows(
    rows: tuple[tuple[str, str, str | tuple[str, ...] | None, str, str], ...],
    current: tuple[int, int],
) -> list[str]:
    """Lists the leaving names whose removal release is the current release or an earlier one."""
    overdue = []
    for name, _kind, _replacement, _since, remove in rows:
        major, minor, _patch = remove.split(".")
        if (int(major), int(minor)) <= current:
            overdue.append(f"{name} (remove {remove})")
    return overdue


def test_leaving_scan_flags_a_type_row_past_its_removal_version() -> None:
    rows = (("Landmark", "type", ("Marking", "markings_near"), "0.4.0", "0.7.0"),)
    assert overdue_leaving_rows(rows, (0, 6)) == []
    assert overdue_leaving_rows(rows, (0, 7)) == ["Landmark (remove 0.7.0)"]


def _current_minor() -> tuple[int, int]:
    match = VERSION_PREFIX.match(e8s.__version__)
    assert match is not None
    return int(match[1]), int(match[2])


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """Maps each module-level name bound to a string literal to that string."""
    constants = {}
    for node in tree.body:
        target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
        value = getattr(node, "value", None)
        text = value.value if isinstance(value, ast.Constant) else None
        if isinstance(target, ast.Name) and isinstance(text, str):
            constants[target.id] = text
    return constants


def _literal(node: ast.expr | None, constants: dict[str, str]) -> str | None:
    """Returns the string a keyword value spells out, or None when it is not a plain literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _called_name(node: ast.Call) -> str | None:
    """Returns the helper name a call spells, as `warn_leaving(...)` or an attribute call."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _defined_names(scope: ast.AST) -> set[str]:
    """Names of the functions defined directly or nested inside a scope, the scope excluded."""
    return {
        node.name
        for node in ast.walk(scope)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node is not scope
    }


def _helper_calls(node: ast.AST, shadowed: frozenset[str]) -> list[tuple[str, ast.Call]]:
    """Lists the helper calls under a node, skipping names a local def shadows in its function."""
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        shadowed = shadowed | _defined_names(node)
    found: list[tuple[str, ast.Call]] = []
    if isinstance(node, ast.Call):
        name = _called_name(node)
        if name in REQUIRED_KEYWORDS and name not in shadowed:
            found.append((name, node))
    for child in ast.iter_child_nodes(node):
        found += _helper_calls(child, shadowed)
    return found


def _remove_problem(name: str, remove: str, where: str) -> str | None:
    """Describes a malformed or overdue `remove` version, or returns None when it is fine."""
    if not RELEASE_PATTERN.fullmatch(remove):
        return f"{where}: {name}() remove={remove} is not MAJOR.MINOR.PATCH"
    major, minor, _patch = remove.split(".")
    if (int(major), int(minor)) <= _current_minor():
        return f"{where}: {name}() remove={remove} is overdue"
    return None


def scan_deprecation_calls(source: str, where: str) -> list[str]:
    """Lists the problems in the deprecation helper calls of one source.

    A keyword must be a string literal or a module-level name bound to one.
    """
    tree = ast.parse(source)
    constants = _string_constants(tree)
    top_level = frozenset(
        node.name for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    )
    problems: list[str] = []
    for name, node in _helper_calls(tree, top_level):
        keywords = {kw.arg: _literal(kw.value, constants) for kw in node.keywords}
        problems.extend(
            f"{where}:{node.lineno}: {name}() needs a literal {required}="
            for required in REQUIRED_KEYWORDS[name]
            if keywords.get(required) is None
        )
        remove = keywords.get("remove")
        problem = (
            None if remove is None else _remove_problem(name, remove, f"{where}:{node.lineno}")
        )
        if problem is not None:
            problems.append(problem)
    return problems


def is_scan_skipped(relative: str) -> bool:
    """Tells whether the scan skips a module, given its path relative to the e8s package."""
    return relative in SCAN_SKIPPED


def test_every_deprecation_call_is_removed_on_time() -> None:
    problems = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        relative = path.relative_to(SOURCE_ROOT).as_posix()
        if is_scan_skipped(relative):
            continue
        problems += scan_deprecation_calls(path.read_text(), relative)
    assert not problems, "E: " + "; ".join(problems)


def test_deprecation_scan_flags_overdue_and_non_literal_calls() -> None:
    overdue = 'warn_leaving("m", since="0.1.0", remove="0.0.1")\n'
    dynamic = 'warn_leaving("m", since="0.4.0", remove=LIMIT)\n'
    decorator = '@leaving_call(since="0.1.0", remove="0.0.1")\ndef g() -> None: ...\n'
    ok = 'warn_leaving("m", since="0.4.0", remove="99.0.0")\n'
    assert "overdue" in scan_deprecation_calls(overdue, "t.py")[0]
    assert "literal remove=" in scan_deprecation_calls(dynamic, "t.py")[0]
    assert "leaving_call() remove=0.0.1 is overdue" in scan_deprecation_calls(decorator, "t.py")[0]
    assert scan_deprecation_calls(ok, "t.py") == []


def test_deprecation_scan_sees_attribute_calls() -> None:
    source = 'deprecation.warn_leaving("m", since="0.1.0", remove="0.0.1")\n'
    assert scan_deprecation_calls(source, "t.py") == [
        "t.py:1: warn_leaving() remove=0.0.1 is overdue"
    ]


def test_deprecation_scan_reports_a_malformed_remove() -> None:
    source = 'warn_leaving("m", since="0.4.0", remove="0.6")\n'
    assert scan_deprecation_calls(source, "t.py") == [
        "t.py:1: warn_leaving() remove=0.6 is not MAJOR.MINOR.PATCH"
    ]


def test_deprecation_scan_skips_a_local_def_only_inside_its_function() -> None:
    source = (
        "def shift(count: float) -> float:\n"
        "    def warn_leaving(value: float) -> float:\n"
        "        return value + 1\n"
        "    return warn_leaving(count)\n"
        'warn_leaving("m", since="0.1.0", remove="0.0.1")\n'
    )
    assert scan_deprecation_calls(source, "t.py") == [
        "t.py:5: warn_leaving() remove=0.0.1 is overdue"
    ]


def test_deprecation_scan_skips_only_the_root_init() -> None:
    assert is_scan_skipped("__init__.py")
    assert is_scan_skipped("core/deprecation.py")
    assert not is_scan_skipped("choreo/__init__.py")
    assert not is_scan_skipped("geo/__init__.py")


LEAVING_TEXT = "`e8s.old` is deprecated since 0.4.0 and leaves the library in 0.7.0."


@leaving_call(since="0.4.0", remove="99.0.0")
def _leaving_function() -> str:
    return "ran"


def _use_leaving_name() -> None:
    warn_leaving("e8s.old", since="0.4.0", remove="0.7.0")


def test_warn_leaving_words_the_message_without_a_replacement() -> None:
    with pytest.warns(DeprecatedWarning) as caught:
        _use_leaving_name()
    assert str(caught[0].message) == LEAVING_TEXT
    assert caught[0].filename == __file__


REPLACEMENT_TEXT = (
    "`e8s.old` is deprecated since 0.4.0 and leaves the library in 0.7.0; "
    "its replacement `e8s.new` arrives in 0.7.0."
)


def _use_leaving_name_with_replacement() -> None:
    warn_leaving("e8s.old", since="0.4.0", remove="0.7.0", replacement="new")


def test_warn_leaving_names_the_replacement_when_there_is_one() -> None:
    with pytest.warns(DeprecatedWarning) as caught:
        _use_leaving_name_with_replacement()
    assert str(caught[0].message) == REPLACEMENT_TEXT
    assert caught[0].filename == __file__


@leaving_call(since="0.4.0", remove="99.0.0", replacement="newer")
def _leaving_function_with_replacement() -> str:
    return "ran"


def test_leaving_call_names_the_replacement_when_there_is_one() -> None:
    with pytest.warns(DeprecatedWarning) as caught:
        assert _leaving_function_with_replacement() == "ran"
    assert str(caught[0].message) == (
        f"`{__name__}._leaving_function_with_replacement` is deprecated since 0.4.0 and leaves "
        "the library in 99.0.0; its replacement `e8s.newer` arrives in 99.0.0."
    )


TWO_REPLACEMENTS_TEXT = (
    "`e8s.old` is deprecated since 0.4.0 and leaves the library in 0.7.0; "
    "its replacements `e8s.Marking` and `e8s.markings_near` arrive in 0.7.0."
)
THREE_REPLACEMENTS_TEXT = (
    "`e8s.old` is deprecated since 0.4.0 and leaves the library in 0.7.0; "
    "its replacements `e8s.locate`, `e8s.at_location` and `e8s.Location` arrive in 0.7.0."
)


def _use_leaving_name_replaced_by(replacement: str | tuple[str, ...] | None) -> None:
    warn_leaving("e8s.old", since="0.4.0", remove="0.7.0", replacement=replacement)


@pytest.mark.parametrize(
    ("replacement", "text"),
    [
        (None, LEAVING_TEXT),
        ("new", REPLACEMENT_TEXT),
        (("new",), REPLACEMENT_TEXT),
        (("Marking", "markings_near"), TWO_REPLACEMENTS_TEXT),
        (("locate", "at_location", "Location"), THREE_REPLACEMENTS_TEXT),
    ],
    ids=["none", "one", "one-in-a-tuple", "two", "three"],
)
def test_warn_leaving_words_any_number_of_replacements(
    replacement: str | tuple[str, ...] | None, text: str
) -> None:
    with pytest.warns(DeprecatedWarning) as caught:
        _use_leaving_name_replaced_by(replacement)
    assert str(caught[0].message) == text
    assert caught[0].filename == __file__


@leaving_call(since="0.4.0", remove="99.0.0", replacement=("sheets", "sheet", "field"))
def _leaving_function_with_replacements() -> str:
    return "ran"


def test_leaving_call_names_several_replacements() -> None:
    with pytest.warns(DeprecatedWarning) as caught:
        assert _leaving_function_with_replacements() == "ran"
    assert str(caught[0].message) == (
        f"`{__name__}._leaving_function_with_replacements` is deprecated since 0.4.0 and leaves "
        "the library in 99.0.0; its replacements `e8s.sheets`, `e8s.sheet` and "
        "`e8s.field` arrive in 99.0.0."
    )
    assert caught[0].filename == __file__


def test_leaving_call_warns_on_each_call_and_runs_the_function() -> None:
    for _ in range(2):
        with pytest.warns(DeprecatedWarning) as caught:
            assert _leaving_function() == "ran"
        assert str(caught[0].message) == (
            f"`{__name__}._leaving_function` is deprecated since 0.4.0 and leaves the library "
            "in 99.0.0."
        )
        assert caught[0].filename == __file__
