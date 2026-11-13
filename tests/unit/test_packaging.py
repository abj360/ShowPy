#!/usr/bin/env python3

"""
test_packaging.py --- tests that the installed distribution exposes its name, version and schema
"""

import importlib.metadata
import re

import stage
from stage.core.resources import schema_path


def test_version_is_exported() -> None:
    assert re.match(r"^\d+\.\d+\.\d+", stage.__version__)


def test_schema_is_a_package_resource() -> None:
    assert schema_path("stage.show.v1.json").is_file()


def test_distribution_name_is_showpy() -> None:
    assert importlib.metadata.metadata("ShowPy")["Name"] == "ShowPy"
