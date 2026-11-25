"""
collab.py --- live collaboration: pins, highlights, selection

Contains:
    pins(): lists the designer's locks
    highlight(): points the designer at spots, an area, a set, a transition or a host-defined
        area
    selection(): returns what the designer has selected
"""

from datetime import datetime

from e8s.core.errors import TimeRangeError
from e8s.core.selection import Selection
from e8s.show.anchors import Anchor
from e8s.show.collab_models import CanvasEvent, Highlight
from e8s.show.models import Pin, Show
from e8s.show.structured import SELECTION_DOC
from e8s.show.workspace import Workspace

DEFAULT_TTL_S = 30.0


def pins(show: Show) -> list[Pin]:
    """Lists the designer's locks in the order they were made."""
    return list(show.pins)


def highlight(
    target: Anchor | str, ttl_s: float = DEFAULT_TTL_S, *, at: datetime, event_id: str
) -> CanvasEvent:
    """Points the designer at spots, an area, a point, a set, a transition or a named area.

    A string target is the name of an area the host defines; the library keeps no zones.

    Highlights are pointers, not edits: they carry no revision and change nothing.

    >>> from datetime import UTC
    >>> event = highlight(Anchor.on_spots(["T1"]), at=datetime(2026, 11, 11, tzinfo=UTC),
    ...                   event_id="h1")
    >>> event.kind, event.payload.ttl_s
    ('highlight', 30.0)
    """
    if not ttl_s > 0:
        raise TimeRangeError(
            f"highlight lasts {ttl_s}s",
            hint="use ttl_s > 0",
            context={"ttl_s": ttl_s},
        )
    payload = (
        Highlight(anchor=target, ttl_s=ttl_s)
        if isinstance(target, Anchor)
        else Highlight(zone=target, ttl_s=ttl_s)
    )
    return CanvasEvent(id=event_id, at=at, kind="highlight", payload=payload)


def selection(ws: Workspace) -> Selection:
    """Returns what the designer has selected on the canvas; empty when nothing is."""
    return Selection._validated_json(ws.read(SELECTION_DOC))
