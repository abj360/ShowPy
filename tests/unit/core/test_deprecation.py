#!/usr/bin/env python3

"""
test_deprecation.py --- tests the deprecation helpers and the ValidationError alias

Contains:
    test_moved_name_warns_and_resolves: warns once per access and returns the new object
    test_moved_rejects_unknown_names: raises AttributeError for a name outside the table
    test_renamed_field_warns_and_reads_the_new_field: reads the new field through the old name
    test_renamed_field_passes_the_new_value_through_read: serves read(new value) under the old name
    test_deprecated_warning_is_a_deprecation_warning: checks the base class
    test_validation_error_alias_warns_and_is_the_same_class: checks the alias on both paths
    test_deprecated_names_reach_users_through_showpy: resolves every table row with a warning
    test_deprecated_class_alias_is_visible_to_type_checkers: types check both aliases statically
    test_deprecations_are_removed_on_time: fails when a row outlives its removal version
    test_every_deprecation_call_is_removed_on_time: fails when a moved, renamed_field or
        renamed_keyword call in showpy outlives its removal version
    test_deprecation_scan_flags_overdue_and_non_literal_calls: proves the scan can fail
"""

import ast
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import showpy
import showpy.core.errors
from showpy._api_table import DEPRECATED, DEPRECATED_KEYWORDS
from showpy.core.deprecation import DeprecatedWarning, moved, renamed_field
from showpy.core.errors import ShowValidationError

if TYPE_CHECKING:
    from showpy import ValidationError as PackageAlias
    from showpy.core.errors import ValidationError as ModuleAlias

VERSION_PREFIX = re.compile(r"^(\d+)\.(\d+)")
SOURCE_ROOT = Path(showpy.__file__).parent
SCAN_SKIPPED = ("__init__.py", "core/deprecation.py")
REQUIRED_KEYWORDS = {
    "moved": ("since", "remove"),
    "renamed_field": ("since", "remove"),
    "renamed_keyword": ("remove",),
}


def _doubled(value: int) -> int:
    return value * 2


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


def scan_deprecation_calls(source: str, where: str) -> list[str]:
    """Lists the problems in the moved, renamed_field and renamed_keyword calls of one source.

    A keyword must be a string literal or a module-level name bound to one.
    """
    tree = ast.parse(source)
    constants = _string_constants(tree)
    local_defs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    problems: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        name = node.func.id
        if name not in REQUIRED_KEYWORDS or name in local_defs:
            continue
        keywords = {kw.arg: _literal(kw.value, constants) for kw in node.keywords}
        problems.extend(
            f"{where}:{node.lineno}: {name}() needs a literal {required}="
            for required in REQUIRED_KEYWORDS[name]
            if keywords.get(required) is None
        )
        remove = keywords.get("remove")
        if remove is not None:
            major, minor, _patch = remove.split(".")
            if (int(major), int(minor)) <= _current_minor():
                problems.append(f"{where}:{node.lineno}: {name}() remove={remove} is overdue")
    return problems


def test_every_deprecation_call_is_removed_on_time() -> None:
    problems = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        relative = path.relative_to(SOURCE_ROOT).as_posix()
        if relative.endswith(SCAN_SKIPPED):
            continue
        problems += scan_deprecation_calls(path.read_text(), relative)
    assert not problems, "E: " + "; ".join(problems)


def test_deprecation_scan_flags_overdue_and_non_literal_calls() -> None:
    overdue = 'getter = moved("m", {}, since="0.1.0", remove="0.0.1")\n'
    dynamic = 'getter = moved("m", {}, since="0.4.0", remove=LIMIT)\n'
    keyword = 'x = renamed_keyword("a", 1, "b", None, default=0, remove=LIMIT)\n'
    ok = 'getter = moved("m", {}, since="0.4.0", remove="99.0.0")\n'
    assert "overdue" in scan_deprecation_calls(overdue, "t.py")[0]
    assert "literal remove=" in scan_deprecation_calls(dynamic, "t.py")[0]
    assert "literal remove=" in scan_deprecation_calls(keyword, "t.py")[0]
    assert scan_deprecation_calls(ok, "t.py") == []
