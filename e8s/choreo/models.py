"""
models.py --- choreography data classes: placements and sets

Contains:
    TABLE_COLUMNS: lists the columns of a set's placement table
    ApprovalState: names the collaboration states of a set
    Placement: holds one spot's state at one set
    PlacementPatch: holds the fields of a placement to change, leaving the rest as they are
    Set: holds a snapshot of every spot's placement at one moment
    Set.table(): returns placements as a DataFrame
    Set.positions(): returns coordinates in selection order
    Set.get(): returns one placement
    Set.with_placements(): returns a set with placements replaced or patched, respecting pins
    SpotCount: counts spots on and off the field in one set

Form, path and match classes live in form_models, path_models and match_models; their old names
here resolve with a deprecation warning.
"""

import math
from collections.abc import Mapping, Sequence
from enum import StrEnum
from functools import cached_property
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
from pydantic import (
    SerializerFunctionWrapHandler,
    ValidationInfo,
    model_serializer,
    model_validator,
)

from e8s.core.deprecation import moved
from e8s.core.errors import LockedError, SchemaError, UnknownRefError
from e8s.core.extras import require_extra
from e8s.core.ids import LEGACY_IDS_KEY, FormId, SetId, SpotId, check_id
from e8s.core.selection import Selection, operand_ids
from e8s.core.types import FrozenModel, Heading, Meters, Point, Points

if TYPE_CHECKING:
    import pandas as pd

TABLE_COLUMNS = ("entity", "x", "y", "facing", "form", "order", "pinned", "on_field", "note")


class ApprovalState(StrEnum):
    """Names the collaboration states of a set."""

    DRAFT = "draft"
    PROPOSED = "proposed"
    APPROVED = "approved"


class Placement(FrozenModel):
    """Holds one spot's state at one set.

    Attributes:
        entity: Spot ID.
        x: Meters from the center line, audience-left negative.
        y: Meters upstage from the front edge.
        facing: Degrees wrapped into [0, 360), 0 toward the audience, counter-clockwise positive.
        form: Form the spot belongs to in this set.
        order: Index along that form.
        pinned: Designer lock; edits need force=True.
        note: Text printed on sheets.
        on_field: False before an entrance or after an exit; not drawn or collision-checked.
    """

    entity: SpotId
    x: Meters
    y: Meters
    facing: Heading = 0.0
    form: FormId | None = None
    order: int | None = None
    pinned: bool = False
    note: str | None = None
    on_field: bool = True

    @model_validator(mode="after")
    def _finite(self) -> "Placement":
        if not all(math.isfinite(v) for v in (self.x, self.y, self.facing)):
            raise SchemaError(
                f"placement of {self.entity} has a non-finite value",
                hint="give finite x, y and facing",
                context={"entity": self.entity, "x": self.x, "y": self.y},
            )
        return self

    @property
    def point(self) -> Point:
        """Returns (x, y)."""
        return self.x, self.y


class PlacementPatch(FrozenModel):
    """Holds the fields of a placement to change; a field never set is left as it is.

    Setting `form`, `order` or `note` to None clears it. Each field validates like the
    placement's own.

    Attributes:
        x: Meters from the center line.
        y: Meters upstage from the front edge.
        facing: Degrees, wrapped into [0, 360).
        form: Form the spot belongs to in this set.
        order: Index along that form.
        pinned: Designer lock.
        note: Text printed on sheets.
        on_field: False before an entrance or after an exit.

    >>> from e8s.examples import two_set_show
    >>> moved = two_set_show().set("2").with_placements({"T1": PlacementPatch(x=3.0)})
    >>> moved.get("T1").x
    3.0
    """

    x: Meters = 0.0
    y: Meters = 0.0
    facing: Heading = 0.0
    form: FormId | None = None
    order: int | None = None
    pinned: bool = False
    note: str | None = None
    on_field: bool = True


