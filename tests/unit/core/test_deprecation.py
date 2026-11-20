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
    test_deprecated_warning_is_a_deprecation_warning: checks the base class
    test_validation_error_alias_warns_and_is_the_same_class: checks the alias on both paths
    test_deprecated_names_reach_users_through_showpy: resolves every table row with a warning
    test_deprecated_class_alias_is_visible_to_type_checkers: types check both aliases statically
    test_deprecations_are_removed_on_time: fails when a row outlives its removal version
    test_every_deprecation_call_is_removed_on_time: fails when a moved, renamed_field,
        renamed_keyword or deprecated_call call in showpy outlives its removal version
    test_deprecation_scan_flags_overdue_and_non_literal_calls: proves the scan can fail
    is_scan_skipped(): tells whether the scan skips a module (only the root __init__.py)
    test_deprecation_scan_*(): attribute calls, malformed `remove`, scoped local defs, skips
    test_every_deprecated_keyword_row_names_real_parameters: checks each row's old and new keyword
"""

import ast
import inspect
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import showpy
import showpy.core.errors
from showpy._api_table import DEPRECATED, DEPRECATED_KEYWORDS
from showpy.core.deprecation import DeprecatedWarning, deprecated_call, moved, renamed_field
from showpy.core.errors import ShowValidationError
from showpy.core.util import approx_equal

if TYPE_CHECKING:
    from showpy import ValidationError as PackageAlias
    from showpy.core.errors import ValidationError as ModuleAlias

VERSION_PREFIX = re.compile(r"^(\d+)\.(\d+)")
RELEASE_PATTERN = re.compile(r"\d+\.\d+\.\d+")
SOURCE_ROOT = Path(showpy.__file__).parent
SCAN_SKIPPED = ("__init__.py", "core/deprecation.py")
REQUIRED_KEYWORDS = {
    "moved": ("since", "remove"),
    "renamed_field": ("since", "remove"),
    "renamed_keyword": ("remove",),
    "deprecated_call": ("since", "remove"),
}


def _doubled(value: int) -> int:
    return value * 2


@deprecated_call("showpy.core.util.approx_equal", since="0.4.0", remove="0.6.0")
def _old_close(a: float, b: float, *, slack: float = 1e-6) -> bool:
    """Compares two numbers the old way."""
    return approx_equal(a, b, abs_tol=slack)


@deprecated_call(approx_equal, since="0.4.0", remove="0.6.0")
def _old_close_by_callable(a: float, b: float) -> bool:
    return approx_equal(a, b)


class _Holder:
    value = 7
    legacy = renamed_field("value", since="0.4.0", remove="0.6.0")
    twice = renamed_field("value", since="0.4.0", remove="0.6.0", read=_doubled)


def test_moved_name_warns_and_resolves() -> None:
    getter = moved(
        "pkg.old", {"Old": "showpy.core.errors.ShowValidationError"}, since="0.4.0", remove="0.6.0"
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
    pattern = r"`.*_old_close` is replaced by `showpy\.core\.util\.approx_equal`.*0\.6\.0"
    with pytest.warns(DeprecatedWarning, match=pattern) as caught:
        assert _old_close(1.0, 1.05, slack=0.1) is True
    assert caught[0].filename == __file__
    with pytest.warns(DeprecatedWarning, match=r"replaced by `showpy\.core\.util\.approx_equal`"):
        assert _old_close_by_callable(1.0, 2.0) is False


def test_deprecated_call_keeps_the_wrapped_signature() -> None:
    assert _old_close.__name__ == "_old_close"
    assert _old_close.__doc__ == "Compares two numbers the old way."
    assert list(inspect.signature(_old_close).parameters) == ["a", "b", "slack"]


def test_deprecated_warning_is_a_deprecation_warning() -> None:
    assert issubclass(DeprecatedWarning, DeprecationWarning)
    assert showpy.DeprecatedWarning is DeprecatedWarning
    assert showpy.core.errors.DeprecatedWarning is DeprecatedWarning


def test_validation_error_alias_warns_and_is_the_same_class() -> None:
    with pytest.warns(DeprecatedWarning):
        from_package = showpy.ValidationError
    with pytest.warns(DeprecatedWarning):
        from_errors = showpy.core.errors.ValidationError
    assert from_package is ShowValidationError
    assert from_errors is ShowValidationError
    assert issubclass(ShowValidationError, from_package)
    with pytest.raises(from_package):
        raise ShowValidationError("bad", hint="fix it")


def test_deprecated_names_reach_users_through_showpy() -> None:
    for name, _target, _since, _remove in DEPRECATED:
        with pytest.warns(DeprecatedWarning, match=name):
            getattr(showpy, name)


def test_deprecations_are_removed_on_time() -> None:
    match = VERSION_PREFIX.match(showpy.__version__)
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
    assert not overdue, f"E: deprecated names past their removal version: {', '.join(overdue)}"


def _accepts_both_aliases(
    from_package: "PackageAlias", from_module: "ModuleAlias"
) -> list[ShowValidationError]:
    """Type-checks that both deprecated names are the class `ShowValidationError`."""
    return [from_package, from_module]


def test_deprecated_class_alias_is_visible_to_type_checkers() -> None:
    assert _accepts_both_aliases.__annotations__["from_package"] == "PackageAlias"


def _current_minor() -> tuple[int, int]:
    match = VERSION_PREFIX.match(showpy.__version__)
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
    """Tells whether the scan skips a module, given its path relative to the showpy package."""
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
    ok = 'getter = moved("m", {}, since="0.4.0", remove="99.0.0")\n'
    assert "overdue" in scan_deprecation_calls(overdue, "t.py")[0]
    assert "literal remove=" in scan_deprecation_calls(dynamic, "t.py")[0]
    assert "literal remove=" in scan_deprecation_calls(keyword, "t.py")[0]
    assert (
        "deprecated_call() remove=0.0.1 is overdue" in scan_deprecation_calls(decorator, "t.py")[0]
    )
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
        parameters = inspect.signature(getattr(showpy, function)).parameters
        assert old in parameters, f"{function}() has no keyword {old}"
        assert new in parameters, f"{function}() has no keyword {new}"
