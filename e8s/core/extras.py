"""
extras.py --- imports the optional dependencies of extra-gated functions on first call

pandas is installed only by the `tables` extra. A function that needs it asks for it here when
called, so importing any e8s module never loads it and a missing extra fails with an install
command, not an ImportError.

Contains:
    MissingExtraError: reports a call to a function whose extra is not installed
    require_extra(): imports an optional module or raises MissingExtraError naming its extra
"""

import importlib
from types import ModuleType

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import E8sError


class MissingExtraError(E8sError):
    """Reports a call to a function whose optional dependencies are not installed."""

    default_code = ErrorCode.MISSING_EXTRA


def require_extra(module: str, *, extra: str, needed_by: str) -> ModuleType:
    """Imports an optional module, or raises MissingExtraError naming the extra that installs it.

    It keeps no cache of its own: `sys.modules` holds the imported module, so later calls cost one
    dictionary lookup.

    Args:
        module: Dotted name of the optional module to import.
        extra: The e8s extra that installs the module.
        needed_by: The public function or output that needs the module, named in the message.

    Returns:
        imported: The imported module.

    >>> require_extra("pandas", extra="tables", needed_by="speeds").__name__
    'pandas'
    >>> try:
    ...     require_extra("not_installed_x", extra="tables", needed_by="speeds")
    ... except MissingExtraError as err:
    ...     print(err.hint)
    install the extra: pip install "e8s[tables]" or uv add "e8s[tables]"
    """
    try:
        return importlib.import_module(module)
    except ImportError as err:
        raise MissingExtraError(
            f"{needed_by} needs the {extra} extra ({module} is not installed)",
            hint=f'install the extra: pip install "e8s[{extra}]" or uv add "e8s[{extra}]"',
            context={"module": module, "extra": extra, "needed_by": needed_by, "reason": str(err)},
        ) from err