class Set(FrozenModel):
    """Holds a snapshot of every spot's placement at one moment.

    Attributes:
        id: Set ID, e.g. `12` or `12A`.
        key: Stable storage key, fixed when the set is created; defaults to the ID. Every
            key must pass check_id, except a defaulted key validated with legacy IDs accepted.
        counts: Counts since the previous set; 0 for the first.
        placements: One placement per spot in the cast.
        forms: Forms present in this set.
        label: Designer label.
        cue: Rehearsal mark or cue.
        source_hash: Hash of the script that built the set.
        state: Collaboration state.
    """

    id: SetId
    key: str | None = None
    counts: float
    placements: tuple[Placement, ...] = ()
    forms: tuple[FormId, ...] = ()
    label: str | None = None
    cue: str | None = None
    source_hash: str = ""
    state: ApprovalState = ApprovalState.DRAFT

    @model_validator(mode="after")
    def _valid(self, info: ValidationInfo) -> "Set":
        if self.counts < 0:
            raise SchemaError(f"set {self.id} has negative counts", hint="use counts >= 0")
        entities = [p.entity for p in self.placements]
        if len(set(entities)) != len(entities):
            raise SchemaError(f"set {self.id} places a spot twice", hint="one placement per spot")
        key = self.id if self.key is None else self.key
        is_legacy = isinstance(info.context, Mapping) and info.context.get(LEGACY_IDS_KEY) is True
        if not (is_legacy and key == self.id):
            key = check_id(key, kind="set key")
        # The model is frozen; the validator is the one place that fills the defaulted key.
        object.__setattr__(self, "key", key)
        return self

    # Any: the dump is the JSON-like mapping pydantic built for this model.
    @model_serializer(mode="wrap")
    def _omit_default_key(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        dumped: dict[str, Any] = handler(self)
        if self.key == self.id:
            dumped.pop("key", None)
        return dumped

    @cached_property
    def _index(self) -> dict[SpotId, int]:
        return {p.entity: i for i, p in enumerate(self.placements)}

    def table(self) -> "pd.DataFrame":
        """Returns placements as a DataFrame, one row per spot; needs the `tables` extra."""
        require_extra("pandas", extra="tables", needed_by="Set.table")
        import pandas as pd

        rows = [{column: getattr(p, column) for column in TABLE_COLUMNS} for p in self.placements]
        return pd.DataFrame(rows, columns=list(TABLE_COLUMNS))

    def entity_ids(self) -> tuple[SpotId, ...]:
        """Returns the spot IDs in placement order."""
        return tuple(p.entity for p in self.placements)

    def positions(self, sel: Literal["all"] | Sequence[str] | Selection = "all") -> Points:
        """Returns coordinates (N, 2) in selection order; "all" uses placement order."""
        ids = self.entity_ids() if sel == "all" else operand_ids(sel)
        if not ids:
            return np.zeros((0, 2))
        return np.array([self.get(i).point for i in ids], dtype=float)

    def get(self, entity: str) -> Placement:
        """Returns one placement, raising UnknownRefError when the spot is absent."""
        index = self._index
        if entity not in index:
            raise UnknownRefError(
                f"spot {entity!r} is not in set {self.id}",
                hint="check the spot id or add the spot first",
                context={"set": self.id, "entity": entity},
            )
        return self.placements[index[SpotId(entity)]]

    def with_placements[K: str](
        self, updates: Mapping[K, Placement | PlacementPatch], *, force: bool = False
    ) -> "Set":
        """Returns a set with placements replaced or patched; pinned rows need force=True.

        >>> from e8s.examples import two_set_show
        >>> second = two_set_show().set("2")
        >>> second.with_placements({"T1": PlacementPatch(note="solo")}).get("T1").note
        'solo'
        """
        rows = list(self.placements)
        for entity, change in updates.items():
            current = self.get(entity)
            if current.pinned and not force:
                raise LockedError(
                    f"{entity} is pinned in set {self.id}",
                    hint="unpin it first, or pass force=True as the designer",
                    context={"set": self.id, "entity": entity},
                )
            rows[self._index[current.entity]] = _apply_change(current, change)
        return self.replace(placements=tuple(rows))


def _apply_change(current: Placement, change: Placement | PlacementPatch) -> Placement:
    if isinstance(change, Placement):
        if change.entity != current.entity:
            raise SchemaError(
                f"update for {current.entity} names {change.entity}",
                hint="key updates by the placement's own entity",
            )
        return change
    if not isinstance(change, PlacementPatch):
        raise SchemaError(
            f"update for {current.entity} is a {type(change).__name__}",
            hint="pass a Placement or a PlacementPatch",
        )
    return current.replace(**{name: getattr(change, name) for name in change._set_fields()})


class SpotCount(FrozenModel):
    """Counts spots on and off the field in one set.

    Attributes:
        set_id: Set counted.
        on_field: Spots on the field.
        off_field: Spots off the field.
        changed: Whether the on-field count differs from the previous set.
    """

    set_id: SetId
    on_field: int
    off_field: int
    changed: bool


__getattr__ = moved(
    __name__,
    {
        "SpacingSpec": "e8s.choreo.form_models.SpacingSpec",
        "FacingSpec": "e8s.choreo.form_models.FacingSpec",
        "TextRecipe": "e8s.choreo.form_models.TextRecipe",
        "FormKind": "e8s.choreo.form_models.FormKind",
        "CrowdedStroke": "e8s.choreo.form_models.CrowdedStroke",
        "TextReport": "e8s.choreo.form_models.TextReport",
        "Form": "e8s.choreo.form_models.Form",
        "FormFnInfo": "e8s.choreo.form_models.FormFnInfo",
        "FONT_URI_PATTERN": "e8s.choreo.form_models.FONT_URI_PATTERN",
        "MAX_TEXT_FONTS": "e8s.choreo.form_models.MAX_TEXT_FONTS",
        "FontUri": "e8s.choreo.form_models.FontUri",
        "PathStyle": "e8s.choreo.path_models.PathStyle",
        "FacingMode": "e8s.choreo.path_models.FacingMode",
        "TurnDirection": "e8s.choreo.path_models.TurnDirection",
        "FacingOverride": "e8s.choreo.path_models.FacingOverride",
        "Waypoint": "e8s.choreo.path_models.Waypoint",
        "Easing": "e8s.choreo.path_models.Easing",
        "PathRow": "e8s.choreo.path_models.PathRow",
        "Route": "e8s.choreo.path_models.Route",
        "TransitionSpec": "e8s.choreo.path_models.TransitionSpec",
        "DEFAULT_EASING_STRENGTH": "e8s.choreo.path_models.DEFAULT_EASING_STRENGTH",
        "MatchObjective": "e8s.choreo.match_models.MatchObjective",
        "CostFn": "e8s.choreo.match_models.CostFn",
        "MatchOptions": "e8s.choreo.match_models.MatchOptions",
        "MatchResult": "e8s.choreo.match_models.MatchResult",
        "MATCH_WEIGHT_KEYS": "e8s.choreo.match_models.MATCH_WEIGHT_KEYS",
    },
    since="0.4.0",
    remove="0.6.0",
)

if TYPE_CHECKING:
    from e8s.choreo.form_models import (
        FONT_URI_PATTERN as FONT_URI_PATTERN,
    )
    from e8s.choreo.form_models import (
        MAX_TEXT_FONTS as MAX_TEXT_FONTS,
    )
    from e8s.choreo.form_models import (
        CrowdedStroke as CrowdedStroke,
    )
    from e8s.choreo.form_models import (
        FacingSpec as FacingSpec,
    )
    from e8s.choreo.form_models import (
        FontUri as FontUri,
    )
    from e8s.choreo.form_models import (
        Form as Form,
    )
    from e8s.choreo.form_models import (
        FormFnInfo as FormFnInfo,
    )
    from e8s.choreo.form_models import (
        FormKind as FormKind,
    )
    from e8s.choreo.form_models import (
        SpacingSpec as SpacingSpec,
    )
    from e8s.choreo.form_models import (
        TextRecipe as TextRecipe,
    )
    from e8s.choreo.form_models import (
        TextReport as TextReport,
    )
    from e8s.choreo.match_models import (
        MATCH_WEIGHT_KEYS as MATCH_WEIGHT_KEYS,
    )
    from e8s.choreo.match_models import (
        CostFn as CostFn,
    )
    from e8s.choreo.match_models import (
        MatchObjective as MatchObjective,
    )
    from e8s.choreo.match_models import (
        MatchOptions as MatchOptions,
    )
    from e8s.choreo.match_models import (
        MatchResult as MatchResult,
    )
    from e8s.choreo.path_models import (
        DEFAULT_EASING_STRENGTH as DEFAULT_EASING_STRENGTH,
    )
    from e8s.choreo.path_models import (
        Easing as Easing,
    )
    from e8s.choreo.path_models import (
        FacingMode as FacingMode,
    )
    from e8s.choreo.path_models import (
        FacingOverride as FacingOverride,
    )
    from e8s.choreo.path_models import (
        PathRow as PathRow,
    )
    from e8s.choreo.path_models import (
        PathStyle as PathStyle,
    )
    from e8s.choreo.path_models import (
        Route as Route,
    )
    from e8s.choreo.path_models import (
        TransitionSpec as TransitionSpec,
    )
    from e8s.choreo.path_models import (
        TurnDirection as TurnDirection,
    )
    from e8s.choreo.path_models import (
        Waypoint as Waypoint,
    )
