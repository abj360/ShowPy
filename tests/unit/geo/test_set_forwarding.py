#!/usr/bin/env python3

"""
test_set_forwarding.py --- tests that geo transforms refuse anything but points or a curve

Contains:
    test_regression_geo_transform_rejects_a_list_without_a_deprecation_warning: raises SchemaError
        for shift([[1.0, 2.0]], ...) and never warns that a Set is replaced
"""

from typing import cast

import numpy as np
import pytest

from e8s.core.errors import SchemaError
from e8s.geo import transforms


def test_regression_geo_transform_rejects_a_list_without_a_deprecation_warning() -> None:
    not_points = cast(np.ndarray, [[1.0, 2.0]])
    with pytest.raises(SchemaError, match="`obj` must be"):
        transforms.shift(not_points, 1.0, 1.0)
