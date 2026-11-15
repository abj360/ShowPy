"""
models.py --- geometry result data classes: snap reports, spacing stats, form and speed metrics

Contains:
    SnapReport: reports how many points a snap moved and by how much at most
    IntervalStats: summarizes the gaps between spots
    FormMetrics: summarizes the shape quality of a form's points
    SpeedStats: summarizes the movement demand of one spot over one transition
"""

from showpy.core.types import FrozenModel, Meters


class SnapReport(FrozenModel):
    """Reports how many points a snap moved and by how much at most.

    Attributes:
        moved: Points whose position changed.
        max_shift: Largest distance any point moved.
    """

    moved: int
    max_shift: Meters


class IntervalStats(FrozenModel):
    """Summarizes the gaps between spots.

    Attributes:
        mean: Mean gap.
        min: Smallest gap.
        max: Largest gap.
        std: Population standard deviation of the gaps.
        gaps: Every gap: consecutive pairs when ordered, nearest neighbour per spot otherwise.
    """

    mean: Meters
    min: Meters
    max: Meters
    std: Meters
    gaps: tuple[Meters, ...]


class FormMetrics(FrozenModel):
    """Summarizes the shape quality of a form's points, read as an ordered polyline.

    Attributes:
        length: Polyline length through the points in order.
        intervals: Consecutive gap statistics.
        curvature_range: Smallest and largest discrete curvature (1/m) at interior points.
        symmetry_error: Largest mirror mismatch about the best vertical axis.
    """

    length: Meters
    intervals: IntervalStats
    curvature_range: tuple[float, float]
    symmetry_error: Meters


class SpeedStats(FrozenModel):
    """Summarizes the movement demand of one spot over one transition.

    Attributes:
        distance: Meters travelled along the path.
        counts: Counts spent moving.
        m_per_count: Mean meters per count.
        m_per_s: Mean meters per second.
        max_instant: Largest instantaneous speed in meters per count.
    """

    distance: Meters
    counts: float
    m_per_count: float
    m_per_s: float
    max_instant: float
