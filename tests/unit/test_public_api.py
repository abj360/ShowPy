#!/usr/bin/env python3

"""
test_public_api.py --- tests that the top-level showpy package re-exports a clean public API
"""

import showpy


def test_every_exported_name_resolves() -> None:
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
