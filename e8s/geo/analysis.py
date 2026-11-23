"""
analysis.py --- deprecated names for the set design feedback that moved to choreo

Every function here warns and forwards to `e8s.choreo.set_analysis`.

Contains:
    density_map(): forwards to e8s.choreo.set_analysis.density_map, deprecated
    coverage(): forwards to e8s.choreo.set_analysis.coverage, deprecated
    balance(): forwards to e8s.choreo.set_analysis.balance, deprecated
    symmetry_score(): forwards to e8s.choreo.set_analysis.symmetry_score, deprecated
    change_amount(): forwards to e8s.choreo.set_analysis.change_amount, deprecated
"""

from typing import Literal

import numpy as np
import numpy.typing as npt

from e8s.core.deprecation import deprecated_call
from e8s.core.types import Meters
from e8s.geo.measure import SET_TYPE, SHEET_TYPE, wrong_type_error


@deprecated_call("e8s.choreo.set_analysis.density_map", since="0.4.0", remove="0.6.0")
def density_map(s: object, sheet: object, cell: Meters = 1.0) -> npt.NDArray[np.int64]:
    """Counts on-field spots per grid cell; moved to `e8s.choreo.set_analysis`."""
    from e8s.choreo.models import Set
    from e8s.choreo.set_analysis import density_map as twin
    from e8s.sheet.models import Sheet

    if not isinstance(s, Set):
        raise wrong_type_error("e8s.geo.analysis.density_map", "s", s, SET_TYPE)
    if not isinstance(sheet, Sheet):
        raise wrong_type_error("e8s.geo.analysis.density_map", "sheet", sheet, SHEET_TYPE)
    return twin(s, sheet, cell)


@deprecated_call("e8s.choreo.set_analysis.coverage", since="0.4.0", remove="0.6.0")
def coverage(s: object, sheet: object, radius: Meters = 1.0) -> float:
    """Returns the covered share of the walkable area; moved to `e8s.choreo.set_analysis`."""
    from e8s.choreo.models import Set
    from e8s.choreo.set_analysis import coverage as twin
    from e8s.sheet.models import Sheet

    if not isinstance(s, Set):
        raise wrong_type_error("e8s.geo.analysis.coverage", "s", s, SET_TYPE)
    if not isinstance(sheet, Sheet):
        raise wrong_type_error("e8s.geo.analysis.coverage", "sheet", sheet, SHEET_TYPE)
    return twin(s, sheet, radius)


@deprecated_call("e8s.choreo.set_analysis.balance", since="0.4.0", remove="0.6.0")
def balance(s: object, sheet: object, axis: Literal["x", "y"] = "x") -> float:
    """Returns the visual weight offset; moved to `e8s.choreo.set_analysis`."""
    from e8s.choreo.models import Set
    from e8s.choreo.set_analysis import balance as twin
    from e8s.sheet.models import Sheet

    if not isinstance(s, Set):
        raise wrong_type_error("e8s.geo.analysis.balance", "s", s, SET_TYPE)
    if not isinstance(sheet, Sheet):
        raise wrong_type_error("e8s.geo.analysis.balance", "sheet", sheet, SHEET_TYPE)
    return twin(s, sheet, axis)


@deprecated_call("e8s.choreo.set_analysis.symmetry_score", since="0.4.0", remove="0.6.0")
def symmetry_score(s: object, axis_x: float = 0.0) -> float:
    """Returns how mirrored the picture is; moved to `e8s.choreo.set_analysis`."""
    from e8s.choreo.models import Set
    from e8s.choreo.set_analysis import symmetry_score as twin

    if not isinstance(s, Set):
        raise wrong_type_error("e8s.geo.analysis.symmetry_score", "s", s, SET_TYPE)
    return twin(s, axis_x)


@deprecated_call("e8s.choreo.set_analysis.change_amount", since="0.4.0", remove="0.6.0")
def change_amount(a: object, b: object) -> Meters:
    """Returns the mean distance spots moved; moved to `e8s.choreo.set_analysis`."""
    from e8s.choreo.models import Set
    from e8s.choreo.set_analysis import change_amount as twin

    if not isinstance(a, Set):
        raise wrong_type_error("e8s.geo.analysis.change_amount", "a", a, SET_TYPE)
    if not isinstance(b, Set):
        raise wrong_type_error("e8s.geo.analysis.change_amount", "b", b, SET_TYPE)
    return twin(a, b)
