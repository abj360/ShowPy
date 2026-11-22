"""
analysis.py --- deprecated names for the set design feedback that moved to choreo

Every function here warns and forwards to `showpy.choreo.set_analysis`.

Contains:
    density_map(): forwards to showpy.choreo.set_analysis.density_map, deprecated
    coverage(): forwards to showpy.choreo.set_analysis.coverage, deprecated
    balance(): forwards to showpy.choreo.set_analysis.balance, deprecated
    symmetry_score(): forwards to showpy.choreo.set_analysis.symmetry_score, deprecated
    change_amount(): forwards to showpy.choreo.set_analysis.change_amount, deprecated
"""

from typing import Literal

import numpy as np
import numpy.typing as npt

from showpy.core.deprecation import deprecated_call
from showpy.core.types import Meters
from showpy.geo.measure import SET_TYPE, SHEET_TYPE, wrong_type_error


@deprecated_call("showpy.choreo.set_analysis.density_map", since="0.4.0", remove="0.6.0")
def density_map(s: object, sheet: object, cell: Meters = 1.0) -> npt.NDArray[np.int64]:
    """Counts on-field spots per grid cell; moved to `showpy.choreo.set_analysis`."""
    from showpy.choreo.models import Set
    from showpy.choreo.set_analysis import density_map as twin
    from showpy.sheet.models import Sheet

    if not isinstance(s, Set):
        raise wrong_type_error("showpy.geo.analysis.density_map", "s", s, SET_TYPE)
    if not isinstance(sheet, Sheet):
        raise wrong_type_error("showpy.geo.analysis.density_map", "sheet", sheet, SHEET_TYPE)
    return twin(s, sheet, cell)


@deprecated_call("showpy.choreo.set_analysis.coverage", since="0.4.0", remove="0.6.0")
def coverage(s: object, sheet: object, radius: Meters = 1.0) -> float:
    """Returns the covered share of the walkable area; moved to `showpy.choreo.set_analysis`."""
    from showpy.choreo.models import Set
    from showpy.choreo.set_analysis import coverage as twin
    from showpy.sheet.models import Sheet

    if not isinstance(s, Set):
        raise wrong_type_error("showpy.geo.analysis.coverage", "s", s, SET_TYPE)
    if not isinstance(sheet, Sheet):
        raise wrong_type_error("showpy.geo.analysis.coverage", "sheet", sheet, SHEET_TYPE)
    return twin(s, sheet, radius)


@deprecated_call("showpy.choreo.set_analysis.balance", since="0.4.0", remove="0.6.0")
def balance(s: object, sheet: object, axis: Literal["x", "y"] = "x") -> float:
    """Returns the visual weight offset; moved to `showpy.choreo.set_analysis`."""
    from showpy.choreo.models import Set
    from showpy.choreo.set_analysis import balance as twin
    from showpy.sheet.models import Sheet

    if not isinstance(s, Set):
        raise wrong_type_error("showpy.geo.analysis.balance", "s", s, SET_TYPE)
    if not isinstance(sheet, Sheet):
        raise wrong_type_error("showpy.geo.analysis.balance", "sheet", sheet, SHEET_TYPE)
    return twin(s, sheet, axis)


@deprecated_call("showpy.choreo.set_analysis.symmetry_score", since="0.4.0", remove="0.6.0")
def symmetry_score(s: object, axis_x: float = 0.0) -> float:
    """Returns how mirrored the picture is; moved to `showpy.choreo.set_analysis`."""
    from showpy.choreo.models import Set
    from showpy.choreo.set_analysis import symmetry_score as twin

    if not isinstance(s, Set):
        raise wrong_type_error("showpy.geo.analysis.symmetry_score", "s", s, SET_TYPE)
    return twin(s, axis_x)


@deprecated_call("showpy.choreo.set_analysis.change_amount", since="0.4.0", remove="0.6.0")
def change_amount(a: object, b: object) -> Meters:
    """Returns the mean distance spots moved; moved to `showpy.choreo.set_analysis`."""
    from showpy.choreo.models import Set
    from showpy.choreo.set_analysis import change_amount as twin

    if not isinstance(a, Set):
        raise wrong_type_error("showpy.geo.analysis.change_amount", "a", a, SET_TYPE)
    if not isinstance(b, Set):
        raise wrong_type_error("showpy.geo.analysis.change_amount", "b", b, SET_TYPE)
    return twin(a, b)
