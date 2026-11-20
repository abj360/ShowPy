"""
models.py --- choreography data classes: placements and sets

Contains:
    TABLE_COLUMNS: lists the columns of a set's placement table
    ApprovalState: names the collaboration states of a set
    Placement: holds one spot's state at one set
    Set: holds a snapshot of every spot's placement at one moment
    Set.table(): returns placements as a DataFrame
    Set.positions(): returns coordinates in selection order
    Set.get(): returns one placement
    Set.with_placements(): returns a set with edited placements, respecting pins
    SpotCount: counts spots on and off the field in one set

Form, path and match classes live in form_models, path_models and match_models; their old names
here resolve with a deprecation warning.
"""

import math
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
from pydantic import SerializerFunctionWrapHandler, model_serializer, model_validator

from showpy.core.deprecation import moved
from showpy.core.errors import LockedError, SchemaError, UnknownRefError
from showpy.core.extras import require_extra
from showpy.core.ids import FormId, SetId, SpotId, check_id
from showpy.core.selection import Selection
from showpy.core.types import Degrees, FrozenModel, Meters, Point, Points

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
        facing: Degrees, 0 toward the audience, counter-clockwise positive.
        form: Form the spot belongs to in this set.
        order: Index along that form.
        pinned: Designer lock; edits need force=True.
        note: Text printed on sheets.
        on_field: False before an entrance or after an exit; not drawn or collision-checked.
    """

    entity: SpotId
    x: Meters
    y: Meters
    facing: Degrees = 0.0
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


class Set(FrozenModel):
    """Holds a snapshot of every spot's placement at one moment.

    Attributes:
        id: Set ID, e.g. `12` or `12A`.
        key: Stable storage key, fixed when the set is created; defaults to the ID.
            A key that differs from the ID must pass check_id.
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
    def _valid(self) -> "Set":
        if self.counts < 0:
            raise SchemaError(f"set {self.id} has negative counts", hint="use counts >= 0")
        entities = [p.entity for p in self.placements]
        if len(set(entities)) != len(entities):
            raise SchemaError(f"set {self.id} places a spot twice", hint="one placement per spot")
        # A key equal to the ID is a stored ID, left unchecked until stored IDs are migrated;
        # constructors check new keys through free_set_key().
        key = self.id if self.key in (None, self.id) else check_id(self.key, kind="set key")
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
        ids = self.entity_ids() if sel == "all" else tuple(sel)
        if not ids:
            return np.zeros((0, 2))
        return np.array([self.get(i).point for i in ids], dtype=float)

    def get(self, entity: str) -> Placement:
        """Returns one placement, raising UnknownRefError when the spot is absent."""
        index = self._index()
        if entity not in index:
            raise UnknownRefError(
                f"spot {entity!r} is not in set {self.id}",
                hint="check the spot id or add the spot first",
                context={"set": self.id, "entity": entity},
            )
        return self.placements[index[SpotId(entity)]]

    # Any: updates may be a Placement or a partial dict of any placement fields.
    def with_placements[K: str](
        self, updates: Mapping[K, Placement | Mapping[str, Any]], *, force: bool = False
    ) -> "Set":
        """Returns a set with placements replaced or patched; pinned rows need force=True."""
        rows = list(self.placements)
        index = self._index()
        for entity, change in updates.items():
            current = self.get(entity)
            if current.pinned and not force:
                raise LockedError(
                    f"{entity} is pinned in set {self.id}",
                    hint="unpin it first, or pass force=True as the designer",
                    context={"set": self.id, "entity": entity},
                )
            rows[index[current.entity]] = _apply_change(current, change)
        return self.replace(placements=tuple(rows))


