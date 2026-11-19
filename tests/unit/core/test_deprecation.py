#!/usr/bin/env python3

"""
test_deprecation.py --- tests the deprecation helpers and the ValidationError alias

Contains:
    test_moved_name_warns_and_resolves: warns once per access and returns the new object
    test_moved_rejects_unknown_names: raises AttributeError for a name outside the table
    test_renamed_field_warns_and_reads_the_new_field: reads the new field through the old name
    test_deprecated_warning_is_a_deprecation_warning: checks the base class
    test_validation_error_alias_warns_and_is_the_same_class: checks the alias on both paths
    test_deprecated_names_reach_users_through_showpy: resolves every table row with a warning
    test_deprecated_class_alias_is_visible_to_type_checkers: types check both aliases statically
    test_deprecations_are_removed_on_time: fails when a row outlives its removal version
"""

import re
from typing import TYPE_CHECKING

import pytest

import showpy
import showpy.core.errors
from showpy._api_table import DEPRECATED
from showpy.core.deprecation import DeprecatedWarning, moved, renamed_field
from showpy.core.errors import ShowValidationError

if TYPE_CHECKING:
    from showpy import ValidationError as PackageAlias
    from showpy.core.errors import ValidationError as ModuleAlias

VERSION_PREFIX = re.compile(r"^(\d+)\.(\d+)")


class _Holder:
    value = 7
    legacy = renamed_field("value", since="0.4.0", remove="0.6.0")


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
    assert not overdue, f"E: deprecated names past their removal version: {', '.join(overdue)}"


def _accepts_both_aliases(
    from_package: "PackageAlias", from_module: "ModuleAlias"
) -> list[ShowValidationError]:
    """Type-checks that both deprecated names are the class `ShowValidationError`."""
    return [from_package, from_module]


def test_deprecated_class_alias_is_visible_to_type_checkers() -> None:
    assert _accepts_both_aliases.__annotations__["from_package"] == "PackageAlias"
