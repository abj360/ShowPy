#!/usr/bin/env python3

"""
test_public_api.py --- tests that the top-level stage package re-exports a clean public API
"""

import stage


def test_every_exported_name_resolves() -> None:
    missing = [name for name in stage.__all__ if not hasattr(stage, name)]
    assert missing == []


def test_exports_are_unique_and_sorted_by_module_groups() -> None:
    assert len(stage.__all__) == len(set(stage.__all__))


def test_key_entry_points_are_exported() -> None:
    for name in ("PathEngine", "Show", "select", "match", "new_show", "export_video", "publish"):
        assert name in stage.__all__


def test_clashing_names_are_disambiguated() -> None:
    assert stage.spiral.__module__ == "stage.geo.shapes"
    assert stage.spiral_motion.__module__ == "stage.choreo.motion"
