"""
resources.py --- locates data files that ship inside the installed showpy package

Contains:
    schema_path(): resolves a bundled JSON schema file by name
"""

from importlib.resources import files
from importlib.resources.abc import Traversable

PACKAGE = "showpy"
SCHEMA_DIR = "schemas"


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
