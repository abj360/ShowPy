#!/usr/bin/env python3

"""
test_api_snapshot.py --- guards the public API signatures against unannounced change

Contains:
    describe_public_object(): renders one public name as a Python-version-stable JSON value
    diff_snapshots(): lists the names added, removed and changed between two snapshots
    unreleased_section(): extracts the Unreleased section of a changelog
    names_missing_from_changelog(): lists changed names the Unreleased section never mentions
    has_released_section(): tells whether a changelog lists any released version
    check_snapshot(): compares the API with the stored snapshot and refreshes it when allowed
    test_public_api_matches_snapshot: fails on any signature drift and checks the changelog
    test_regression_refresh_refuses_a_change_missing_from_the_changelog: fails and keeps the old
        snapshot when refresh mode meets a changed name that Unreleased never names
    test_refresh_writes_a_change_the_changelog_names: rewrites the snapshot when every changed
        name is in Unreleased
    test_regression_bootstrap_refuses_once_a_release_exists: fails and writes nothing when the
        snapshot is missing and the changelog already lists a release
    test_bootstrap_writes_a_snapshot_before_any_release: writes the first snapshot when the
        changelog has no released section
    test_has_released_section_reads_both_heading_styles: checks the release heading match
    test_diff_snapshots_reports_each_kind_of_change: checks the diff on a synthetic pair
    test_unreleased_section_stops_at_the_next_release: checks the changelog slicing
    test_names_missing_from_changelog_flags_unlisted_names: checks the changelog gate can fail
    test_names_missing_from_changelog_ignores_prose_mentions: checks plain words do not count
    test_snapshot_is_stable_across_python_versions: checks reprs carry no version-only text
"""

import enum
import inspect
import json
import os
import re
import types
from pathlib import Path
from typing import Literal, Union, get_args, get_origin

import pytest
from pydantic import BaseModel

import showpy
from showpy._api_table import PUBLIC_API

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = ROOT / "tests" / "fixtures" / "api_snapshot.json"
CHANGELOG_PATH = ROOT / "CHANGELOG.md"
UPDATE_ENV = "SHOWPY_UPDATE_API"
UPDATE_COMMAND = f"{UPDATE_ENV}=1 uv run pytest tests/unit/test_api_snapshot.py"
NO_SIGNATURE = "(*args)"
BARE_MODULES = ("builtins", "typing")
OPAQUE_DEFAULT = "..."
STABLE_DEFAULT_TYPES = (type(None), bool, int, float, str, bytes, tuple, frozenset, enum.Enum)
UNRELEASED_HEADING = re.compile(r"^## Unreleased\s*$", re.MULTILINE)
NEXT_RELEASE_HEADING = re.compile(r"^## ", re.MULTILINE)
RELEASED_HEADING = re.compile(r"^## \[?\d+\.\d+\.\d+", re.MULTILINE)
RESTORE_COMMAND = "git checkout -- tests/fixtures/api_snapshot.json"

JsonValue = str | dict[str, "JsonValue"]


class _OpaqueDefault:
    """Stands in for a default value whose repr is not stable across runs."""

    def __repr__(self) -> str:
        return OPAQUE_DEFAULT


def _stable_default(default: object) -> object:
    """Keeps literal defaults and replaces object defaults (registries, instances) by `...`."""
    if default is inspect.Parameter.empty or isinstance(default, STABLE_DEFAULT_TYPES):
        return default
    return _OpaqueDefault()


def _rendered(annotation: object) -> object:
    """Replaces an annotation by its version-stable text, keeping `empty` as is."""
    if annotation is inspect.Signature.empty:
        return annotation
    return _Rendered(_annotation_text(annotation))


