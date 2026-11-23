#!/usr/bin/env python3

"""
test_deprecation.py --- tests the deprecation helpers and the ValidationError alias

Contains:
    test_moved_name_warns_and_resolves: warns once per access and returns the new object
    test_moved_rejects_unknown_names: raises AttributeError for a name outside the table
    test_renamed_field_warns_and_reads_the_new_field: reads the new field through the old name
    test_renamed_field_passes_the_new_value_through_read: serves read(new value) under the old name
    test_deprecated_call_warns_names_the_replacement_and_forwards: warns at the caller, then runs
    test_deprecated_call_keeps_the_wrapped_signature: keeps name, docstring and parameters
    test_warn_deprecated_points_at_the_callers_line: attributes the warning to the caller's line,
        also through a helper with extra_frames=1
    test_warn_deprecated_stays_silent_on_the_valid_path: no warning when no Set is given
    test_deprecated_warning_is_a_deprecation_warning: checks the base class
    test_validation_error_alias_warns_and_is_the_same_class: checks the alias on both paths
    test_deprecated_names_reach_users_through_e8s: resolves every table row with a warning
    test_deprecated_class_alias_is_visible_to_type_checkers: types check both aliases statically
    test_deprecations_are_removed_on_time: fails when a row outlives its removal version
    test_every_deprecation_call_is_removed_on_time: fails when a moved, renamed_field,
        renamed_keyword or deprecated_call call in e8s outlives its removal version
    test_deprecation_scan_flags_overdue_and_non_literal_calls: proves the scan can fail
    is_scan_skipped(): tells whether the scan skips a module (only the root __init__.py)
    test_deprecation_scan_*(): attribute calls, malformed `remove`, scoped local defs, skips
    test_every_deprecated_keyword_row_names_real_parameters: checks each row's old and new keyword
    test_warn_leaving_words_any_number_of_replacements: words none, one, two and three names
    test_leaving_call_names_several_replacements: lists each name in its given order
"""

import ast
import inspect
import re
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import e8s
import e8s.core.errors
from e8s._api_table import DEPRECATED, DEPRECATED_KEYWORDS, LEAVING
from e8s.core.deprecation import (
    DeprecatedWarning,
    deprecated_call,
    leaving_call,
    moved,
    renamed_field,
    warn_deprecated,
    warn_leaving,
)
from e8s.core.errors import ShowValidationError
from e8s.core.util import approx_equal

if TYPE_CHECKING:
    from e8s import ValidationError as PackageAlias
    from e8s.core.errors import ValidationError as ModuleAlias

VERSION_PREFIX = re.compile(r"^(\d+)\.(\d+)")
RELEASE_PATTERN = re.compile(r"\d+\.\d+\.\d+")
SOURCE_ROOT = Path(e8s.__file__).parent
SCAN_SKIPPED = ("__init__.py", "core/deprecation.py")
TURN_WARNING = r"`turn\(frozenset\)` is replaced by `turn_set`; the old use is removed in 0\.6\.0"
REQUIRED_KEYWORDS = {
    "moved": ("since", "remove"),
    "renamed_field": ("since", "remove"),
    "renamed_keyword": ("remove",),
    "deprecated_call": ("since", "remove"),
    "warn_deprecated": ("since", "remove"),
    "leaving_call": ("since", "remove"),
    "warn_leaving": ("since", "remove"),
    "leave_field": ("since", "remove"),
}


def _doubled(value: int) -> int:
    return value * 2


@deprecated_call("e8s.core.util.approx_equal", since="0.4.0", remove="0.6.0")
def _old_close(a: float, b: float, *, slack: float = 1e-6) -> bool:
    """Compares two numbers the old way."""
    return approx_equal(a, b, abs_tol=slack)


@deprecated_call(approx_equal, since="0.4.0", remove="0.6.0")
def _old_close_by_callable(a: float, b: float) -> bool:
    return approx_equal(a, b)


def _turn(target: object) -> str:
    """Forwards a set to its replacement with a warning; other targets stay valid."""
    if isinstance(target, frozenset):
        warn_deprecated("turn(frozenset)", "turn_set", since="0.4.0", remove="0.6.0")
    return "turned"


