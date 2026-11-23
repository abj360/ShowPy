"""
positions.py --- serves import_positions from its new module, with a warning

Contains:
    __getattr__(): returns import_positions from e8s.show.interchange and warns
"""

from e8s.core.deprecation import moved

__getattr__ = moved(
    __name__,
    {"import_positions": "e8s.show.interchange.import_positions"},
    since="0.4.0",
    remove="0.6.0",
)