def _signature_text(obj: object) -> str:
    """Renders a signature without module prefixes or object addresses in defaults."""
    try:
        signature = inspect.signature(obj)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return NO_SIGNATURE
    parameters = [
        p.replace(default=_stable_default(p.default), annotation=_rendered(p.annotation))
        for p in signature.parameters.values()
    ]
    returned = _rendered(signature.return_annotation)
    return str(signature.replace(parameters=parameters, return_annotation=returned))


def _annotation_text(annotation: object) -> str:
    """Renders an annotation identically on every Python version (Union vs `|`, typing prefixes)."""
    if annotation is None or annotation is type(None):
        return "None"
    if annotation is Ellipsis:
        return "..."
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, types.UnionType):
        return " | ".join(_annotation_text(arg) for arg in args)
    if origin is Literal:
        return f"Literal[{', '.join(repr(arg) for arg in args)}]"
    if origin is not None and isinstance(origin, type):
        inner = ", ".join(_annotation_text(arg) for arg in args)
        return f"{_qualified_name(origin)}[{inner}]"
    if isinstance(annotation, list):
        return f"[{', '.join(_annotation_text(arg) for arg in annotation)}]"
    if isinstance(annotation, type):
        return _qualified_name(annotation)
    return repr(annotation).replace("typing.", "")


def _qualified_name(cls: type) -> str:
    """Returns the dotted name of a class.

    Builtins and typing stay bare, and private module parts are dropped: Python 3.13 moved
    pathlib.Path to pathlib._local.
    """
    if cls.__module__ in BARE_MODULES:
        return cls.__qualname__
    public_parts = [part for part in cls.__module__.split(".") if not part.startswith("_")]
    return ".".join([*public_parts, cls.__qualname__])


