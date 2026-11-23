"""
positions.py --- serves import_positions from its new module, with a warning

Contains:
    __getattr__(): returns import_positions from showpy.show.interchange and warns
"""

from showpy.core.deprecation import moved

__getattr__ = moved(
    __name__,
    {"import_positions": "showpy.show.interchange.import_positions"},
    since="0.4.0",
    remove="0.6.0",
)