def _warn_from_helper() -> None:
    warn_deprecated("turn(frozenset)", "turn_set", since="0.4.0", remove="0.6.0", extra_frames=1)


def _turn_through_helper(target: object) -> str:
    if isinstance(target, frozenset):
        _warn_from_helper()
    return "turned"


class _Holder:
    value = 7
    legacy = renamed_field("value", since="0.4.0", remove="0.6.0")
    twice = renamed_field("value", since="0.4.0", remove="0.6.0", read=_doubled)


def test_moved_name_warns_and_resolves() -> None:
    getter = moved(
        "pkg.old", {"Old": "e8s.core.errors.ShowValidationError"}, since="0.4.0", remove="0.6.0"
    )
    with pytest.warns(DeprecatedWarning, match="`Old` moved to .*ShowValidationError.*0.6.0"):
        assert getter("Old") is ShowValidationError


def test_moved_rejects_unknown_names() -> None:
    getter = moved("pkg.old", {}, since="0.4.0", remove="0.6.0")
    with pytest.raises(AttributeError, match=r"pkg\.old"):
        getter("Missing")


def test_renamed_field_warns_and_reads_the_new_field() -> None:
    with pytest.warns(DeprecatedWarning, match="legacy.*value.*0.6.0"):
        assert _Holder().legacy == _Holder.value


def test_renamed_field_passes_the_new_value_through_read() -> None:
    with pytest.warns(DeprecatedWarning, match="`twice` renamed to `value`"):
        assert _Holder().twice == 2 * _Holder.value


def test_deprecated_call_warns_names_the_replacement_and_forwards() -> None:
    pattern = r"`.*_old_close` is replaced by `e8s\.core\.util\.approx_equal`.*0\.6\.0"
    with pytest.warns(DeprecatedWarning, match=pattern) as caught:
        assert _old_close(1.0, 1.05, slack=0.1) is True
    assert caught[0].filename == __file__
    with pytest.warns(DeprecatedWarning, match=r"replaced by `e8s\.core\.util\.approx_equal`"):
        assert _old_close_by_callable(1.0, 2.0) is False


def test_warn_deprecated_points_at_the_callers_line() -> None:
    frame = inspect.currentframe()
    assert frame is not None
    line = frame.f_lineno + 2
    with pytest.warns(DeprecatedWarning, match=TURN_WARNING) as direct:
        _turn(frozenset())
    helper_line = frame.f_lineno + 2
    with pytest.warns(DeprecatedWarning, match=TURN_WARNING) as through_helper:
        _turn_through_helper(frozenset())
    assert (direct[0].filename, direct[0].lineno) == (__file__, line)
    assert (through_helper[0].filename, through_helper[0].lineno) == (__file__, helper_line)


def test_warn_deprecated_stays_silent_on_the_valid_path() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert _turn((1.0, 2.0)) == "turned"


def test_deprecated_call_keeps_the_wrapped_signature() -> None:
    assert _old_close.__name__ == "_old_close"
    assert _old_close.__doc__ == "Compares two numbers the old way."
    assert list(inspect.signature(_old_close).parameters) == ["a", "b", "slack"]


def test_deprecated_warning_is_a_deprecation_warning() -> None:
    assert issubclass(DeprecatedWarning, DeprecationWarning)
    assert e8s.DeprecatedWarning is DeprecatedWarning
    assert e8s.core.errors.DeprecatedWarning is DeprecatedWarning


def test_validation_error_alias_warns_and_is_the_same_class() -> None:
    with pytest.warns(DeprecatedWarning):
        from_package = e8s.ValidationError
    with pytest.warns(DeprecatedWarning):
        from_errors = e8s.core.errors.ValidationError
    assert from_package is ShowValidationError
    assert from_errors is ShowValidationError
    assert issubclass(ShowValidationError, from_package)
    with pytest.raises(from_package):
        raise ShowValidationError("bad", hint="fix it")


def test_deprecated_names_reach_users_through_e8s() -> None:
    for name, _target, _since, _remove in DEPRECATED:
        with pytest.warns(DeprecatedWarning, match=name):
            getattr(e8s, name)


