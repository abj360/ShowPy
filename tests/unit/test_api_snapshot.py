#!/usr/bin/env python3

"""
test_api_snapshot.py --- guards the public API signatures against unannounced change

A commit that changes the snapshot must name each changed name in its subject or a
BREAKING CHANGE footer; `nox -s api-changes` checks that across the commits since the last
release.

Contains:
    describe_public_object(): renders one public name as a Python-version-stable JSON value
    build_snapshot(): builds the snapshot of every public name, deprecated alias and leaving name
    inline_short_arrays(): puts each JSON array on one line when it fits, as Prettier does
    check_snapshot(): compares the API with the stored snapshot and refreshes it when allowed
    test_public_api_matches_snapshot: fails on any signature drift from the stored snapshot
    test_snapshot_drift_fails_and_keeps_the_stored_snapshot: names each drifted name and the
        refresh command, and writes nothing outside refresh mode
    test_refresh_writes_the_changed_snapshot: rewrites the snapshot in refresh mode
    test_missing_snapshot_fails_outside_refresh_mode: fails and writes nothing
    test_refresh_writes_a_missing_snapshot: writes the first snapshot in refresh mode
    test_diff_snapshots_reports_each_kind_of_change: checks the diff on a synthetic pair
    test_regression_snapshot_renders_an_annotated_type_without_its_validators: shows a field
        type such as `SpotIdField` as its base type, with no validator address
    test_snapshot_is_stable_across_python_versions: checks reprs carry no version-only text
    test_regression_snapshot_tells_a_frozenset_constant_from_a_list: renders a set constant with
        its type, so a frozenset turned list shows up as a change
    test_inline_short_arrays_matches_prettier: inlines an array that fits, keeps a long one
"""

import enum
import inspect
import json
import os
import types
import warnings
from pathlib import Path
from typing import Annotated, Literal, Union, get_args, get_origin

import pytest
from pydantic import BaseModel

import showpy
from scripts.check_api_changes import DEPRECATED_KEY, LEAVING_KEY, JsonValue, diff_snapshots
from showpy._api_table import DEPRECATED, LEAVING, PUBLIC_API
from showpy.core.deprecation import DeprecatedWarning

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = ROOT / "tests" / "fixtures" / "api_snapshot.json"
UPDATE_ENV = "SHOWPY_UPDATE_API"
UPDATE_COMMAND = f"{UPDATE_ENV}=1 uv run pytest tests/unit/test_api_snapshot.py"
NO_SIGNATURE = "(*args)"
BARE_MODULES = ("builtins", "typing")
OPAQUE_DEFAULT = "..."
STABLE_DEFAULT_TYPES = (type(None), bool, int, float, str, bytes, tuple, frozenset, enum.Enum)
PRETTIER_PRINT_WIDTH = 100


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
    if origin is Annotated:
        return _annotation_text(args[0])
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
        return {"constant": f"{type(obj).__name__}({sorted(obj, key=repr)!r})"}
    return {"constant": repr(obj)}


def _leaving_entry(
    kind: str, replacement: str | tuple[str, ...] | None, since: str, remove: str
) -> dict[str, JsonValue]:
    """Describes one leaving name; the replacement key is absent when the name has none."""
    entry: dict[str, JsonValue] = {"kind": kind, "since": since, "remove": remove}
    if isinstance(replacement, str):
        entry["replacement"] = replacement
    elif replacement is not None:
        entry["replacement"] = list(replacement)
    return entry


