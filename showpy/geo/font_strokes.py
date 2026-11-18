"""
font_strokes.py --- samples font stroke path data into points for measuring

Contains:
    sample_stroke(): samples each subpath of one stroke into an array of points
    sample_subpaths(): samples parsed subpaths into arrays of points
    stroke_length(): measures the polyline length of sampled points
"""

from collections.abc import Sequence

import numpy as np

from showpy.core.types import Points
from showpy.geo.svg import Subpath, parse_path

SAMPLES_PER_PIECE = 9


def sample_stroke(d: str) -> list[Points]:
    """Samples each subpath of one stroke into an array of points.

    Args:
        d: SVG path data of the stroke.

    Returns:
        subpaths: One (N, 2) array per subpath, N = 9 per drawn piece; empty for a bare move.

    Examples:
        >>> [len(points) for points in sample_stroke("M0 0 L1 0 L1 1")]
        [18]
    """
    return sample_subpaths(parse_path(d))


def sample_subpaths(subpaths: Sequence[Subpath]) -> list[Points]:
    """Samples parsed subpaths into arrays of points, 9 per drawn piece.

    Args:
        subpaths: Subpaths as `parse_path` returns them.

    Returns:
        subpaths: One (N, 2) array per subpath; empty for a bare move.

    Examples:
        >>> [len(points) for points in sample_subpaths(parse_path("M0 0 L1 0"))]
        [9]
    """
    parameters = np.linspace(0.0, 1.0, SAMPLES_PER_PIECE)
    return [
        np.vstack([piece.evaluate(parameters) for piece in subpath.pieces])
        if subpath.pieces
        else np.empty((0, 2))
        for subpath in subpaths
    ]


def stroke_length(points: Points) -> float:
    """Measures the polyline length of sampled points.

    Args:
        points: An (N, 2) array in drawing order.

    Returns:
        length: Sum of the gaps between consecutive points; 0 for fewer than two points.

    Examples:
        >>> stroke_length(sample_stroke("M0 0 L3 4")[0])
        5.0
    """
    if len(points) < 2:
        return 0.0
    return float(np.hypot(*np.diff(points, axis=0).T).sum())
