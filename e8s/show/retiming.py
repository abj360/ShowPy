"""
retiming.py --- scales and checks the stored path timing of a transition whose set is retimed

Contains:
    late_rows(): lists the spots whose path window does not fit a transition's counts
    late_routes(): lists the routes that end after a transition's counts
    retimed_spec(): retimes a transition, scaling its paths or refusing ones that run late
    remapped_overrides(): maps facing override counts across a retimed set
"""

from e8s.choreo.path_models import FacingOverride, Route, TransitionSpec, Waypoint
from e8s.core.errors import PathError
from e8s.core.ids import SpotId, format_tid


def _scale_route(route: Route, factor: float) -> Route:
    end = None if route.end is None else route.end * factor
    return route.replace(start=route.start * factor, end=end)


def _scale_spec(spec: TransitionSpec, factor: float) -> TransitionSpec:
    rows = {}
    for entity, row in spec.rows.items():
        waypoints = tuple(
            w if w.at is None else Waypoint(x=w.x, y=w.y, at=w.at * factor) for w in row.waypoints
        )
        end = None if row.end is None else row.end * factor
        rows[entity] = row.replace(start=row.start * factor, end=end, waypoints=waypoints)
    routes = {key: _scale_route(route, factor) for key, route in spec.routes.items()}
    return spec.replace(rows=rows, routes=routes)


def late_rows(spec: TransitionSpec, counts: float) -> list[SpotId]:
    """Lists the spots whose stored path window does not fit a transition of `counts`.

    >>> from e8s.examples import two_set_show
    >>> late_rows(two_set_show().transition(("1", "2")), 8.0)
    []
    """
    return [
        entity
        for entity, row in spec.rows.items()
        if row.window(counts)[1] > counts
        or row.start >= counts
        or any(w.at is not None and w.at > counts for w in row.waypoints)
    ]


def late_routes(spec: TransitionSpec, counts: float) -> list[str]:
    """Lists the routes of a transition that end after `counts`.

    >>> from e8s.examples import two_set_show
    >>> late_routes(two_set_show().transition(("1", "2")), 8.0)
    []
    """
    return [key for key, r in spec.routes.items() if (r.end or counts) > counts]


def _overruns(spec: TransitionSpec, counts: float) -> list[str]:
    return [*late_rows(spec, counts), *late_routes(spec, counts)]


def retimed_spec(spec: TransitionSpec, counts: float, *, rescale: bool) -> TransitionSpec:
    """Returns a transition retimed to `counts`, scaling its paths first when `rescale` is set.

    Raises PathError naming the rows and routes that would run past the new counts.

    >>> from e8s.examples import two_set_show
    >>> retimed_spec(two_set_show().transition(("1", "2")), 12.0, rescale=False).counts
    12.0
    """
    if rescale and spec.counts > 0:
        spec = _scale_spec(spec, counts / spec.counts)
    late = _overruns(spec, counts)
    if late:
        raise PathError(
            f"paths in {format_tid(spec.id)} run past {counts} counts: {', '.join(late)}",
            hint="pass rescale=True to scale the paths, or shorten them first",
            context={"tid": spec.id, "late": late},
        )
    return spec.replace(counts=counts)


def _remapped_count(count: float, start: float, old: float, new: float) -> float:
    if count <= start:
        return count
    if count <= start + old:
        return start + (count - start) * new / old
    return count + new - old


def remapped_overrides(
    overrides: tuple[FacingOverride, ...], start: float, old: float, new: float
) -> tuple[FacingOverride, ...]:
    """Maps facing override counts for a set retimed from `old` to `new` counts at `start`.

    Counts before the set stay, counts inside it scale and counts after it shift.

    >>> remapped_overrides((), 0.0, 8.0, 16.0)
    ()
    """
    if old <= 0:
        return overrides
    return tuple(
        o.replace(
            start=_remapped_count(o.start, start, old, new),
            end=_remapped_count(o.end, start, old, new),
        )
        for o in overrides
    )
