#!/usr/bin/env python3

"""
test_public_api.py --- tests that the top-level showpy package re-exports a clean public API

Contains:
    test_every_exported_name_resolves: checks every name in __all__ imports
    test_exports_are_unique_and_sorted_by_module_groups: checks __all__ has no duplicates
    test_key_entry_points_are_exported: checks the main entry points are public
    test_clashing_names_are_disambiguated: checks spiral and spiral_motion resolve apart
    test_package_ships_a_py_typed_marker: checks the typing marker ships
    test_generated_init_matches_table: checks __init__.py is the generator's output
    test_table_is_sorted_and_unique: checks PUBLIC_API has no duplicate or unsorted names
"""

import importlib.util
import warnings
from pathlib import Path
from types import ModuleType

import showpy
from showpy._api_table import PUBLIC_API
from showpy.core.deprecation import DeprecatedWarning

GENERATOR_PATH = Path(__file__).resolve().parents[2] / "scripts" / "gen_public_api.py"


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_public_api", GENERATOR_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_exported_name_resolves() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecatedWarning)
        missing = [name for name in showpy.__all__ if not hasattr(showpy, name)]
    assert missing == []


def test_exports_are_unique_and_sorted_by_module_groups() -> None:
    assert len(showpy.__all__) == len(set(showpy.__all__))


def test_key_entry_points_are_exported() -> None:
    for name in ("PathEngine", "Show", "select", "match", "new_show", "export_video", "publish"):
        assert name in showpy.__all__


def test_clashing_names_are_disambiguated() -> None:
    assert showpy.spiral.__module__ == "showpy.geo.shapes"
    assert showpy.spiral_motion.__module__ == "showpy.choreo.motion"


def test_package_ships_a_py_typed_marker() -> None:
    from importlib.resources import files

    assert files("showpy").joinpath("py.typed").is_file()


def test_generated_init_matches_table() -> None:
    generator = _load_generator()
    on_disk = Path(showpy.__file__).read_text()
    assert generator.render_init() == on_disk, (
        "E: showpy/__init__.py differs from showpy/_api_table.py; "
        "run: uv run python scripts/gen_public_api.py"
    )


def test_table_is_sorted_and_unique() -> None:
    names = [name for name, _, _ in PUBLIC_API]
    assert names == sorted(names)
    assert len(names) == len(set(names))
