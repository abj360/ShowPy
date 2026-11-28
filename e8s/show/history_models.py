"""
history_models.py --- the diff and revision records of a show

Contains:
    STORED_TIME_KEY: validation context key whose True value reads a naive stored time as UTC
    SetDiff: lists what differs between two sets
    PathDiff: lists what differs between two transitions
    Revision: records one saved state of a show in storage
"""

from collections.abc import Mapping
from datetime import UTC, datetime

from pydantic import ValidationInfo, field_validator

from e8s.core.errors import SchemaError
from e8s.core.ids import (
    FormId,
    RouteId,
    SpotId,
)
from e8s.core.types import FrozenModel

STORED_TIME_KEY = "stored_time"


class SetDiff(FrozenModel):
    """Lists what differs between two sets; every field is sorted naturally.

    Attributes:
        moved: Spots whose position or on-field state changed.
        facing_changed: Spots whose facing changed.
        added: Spots placed only in the second set.
        removed: Spots placed only in the first set.
        forms_changed: Forms added, removed or re-membered between the sets.
    """

    moved: tuple[SpotId, ...] = ()
    facing_changed: tuple[SpotId, ...] = ()
    added: tuple[SpotId, ...] = ()
    removed: tuple[SpotId, ...] = ()
    forms_changed: tuple[FormId, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Reports whether the sets match."""
        return not any(self._dumped().values())


class PathDiff(FrozenModel):
    """Lists what differs between two transitions; a missing row equals a default row.

    Attributes:
        changed_rows: Spots whose effective path row changed.
        routes_changed: Routes added, removed or edited.
    """

    changed_rows: tuple[SpotId, ...] = ()
    routes_changed: tuple[RouteId, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Reports whether the transitions match."""
        return not (self.changed_rows or self.routes_changed)


class Revision(FrozenModel):
    """Records one saved state of a show: what changed and when, never who or why.

    Attributes:
        id: Content hash of parent, time and change.
        parent: Previous revision; empty for the first.
        at: When it was saved, with a timezone; passed in, never read from the clock. A stored
            revision whose time has none reads as UTC.
        changed: Stored paths written or deleted, sorted; set and transition folders are named
            by set key, so they can differ from workspace paths after a renumber.
    """

    id: str
    parent: str
    at: datetime
    changed: tuple[str, ...] = ()

    @field_validator("at")
    @classmethod
    def _at_has_a_timezone(cls, value: datetime, info: ValidationInfo) -> datetime:
        if value.utcoffset() is not None:
            return value
        # Releases before 0.5 stored the time as given, so a stored revision may have none; only
        # a new revision must carry one.
        if isinstance(info.context, Mapping) and info.context.get(STORED_TIME_KEY) is True:
            return value.replace(tzinfo=UTC)
        raise SchemaError(
            f"revision time {value.isoformat()} has no timezone, so revisions cannot be ordered",
            hint="pass an aware time, such as datetime(2026, 1, 1, tzinfo=UTC)",
            context={"at": value.isoformat()},
        )