def build_snapshot() -> dict[str, JsonValue]:
    """Builds the snapshot of every public name, plus its deprecated aliases and leaving names."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecatedWarning)
        snapshot = {
            name: describe_public_object(getattr(showpy, name)) for name, _, _ in PUBLIC_API
        }
    snapshot[DEPRECATED_KEY] = {
        name: {"target": target, "since": since, "remove": remove}
        for name, target, since, remove in DEPRECATED
    }
    snapshot[LEAVING_KEY] = {
        name: _leaving_entry(kind, replacement, since, remove)
        for name, kind, replacement, since, remove in LEAVING
    }
    return snapshot


def _format_failure(diff: dict[str, list[str]]) -> str:
    """Formats the failure message with each changed name and the refresh command."""
    lines = ["E: public API differs from tests/fixtures/api_snapshot.json"]
    lines += [f"  {kind}: {', '.join(names)}" for kind, names in diff.items() if names]
    lines.append(f"Fix: run {UPDATE_COMMAND} and commit the snapshot with the change")
    return "\n".join(lines)


def inline_short_arrays(text: str) -> str:
    """Puts each array of indented JSON on one line when the line fits, as Prettier formats it.

    Args:
        text: JSON written by `json.dumps` with an indent.

    Returns:
        formatted: The same JSON, each array that fits the print width on one line.
    """
    lines = text.split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        end = index + 1
        if line.endswith("["):
            while not lines[end].strip().startswith("]"):
                end += 1
            items = ", ".join(item.strip().rstrip(",") for item in lines[index + 1 : end])
            joined = f"{line}{items}{lines[end].strip()}"
            if len(joined) <= PRETTIER_PRINT_WIDTH:
                out.append(joined)
                index = end + 1
                continue
            end = index + 1
        out.append(line)
        index = end
    return "\n".join(out)


def _write_snapshot(snapshot: dict[str, JsonValue], path: Path) -> None:
    """Writes the snapshot as sorted, indented JSON with a trailing newline."""
    path.write_text(inline_short_arrays(json.dumps(snapshot, indent=2, sort_keys=True)) + "\n")


def check_snapshot(
    current: dict[str, JsonValue], snapshot_path: Path, *, is_updating: bool
) -> str | None:
    """Compares the API with the stored snapshot, rewriting it in refresh mode.

    Args:
        current: The snapshot of the current code.
        snapshot_path: Where the stored snapshot lives.
        is_updating: Whether refresh mode (`SHOWPY_UPDATE_API=1`) is on.

    Returns:
        failure: The failure message, or None when the API matches or the refresh succeeded.
    """
    if is_updating:
        _write_snapshot(current, snapshot_path)
        return None
    if not snapshot_path.exists():
        return f"E: {snapshot_path.name} is missing; run {UPDATE_COMMAND}"
    stored: dict[str, JsonValue] = json.loads(snapshot_path.read_text())
    diff = diff_snapshots(stored, current)
    if not any(diff.values()):
        return None
    return _format_failure(diff)


def test_public_api_matches_snapshot() -> None:
    is_updating = os.environ.get(UPDATE_ENV) == "1"
    failure = check_snapshot(build_snapshot(), SNAPSHOT_PATH, is_updating=is_updating)
    if failure is not None:
        pytest.fail(failure, pytrace=False)


def test_diff_snapshots_reports_each_kind_of_change() -> None:
    old: dict[str, JsonValue] = {"keep": "(a)", "drop": "(a)", "edit": "(a)"}
    new: dict[str, JsonValue] = {"keep": "(a)", "edit": "(a, b)", "add": "()"}
    assert diff_snapshots(old, new) == {"added": ["add"], "removed": ["drop"], "changed": ["edit"]}


def test_regression_snapshot_renders_an_annotated_type_without_its_validators() -> None:
    assert _annotation_text(Annotated[str, object()]) == "str"
    assert "object" not in _annotation_text(tuple[Annotated[str, object()], ...])


def test_snapshot_is_stable_across_python_versions() -> None:
    forbidden = ("typing.", "<class", " at 0x")
    offenders = [
        name
        for name, value in build_snapshot().items()
        if any(token in json.dumps(value) for token in forbidden)
    ]
    assert offenders == []


def test_regression_snapshot_tells_a_frozenset_constant_from_a_list() -> None:
    as_set = describe_public_object(frozenset({"b", "a"}))
    assert as_set == {"constant": "frozenset(['a', 'b'])"}
    assert as_set != describe_public_object(["a", "b"])
    assert describe_public_object({"b", "a"}) == {"constant": "set(['a', 'b'])"}


STORED_API: dict[str, JsonValue] = {"alpha": "(a)"}
CHANGED_API: dict[str, JsonValue] = {"alpha": "(a, b)", "beta": "()"}


def _stored_api(tmp_path: Path) -> Path:
    snapshot_path = tmp_path / "api_snapshot.json"
    _write_snapshot(STORED_API, snapshot_path)
    return snapshot_path


def test_snapshot_drift_fails_and_keeps_the_stored_snapshot(tmp_path: Path) -> None:
    snapshot_path = _stored_api(tmp_path)
    before = snapshot_path.read_text()

    failure = check_snapshot(CHANGED_API, snapshot_path, is_updating=False)

    assert failure is not None
    assert "added: beta" in failure
    assert "changed: alpha" in failure
    assert UPDATE_COMMAND in failure
    assert snapshot_path.read_text() == before


def test_refresh_writes_the_changed_snapshot(tmp_path: Path) -> None:
    snapshot_path = _stored_api(tmp_path)

    assert check_snapshot(CHANGED_API, snapshot_path, is_updating=True) is None
    assert json.loads(snapshot_path.read_text()) == CHANGED_API


def test_missing_snapshot_fails_outside_refresh_mode(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "api_snapshot.json"

    failure = check_snapshot(CHANGED_API, snapshot_path, is_updating=False)

    assert failure is not None
    assert UPDATE_COMMAND in failure
    assert not snapshot_path.exists()


def test_refresh_writes_a_missing_snapshot(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "api_snapshot.json"

    assert check_snapshot(CHANGED_API, snapshot_path, is_updating=True) is None
    assert json.loads(snapshot_path.read_text()) == CHANGED_API


def test_inline_short_arrays_matches_prettier() -> None:
    long_item = "x" * PRETTIER_PRINT_WIDTH
    text = json.dumps({"a": {"short": ["b", "c"], "long": [long_item]}}, indent=2)
    assert inline_short_arrays(text).split("\n") == [
        "{",
        '  "a": {',
        '    "short": ["b", "c"],',
        '    "long": [',
        f'      "{long_item}"',
        "    ]",
        "  }",
        "}",
    ]
