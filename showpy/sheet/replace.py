"""
replace.py --- fits the least-squares similarity between two sets of matched sheet points

Contains:
    similarity_fit(): fits the least-squares similarity between matched points (Umeyama),
        refusing reflections and degenerate input
"""

import numpy as np

from showpy.core.errors import GeometryError
from showpy.core.types import Points
from showpy.geo.affine import Matrix

MIN_SHARED_LANDMARKS = 2
SPREAD_TOL = 1e-12
RANK_ONE_RATIO = 1e-9


def _refuse(code: str, message: str, hint: str, **context: object) -> GeometryError:
    return GeometryError(message, hint=hint, code=code, context=context)


def _checked_points(src: Points, dst: Points) -> tuple[Points, Points]:
    """Validates matched landmark arrays before any reduction runs.

    Args:
        src: Candidate points on the old sheet.
        dst: Candidate matching points on the new sheet.

    Returns:
        points: The two arrays as float, both (N, 2) with N >= 2, finite and spread out.
    """
    try:
        src_a, dst_a = np.asarray(src, dtype=float), np.asarray(dst, dtype=float)
    except (ValueError, TypeError) as error:
        raise _refuse(
            "BAD_SHAPE",
            "similarity fit needs two numeric (N, 2) arrays",
            "pass matched (N, 2) arrays of numbers with the same N",
        ) from error
    if src_a.ndim != 2 or src_a.shape[1] != 2 or src_a.shape != dst_a.shape:
        raise _refuse(
            "BAD_SHAPE",
            "similarity fit needs two (N, 2) arrays of the same shape",
            "pass matched (N, 2) point arrays with the same N",
            src_shape=list(src_a.shape),
            dst_shape=list(dst_a.shape),
        )
    if len(src_a) < MIN_SHARED_LANDMARKS:
        raise _refuse(
            "TOO_FEW_POINTS",
            "similarity fit needs at least two matched points",
            "match two or more landmarks that are not on top of each other",
            count=len(src_a),
        )
    if not (np.isfinite(src_a).all() and np.isfinite(dst_a).all()):
        raise _refuse(
            "NOT_FINITE",
            "similarity fit needs finite coordinates",
            "remove NaN and infinite landmark coordinates",
        )
    for name, points in (("src", src_a), ("dst", dst_a)):
        if float(((points - points.mean(axis=0)) ** 2).sum()) / len(points) < SPREAD_TOL:
            raise _refuse(
                "DEGENERATE",
                f"{name} landmarks sit on top of each other",
                "match two or more landmarks that are not on top of each other",
                side=name,
            )
    return src_a, dst_a


def _fit_matrix(src: Points, dst: Points) -> Matrix:
    """Computes the Umeyama similarity for validated landmark arrays.

    Args:
        src: Validated (N, 2) points on the old sheet.
        dst: Validated (N, 2) matching points on the new sheet.

    Returns:
        matrix: 3x3 affine map.
    """
    mu_src, mu_dst = src.mean(axis=0), dst.mean(axis=0)
    spread = float(((src - mu_src) ** 2).sum()) / len(src)
    covariance = (dst - mu_dst).T @ (src - mu_src) / len(src)
    u, singular, vt = np.linalg.svd(covariance)
    is_mirrored = np.linalg.det(u) * np.linalg.det(vt) < 0
    if is_mirrored and singular[1] > RANK_ONE_RATIO * singular[0]:
        raise _refuse(
            "REFLECTION",
            "landmarks are mirrored between the sheets",
            "similarity fits keep handedness; check the landmark pairing; to fit a deliberate "
            "mirror, reflect one set first with showpy.geo.transforms.mirror",
        )
    keep_handedness = np.diag([1.0, -1.0 if is_mirrored else 1.0])
    rotation = u @ keep_handedness @ vt
    scale = float(np.trace(np.diag(singular) @ keep_handedness)) / spread
    matrix = np.eye(3)
    matrix[:2, :2] = scale * rotation
    matrix[:2, 2] = mu_dst - scale * rotation @ mu_src
    return matrix


def similarity_fit(src: Points, dst: Points) -> Matrix:
    """Fits the rotation, uniform scale and translation that best maps `src` onto `dst`.

    Least squares over matched rows, without reflection (Umeyama 1991). Raises GeometryError
    for a bad shape, fewer than two rows, non-finite values, coincident points on either side,
    or a mirrored landmark set. Collinear landmarks have no handedness and still fit.

    Args:
        src: (N, 2) points on the old sheet, N >= 2.
        dst: (N, 2) matching points on the new sheet.

    Returns:
        matrix: 3x3 affine map.

    >>> fit = similarity_fit(np.array([[0.0, 0], [1, 0]]), np.array([[0.0, 1], [2, 1]]))
    >>> fit.round(6).tolist()
    [[2.0, 0.0, 0.0], [0.0, 2.0, 1.0], [0.0, 0.0, 1.0]]
    """
    try:
        with np.errstate(over="raise", invalid="raise"):
            matrix = _fit_matrix(*_checked_points(src, dst))
    except FloatingPointError as error:
        raise _refuse(
            "NOT_FINITE",
            "similarity fit overflowed on these coordinates",
            "use coordinates in meters on the sheet, well below 1e150",
        ) from error
    if not np.isfinite(matrix).all():
        raise _refuse(
            "NOT_FINITE",
            "similarity fit produced a non-finite matrix",
            "use coordinates in meters on the sheet, well below 1e150",
        )
    return matrix
