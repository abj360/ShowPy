"""
replace.py --- fits the least-squares similarity between two sets of matched sheet points

Contains:
    similarity_fit(): fits the least-squares similarity between matched points (Umeyama)
"""

import numpy as np

from showpy.core.errors import GeometryError
from showpy.core.types import Points
from showpy.geo.affine import Matrix

MIN_SHARED_LANDMARKS = 2
SPREAD_TOL = 1e-12


def similarity_fit(src: Points, dst: Points) -> Matrix:
    """Fits the rotation, uniform scale and translation that best maps `src` onto `dst`.

    Least squares over matched rows, without reflection (Umeyama 1991).

    Args:
        src: (N, 2) points on the old sheet, N >= 2.
        dst: (N, 2) matching points on the new sheet.

    Returns:
        matrix: 3x3 affine map.

    >>> fit = similarity_fit(np.array([[0.0, 0], [1, 0]]), np.array([[0.0, 1], [2, 1]]))
    >>> fit.round(6).tolist()
    [[2.0, 0.0, 0.0], [0.0, 2.0, 1.0], [0.0, 0.0, 1.0]]
    """
    src, dst = np.asarray(src, dtype=float), np.asarray(dst, dtype=float)
    spread = float(((src - src.mean(axis=0)) ** 2).sum()) / max(len(src), 1)
    if src.shape != dst.shape or len(src) < MIN_SHARED_LANDMARKS or spread < SPREAD_TOL:
        raise GeometryError(
            "similarity fit needs at least two distinct matched points",
            hint="match two or more landmarks that are not on top of each other",
            context={"src": src.tolist(), "dst": dst.tolist()},
        )
    mu_src, mu_dst = src.mean(axis=0), dst.mean(axis=0)
    covariance = (dst - mu_dst).T @ (src - mu_src) / len(src)
    u, singular, vt = np.linalg.svd(covariance)
    keep_handedness = np.diag([1.0, 1.0 if np.linalg.det(u) * np.linalg.det(vt) >= 0 else -1.0])
    rotation = u @ keep_handedness @ vt
    scale = float(np.trace(np.diag(singular) @ keep_handedness)) / spread
    matrix = np.eye(3)
    matrix[:2, :2] = scale * rotation
    matrix[:2, 2] = mu_dst - scale * rotation @ mu_src
    return matrix
