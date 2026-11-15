#!/usr/bin/env python3

"""
test_packaging.py --- tests that the installed distribution exposes its name, version and schema
"""

import importlib
import importlib.metadata
import importlib.util
import re

import showpy
from showpy.core.resources import schema_path


def test_version_is_exported() -> None:
    assert re.match(r"^\d+\.\d+\.\d+", showpy.__version__)


def test_schema_is_a_package_resource() -> None:
    assert schema_path("showpy.show.v1.json").is_file()


def test_distribution_name_is_showpy() -> None:
    assert importlib.metadata.metadata("ShowPy")["Name"] == "ShowPy"


def test_import_name_is_showpy() -> None:
    assert importlib.import_module("showpy").__name__ == "showpy"
    assert importlib.util.find_spec("stage") is None
