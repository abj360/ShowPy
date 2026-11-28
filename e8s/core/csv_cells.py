"""
csv_cells.py --- neutralizes spreadsheet formula cells in text tables

Contains:
    FORMULA_TRIGGERS: lists the leading characters a spreadsheet treats as a formula
    FORMULA_GUARD: holds the prefix that turns a formula cell into text
    PLAIN_DECIMAL: matches a plain decimal literal, which stays a number
    neutralize_cell(): prefixes a formula-looking cell with a quote
"""

from e8s._core.core import FORMULA_GUARD as FORMULA_GUARD
from e8s._core.core import FORMULA_TRIGGERS as FORMULA_TRIGGERS
from e8s._core.core import PLAIN_DECIMAL as PLAIN_DECIMAL
from e8s._core.core import neutralize_cell as neutralize_cell
