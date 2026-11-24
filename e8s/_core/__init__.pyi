"""
__init__.pyi --- typing stub of the compiled extension module e8s._core

Each submodule is importable by dotted path and has its own stub beside this one.

Contains:
    __all__: lists the version and the seven submodules, as the extension does
    __version__: reports the version of the Cargo workspace the extension was built from
    core, geo, time, sheet, cast, engine, forms: the compiled counterparts of the e8s packages
"""

from e8s._core import cast as cast
from e8s._core import core as core
from e8s._core import engine as engine
from e8s._core import forms as forms
from e8s._core import geo as geo
from e8s._core import sheet as sheet
from e8s._core import time as time

__all__ = ["__version__", "cast", "core", "engine", "forms", "geo", "sheet", "time"]

__version__: str
