#!/usr/bin/env python3

"""
test_packaging.py --- tests that the installed distribution exposes its name, version, schema
and bundled fonts
"""

import importlib
import importlib.metadata
import importlib.util
import json
import re
from importlib.resources import files

import showpy
from showpy.core.resources import schema_path
from showpy.geo.fonts import MAX_FONT_BYTES


def test_version_is_exported() -> None:
    assert re.match(r"^\d+\.\d+\.\d+", showpy.__version__)


def test_schema_is_a_package_resource() -> None:
    assert schema_path("showpy.show.v1.json").is_file()


def test_distribution_name_is_showpy() -> None:
    assert importlib.metadata.metadata("ShowPy")["Name"] == "ShowPy"


def test_import_name_is_showpy() -> None:
    assert importlib.import_module("showpy").__name__ == "showpy"
    assert importlib.util.find_spec("stage") is None


BUNDLED = ("simplex", "hershey-roman", "hershey-script", "hershey-greek", "relief-singleline")
LICENCES = ("licenses/OFL-1.1.txt", "licenses/HERSHEY.txt", "licenses/MIT.txt", "NOTICE.txt")


def test_bundled_fonts_ship_with_their_licences() -> None:
    fonts = files("showpy").joinpath("fonts")
    for name in BUNDLED:
        data = fonts.joinpath(f"{name}.json").read_bytes()
        assert len(data) < MAX_FONT_BYTES
        font = json.loads(data)
        assert font["format"] == "showpy.font.v1"
        assert font["license"] in {"MIT", "OFL-1.1", "LicenseRef-Hershey-Fonts"}
    assert all(fonts.joinpath(path).is_file() for path in LICENCES)
