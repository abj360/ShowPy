"""
extras.py --- imports the optional dependencies of extra-gated functions on first call

pandas, Pillow, segno and imageio-ffmpeg are installed only by the `tables`, `render`, `qr` and
`video` extras. A function that needs one asks for it here when called, so importing any showpy
module never loads them and a missing extra fails with an install command, not an ImportError.

Contains:
    MissingExtraError: reports a call to a function whose extra is not installed
    require_extra(): imports an optional module or raises MissingExtraError naming its extra
    is_dataframe(): tells whether an object is a pandas DataFrame without importing pandas
"""

import importlib
import sys
from types import ModuleType
from typing import TYPE_CHECKING, TypeIs

from showpy.core.error_codes import ErrorCode
from showpy.core.errors import ShowPyError

if TYPE_CHECKING:
    import pandas as pd

PANDAS_MODULE = "pandas"


class MissingExtraError(ShowPyError):
    """Reports a call to a function whose optional dependencies are not installed."""

    default_code = ErrorCode.MISSING_EXTRA


def require_extra(module: str, *, extra: str, needed_by: str) -> ModuleType:
    """Imports an optional module, or raises MissingExtraError naming the extra that installs it.

    It keeps no cache of its own: `sys.modules` holds the imported module, so later calls cost one
    dictionary lookup.

    Args:
        module: Dotted name of the optional module to import.
        extra: The showpy extra that installs the module.
        needed_by: The public function or output that needs the module, named in the message.

    Returns:
        imported: The imported module.

    >>> require_extra("pandas", extra="tables", needed_by="speeds").__name__
    'pandas'
    >>> try:
    ...     require_extra("not_installed_x", extra="tables", needed_by="speeds")
    ... except MissingExtraError as err:
    ...     print(err.hint)
    install the extra: pip install "showpy[tables]" or uv add "showpy[tables]"
    """
    try:
        return importlib.import_module(module)
    except ImportError as err:
        raise MissingExtraError(
            f"{needed_by} needs the {extra} extra ({module} is not installed)",
            hint=f'install the extra: pip install "showpy[{extra}]" or uv add "showpy[{extra}]"',
            context={"module": module, "extra": extra, "needed_by": needed_by, "reason": str(err)},
        ) from err


def is_dataframe(obj: object) -> "TypeIs[pd.DataFrame]":
    """Tells whether an object is a pandas DataFrame, without importing pandas.

    No DataFrame can exist before pandas is imported, so a missing pandas module means no.

    >>> is_dataframe([("x", 1.0)])
    False
    """
    pandas = sys.modules.get(PANDAS_MODULE)
    return pandas is not None and isinstance(obj, pandas.DataFrame)
