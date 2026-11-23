#!/usr/bin/env python3

"""
recipes.py --- small valid form recipes for tests that need a form but not its shape

Contains:
    LINE_RECIPE: a line recipe along a 4 m segment
    ARC_RECIPE: an arc recipe of a quarter circle
"""

from e8s.choreo.recipe_models import ArcRecipe, LineRecipe
from e8s.geo.shapes import arc, line

LINE_RECIPE = LineRecipe(curve=line((0.0, 0.0), (4.0, 0.0)))
ARC_RECIPE = ArcRecipe(curve=arc((0.0, 0.0), 4.0, 0.0, 90.0))