# Any: a partial update may touch any placement field.
def _apply_change(current: Placement, change: Placement | Mapping[str, Any]) -> Placement:
    if isinstance(change, Placement):
        if change.entity != current.entity:
            raise SchemaError(
                f"update for {current.entity} names {change.entity}",
                hint="key updates by the placement's own entity",
            )
        return change
    return Placement.model_validate({**current.model_dump(), **change})


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
        "SpacingSpec": "showpy.choreo.form_models.SpacingSpec",
        "FacingSpec": "showpy.choreo.form_models.FacingSpec",
        "TextRecipe": "showpy.choreo.form_models.TextRecipe",
        "FormKind": "showpy.choreo.form_models.FormKind",
        "CrowdedStroke": "showpy.choreo.form_models.CrowdedStroke",
        "TextReport": "showpy.choreo.form_models.TextReport",
        "Form": "showpy.choreo.form_models.Form",
        "FormFnInfo": "showpy.choreo.form_models.FormFnInfo",
        "FONT_URI_PATTERN": "showpy.choreo.form_models.FONT_URI_PATTERN",
        "MAX_TEXT_FONTS": "showpy.choreo.form_models.MAX_TEXT_FONTS",
        "FontUri": "showpy.choreo.form_models.FontUri",
        "PathStyle": "showpy.choreo.path_models.PathStyle",
        "FacingMode": "showpy.choreo.path_models.FacingMode",
        "TurnDirection": "showpy.choreo.path_models.TurnDirection",
        "FacingOverride": "showpy.choreo.path_models.FacingOverride",
        "Waypoint": "showpy.choreo.path_models.Waypoint",
        "Easing": "showpy.choreo.path_models.Easing",
        "PathRow": "showpy.choreo.path_models.PathRow",
        "Route": "showpy.choreo.path_models.Route",
        "TransitionSpec": "showpy.choreo.path_models.TransitionSpec",
        "DEFAULT_EASING_STRENGTH": "showpy.choreo.path_models.DEFAULT_EASING_STRENGTH",
        "MatchObjective": "showpy.choreo.match_models.MatchObjective",
        "CostFn": "showpy.choreo.match_models.CostFn",
        "MatchOptions": "showpy.choreo.match_models.MatchOptions",
        "MatchResult": "showpy.choreo.match_models.MatchResult",
        "MATCH_WEIGHT_KEYS": "showpy.choreo.match_models.MATCH_WEIGHT_KEYS",
    },
    since="0.4.0",
    remove="0.6.0",
)

if TYPE_CHECKING:
    from showpy.choreo.form_models import (
        FONT_URI_PATTERN as FONT_URI_PATTERN,
    )
    from showpy.choreo.form_models import (
        MAX_TEXT_FONTS as MAX_TEXT_FONTS,
    )
    from showpy.choreo.form_models import (
        CrowdedStroke as CrowdedStroke,
    )
    from showpy.choreo.form_models import (
        FacingSpec as FacingSpec,
    )
    from showpy.choreo.form_models import (
        FontUri as FontUri,
    )
    from showpy.choreo.form_models import (
        Form as Form,
    )
    from showpy.choreo.form_models import (
        FormFnInfo as FormFnInfo,
    )
    from showpy.choreo.form_models import (
        FormKind as FormKind,
    )
    from showpy.choreo.form_models import (
        SpacingSpec as SpacingSpec,
    )
    from showpy.choreo.form_models import (
        TextRecipe as TextRecipe,
    )
    from showpy.choreo.form_models import (
        TextReport as TextReport,
    )
    from showpy.choreo.match_models import (
        MATCH_WEIGHT_KEYS as MATCH_WEIGHT_KEYS,
    )
    from showpy.choreo.match_models import (
        CostFn as CostFn,
    )
    from showpy.choreo.match_models import (
        MatchObjective as MatchObjective,
    )
    from showpy.choreo.match_models import (
        MatchOptions as MatchOptions,
    )
    from showpy.choreo.match_models import (
        MatchResult as MatchResult,
    )
    from showpy.choreo.path_models import (
        DEFAULT_EASING_STRENGTH as DEFAULT_EASING_STRENGTH,
    )
    from showpy.choreo.path_models import (
        Easing as Easing,
    )
    from showpy.choreo.path_models import (
        FacingMode as FacingMode,
    )
    from showpy.choreo.path_models import (
        FacingOverride as FacingOverride,
    )
    from showpy.choreo.path_models import (
        PathRow as PathRow,
    )
    from showpy.choreo.path_models import (
        PathStyle as PathStyle,
    )
    from showpy.choreo.path_models import (
        Route as Route,
    )
    from showpy.choreo.path_models import (
        TransitionSpec as TransitionSpec,
    )
    from showpy.choreo.path_models import (
        TurnDirection as TurnDirection,
    )
    from showpy.choreo.path_models import (
        Waypoint as Waypoint,
    )
