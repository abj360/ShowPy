"""
csv_cells.py --- neutralizes spreadsheet formula cells in text tables

Contains:
    FORMULA_TRIGGERS: lists the leading characters a spreadsheet treats as a formula
    FORMULA_GUARD: holds the prefix that turns a formula cell into text
    PLAIN_DECIMAL: matches a plain decimal literal, which stays a number
    neutralize_cell(): prefixes a formula-looking cell with a quote
"""

import re

FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
FORMULA_GUARD = "'"
PLAIN_DECIMAL = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", re.ASCII)


def neutralize_cell(cell: str) -> str:
    """Prefixes a cell that starts like a formula and is not a plain decimal with a quote.

    Args:
        cell: Text of one CSV cell.

    Returns:
        safe_cell: The cell unchanged, or the cell behind `'` so spreadsheets show it as text.

    >>> neutralize_cell("=1+1"), neutralize_cell("-5"), neutralize_cell("cue")
    ("'=1+1", '-5', 'cue')
    """
    if not cell.startswith(FORMULA_TRIGGERS) or PLAIN_DECIMAL.fullmatch(cell):
        return cell
    return FORMULA_GUARD + cell