def test_deprecations_are_removed_on_time() -> None:
    match = VERSION_PREFIX.match(e8s.__version__)
    assert match is not None
    current = (int(match[1]), int(match[2]))
    overdue = []
    for name, _target, _since, remove in DEPRECATED:
        major, minor, _patch = remove.split(".")
        if (int(major), int(minor)) <= current:
            overdue.append(f"{name} (remove {remove})")
    for function, old, _new, _since, remove in DEPRECATED_KEYWORDS:
        major, minor, _patch = remove.split(".")
        if (int(major), int(minor)) <= current:
            overdue.append(f"{function}({old}=) (remove {remove})")
    overdue += overdue_leaving_rows(LEAVING, current)
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


def _accepts_both_aliases(
    from_package: "PackageAlias", from_module: "ModuleAlias"
) -> list[ShowValidationError]:
    """Type-checks that both deprecated names are the class `ShowValidationError`."""
    return [from_package, from_module]


def test_deprecated_class_alias_is_visible_to_type_checkers() -> None:
    assert _accepts_both_aliases.__annotations__["from_package"] == "PackageAlias"


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
    """Returns the helper name a call spells, as `moved(...)` or `deprecation.moved(...)`."""
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
    overdue = 'getter = moved("m", {}, since="0.1.0", remove="0.0.1")\n'
    dynamic = 'getter = moved("m", {}, since="0.4.0", remove=LIMIT)\n'
    keyword = 'x = renamed_keyword("a", 1, "b", None, default=0, remove=LIMIT)\n'
    decorator = '@deprecated_call("m.f", since="0.1.0", remove="0.0.1")\ndef g() -> None: ...\n'
    conditional = 'warn_deprecated("f(Set)", "g", since="0.1.0", remove="0.0.1")\n'
    ok = 'getter = moved("m", {}, since="0.4.0", remove="99.0.0")\n'
    assert "overdue" in scan_deprecation_calls(overdue, "t.py")[0]
    assert "literal remove=" in scan_deprecation_calls(dynamic, "t.py")[0]
    assert "literal remove=" in scan_deprecation_calls(keyword, "t.py")[0]
    assert (
        "deprecated_call() remove=0.0.1 is overdue" in scan_deprecation_calls(decorator, "t.py")[0]
    )
    assert scan_deprecation_calls(conditional, "t.py") == [
        "t.py:1: warn_deprecated() remove=0.0.1 is overdue"
    ]
    assert scan_deprecation_calls(ok, "t.py") == []


def test_deprecation_scan_sees_attribute_calls() -> None:
    source = 'getter = deprecation.moved("m", {}, since="0.1.0", remove="0.0.1")\n'
    assert scan_deprecation_calls(source, "t.py") == ["t.py:1: moved() remove=0.0.1 is overdue"]


def test_deprecation_scan_reports_a_malformed_remove() -> None:
    source = 'getter = moved("m", {}, since="0.4.0", remove="0.6")\n'
    assert scan_deprecation_calls(source, "t.py") == [
        "t.py:1: moved() remove=0.6 is not MAJOR.MINOR.PATCH"
    ]


def test_deprecation_scan_skips_a_local_def_only_inside_its_function() -> None:
    source = (
        "def shift(count: float) -> float:\n"
        "    def moved(value: float) -> float:\n"
        "        return value + 1\n"
        "    return moved(count)\n"
        'getter = moved("m", {}, since="0.1.0", remove="0.0.1")\n'
    )
    assert scan_deprecation_calls(source, "t.py") == ["t.py:5: moved() remove=0.0.1 is overdue"]


def test_deprecation_scan_skips_only_the_root_init() -> None:
    assert is_scan_skipped("__init__.py")
    assert is_scan_skipped("core/deprecation.py")
    assert not is_scan_skipped("choreo/__init__.py")
    assert not is_scan_skipped("geo/__init__.py")


def test_every_deprecated_keyword_row_names_real_parameters() -> None:
    for function, old, new, _since, _remove in DEPRECATED_KEYWORDS:
        parameters = inspect.signature(getattr(e8s, function)).parameters
        assert old in parameters, f"{function}() has no keyword {old}"
        assert new in parameters, f"{function}() has no keyword {new}"


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
