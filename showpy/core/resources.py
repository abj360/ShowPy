"""
resources.py --- locates data files that ship inside the installed showpy package

Contains:
    schema_path(): resolves a bundled JSON schema file by name
    font_path(): resolves a bundled font file by name
"""

from importlib.resources import files
from importlib.resources.abc import Traversable

PACKAGE = "showpy"
SCHEMA_DIR = "schemas"
FONT_DIR = "fonts"


def schema_path(name: str) -> Traversable:
    """Resolves a bundled JSON schema file by name.

    Args:
        name: File name of the schema, such as `showpy.show.v1.json`.

    Returns:
        schema_file: Readable handle to the schema inside the installed package.

    Examples:
        >>> schema_path("showpy.show.v1.json").is_file()
        True
    """
    return files(PACKAGE).joinpath(SCHEMA_DIR, name)


def font_path(name: str) -> Traversable:
    """Resolves a bundled font file by name.

    Args:
        name: File name of the font, such as `simplex.json`.

    Returns:
        font_file: Readable handle to the font inside the installed package.

    Examples:
        >>> font_path("simplex.json").is_file()
        True
    """
    return files(PACKAGE).joinpath(FONT_DIR, name)