class _Rendered:
    """Wraps text so inspect prints it verbatim as an annotation."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:
        return self.text


def _protocol_members(cls: type) -> dict[str, JsonValue]:
    """Lists the public methods a Protocol declares, with their signatures."""
    return {
        name: _signature_text(member)
        for name, member in sorted(vars(cls).items())
        if not name.startswith("_") and callable(member)
    }


def describe_public_object(obj: object) -> JsonValue:
    """Renders one public name as a Python-version-stable JSON value.

    Args:
        obj: The object re-exported by showpy.

    Returns:
        description: A signature string, or a dict describing a model, enum, protocol or constant.
    """
    if isinstance(obj, type) and issubclass(obj, BaseModel):
        fields = {name: _annotation_text(f.annotation) for name, f in obj.model_fields.items()}
        return {"model": dict(sorted(fields.items()))}
    if isinstance(obj, type) and issubclass(obj, enum.Enum):
        return {"enum": {member.name: repr(member.value) for member in obj}}
    if isinstance(obj, type) and getattr(obj, "_is_protocol", False) is True:
        return {"protocol": _protocol_members(obj)}
    if callable(obj):
        return _signature_text(obj)
    if isinstance(obj, frozenset | set):
        return {"constant": repr(sorted(obj, key=repr))}
    return {"constant": repr(obj)}


def build_snapshot() -> dict[str, JsonValue]:
    """Builds the snapshot of every public name, sorted by name."""
    return {name: describe_public_object(getattr(showpy, name)) for name, _, _ in PUBLIC_API}


def diff_snapshots(old: dict[str, JsonValue], new: dict[str, JsonValue]) -> dict[str, list[str]]:
    """Lists the names added, removed and changed between two snapshots.

    Args:
        old: The stored snapshot.
        new: The snapshot of the current code.

    Returns:
        diff: Sorted name lists under the keys "added", "removed" and "changed".
    """
    return {
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "changed": sorted(name for name in old.keys() & new.keys() if old[name] != new[name]),
    }


def unreleased_section(changelog: str) -> str:
    """Extracts the Unreleased section of a changelog, or an empty string when it has none."""
    start = UNRELEASED_HEADING.search(changelog)
    if start is None:
        return ""
    rest = changelog[start.end() :]
    following = NEXT_RELEASE_HEADING.search(rest)
    return rest if following is None else rest[: following.start()]


def names_missing_from_changelog(names: list[str], changelog: str) -> list[str]:
    """Lists the names the Unreleased section never mentions as a backticked identifier."""
    section = unreleased_section(changelog)
    return [name for name in names if f"`{name}`" not in section]


def has_released_section(changelog: str) -> bool:
    """Tells whether a changelog lists any released version, as `## [x.y.z]` or `## x.y.z`."""
    return RELEASED_HEADING.search(changelog) is not None


def _format_failure(diff: dict[str, list[str]], unlisted: list[str]) -> str:
    """Formats the failure message with each changed name and the fix commands."""
    lines = ["E: public API differs from tests/fixtures/api_snapshot.json"]
    lines += [f"  {kind}: {', '.join(names)}" for kind, names in diff.items() if names]
    if unlisted:
        lines.append(f"  not in CHANGELOG.md Unreleased: {', '.join(unlisted)}")
    lines.append(f"Fix: add the names to CHANGELOG.md under Unreleased, then run {UPDATE_COMMAND}")
    return "\n".join(lines)


def _write_snapshot(snapshot: dict[str, JsonValue], path: Path) -> None:
    """Writes the snapshot as sorted, indented JSON with a trailing newline."""
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")


def check_snapshot(
    current: dict[str, JsonValue], snapshot_path: Path, changelog_path: Path, *, is_updating: bool
) -> str | None:
    """Compares the API with the stored snapshot, rewriting it in refresh mode when allowed.

    Args:
        current: The snapshot of the current code.
        snapshot_path: Where the stored snapshot lives.
        changelog_path: The changelog whose Unreleased section must name every changed name.
        is_updating: Whether refresh mode (`SHOWPY_UPDATE_API=1`) is on.

    Returns:
        failure: The failure message, or None when the API matches or the refresh succeeded.
    """
    if not snapshot_path.exists() and is_updating:
        if changelog_path.exists() and has_released_section(changelog_path.read_text()):
            return (
                f"E: {snapshot_path.name} is missing but {changelog_path.name} lists a release; "
                f"a fresh snapshot would skip the changelog check\n"
                f"Fix: run {RESTORE_COMMAND}, then {UPDATE_COMMAND}"
            )
        _write_snapshot(current, snapshot_path)
        return None
    if not snapshot_path.exists():
        return f"E: {snapshot_path.name} is missing; run {UPDATE_COMMAND}"
    stored: dict[str, JsonValue] = json.loads(snapshot_path.read_text())
    diff = diff_snapshots(stored, current)
    changed = [name for names in diff.values() for name in names]
    if not changed:
        return None
    if not changelog_path.exists():
        return (
            f"E: public API drifted and {changelog_path.name} is absent; "
            f"run from a source checkout: {UPDATE_COMMAND}"
        )
    unlisted = names_missing_from_changelog(changed, changelog_path.read_text())
    if unlisted or not is_updating:
        return _format_failure(diff, unlisted)
    _write_snapshot(current, snapshot_path)
    return None


def test_public_api_matches_snapshot() -> None:
    is_updating = os.environ.get(UPDATE_ENV) == "1"
    failure = check_snapshot(
        build_snapshot(), SNAPSHOT_PATH, CHANGELOG_PATH, is_updating=is_updating
    )
    if failure is not None:
        pytest.fail(failure, pytrace=False)


def test_diff_snapshots_reports_each_kind_of_change() -> None:
    old: dict[str, JsonValue] = {"keep": "(a)", "drop": "(a)", "edit": "(a)"}
    new: dict[str, JsonValue] = {"keep": "(a)", "edit": "(a, b)", "add": "()"}
    assert diff_snapshots(old, new) == {"added": ["add"], "removed": ["drop"], "changed": ["edit"]}


def test_unreleased_section_stops_at_the_next_release() -> None:
    changelog = "# Changelog\n\n## Unreleased\n\n- `alpha`\n\n## [0.1.1] - 2026-01-01\n\n- `beta`\n"
    assert "alpha" in unreleased_section(changelog)
    assert "beta" not in unreleased_section(changelog)
    assert unreleased_section("# Changelog\n") == ""


def test_names_missing_from_changelog_flags_unlisted_names() -> None:
    changelog = "## Unreleased\n\n- `alpha` renamed\n\n## [0.1.1] - 2026-01-01\n\n- `beta`\n"
    assert names_missing_from_changelog(["alpha", "beta", "alph"], changelog) == ["beta", "alph"]


def test_names_missing_from_changelog_ignores_prose_mentions() -> None:
    changelog = "## Unreleased\n\n- select the best option from the list\n"
    assert names_missing_from_changelog(["select", "list"], changelog) == ["select", "list"]


def test_snapshot_is_stable_across_python_versions() -> None:
    forbidden = ("typing.", "<class", " at 0x")
    offenders = [
        name
        for name, value in build_snapshot().items()
        if any(token in json.dumps(value) for token in forbidden)
    ]
    assert offenders == []


STORED_API: dict[str, JsonValue] = {"alpha": "(a)"}
CHANGED_API: dict[str, JsonValue] = {"alpha": "(a, b)", "beta": "()"}


def _api_files(tmp_path: Path, changelog: str) -> tuple[Path, Path]:
    snapshot_path = tmp_path / "api_snapshot.json"
    changelog_path = tmp_path / "CHANGELOG.md"
    _write_snapshot(STORED_API, snapshot_path)
    changelog_path.write_text(changelog)
    return snapshot_path, changelog_path


def test_regression_refresh_refuses_a_change_missing_from_the_changelog(tmp_path: Path) -> None:
    snapshot_path, changelog_path = _api_files(tmp_path, "## Unreleased\n\n- `alpha` grows b\n")
    before = snapshot_path.read_text()

    failure = check_snapshot(CHANGED_API, snapshot_path, changelog_path, is_updating=True)

    assert failure is not None
    assert "not in CHANGELOG.md Unreleased: beta" in failure
    assert snapshot_path.read_text() == before


def test_refresh_writes_a_change_the_changelog_names(tmp_path: Path) -> None:
    changelog = "## Unreleased\n\n- `alpha` grows b\n- add `beta`\n"
    snapshot_path, changelog_path = _api_files(tmp_path, changelog)

    assert check_snapshot(CHANGED_API, snapshot_path, changelog_path, is_updating=True) is None
    assert json.loads(snapshot_path.read_text()) == CHANGED_API


def test_regression_bootstrap_refuses_once_a_release_exists(tmp_path: Path) -> None:
    changelog = "## Unreleased\n\n- `alpha` grows b\n\n## [0.2.0] - 2026-11-15\n\n- `alpha`\n"
    snapshot_path, changelog_path = _api_files(tmp_path, changelog)
    snapshot_path.unlink()

    failure = check_snapshot(CHANGED_API, snapshot_path, changelog_path, is_updating=True)

    assert failure is not None
    assert RESTORE_COMMAND in failure
    assert not snapshot_path.exists()


def test_bootstrap_writes_a_snapshot_before_any_release(tmp_path: Path) -> None:
    snapshot_path, changelog_path = _api_files(tmp_path, "## Unreleased\n\n- first names\n")
    snapshot_path.unlink()

    assert check_snapshot(CHANGED_API, snapshot_path, changelog_path, is_updating=True) is None
    assert json.loads(snapshot_path.read_text()) == CHANGED_API


def test_has_released_section_reads_both_heading_styles() -> None:
    assert has_released_section("## Unreleased\n\n## [0.2.0] - 2026-11-15\n")
    assert has_released_section("## Unreleased\n\n## 0.2.0\n")
    assert not has_released_section("## Unreleased\n\n- fixed 1.2.3 parsing\n")
