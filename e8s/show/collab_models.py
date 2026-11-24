"""
collab_models.py --- the records of live collaboration: comments, ops, events, proposals

Contains:
    EventKind: names what a canvas event records
    ProposalState: names where a proposal stands
    Comment: holds one review comment anchored on the canvas
    PlacementOp: replaces one spot's placement in one set
    FormOp: replaces or adds one form
    PathsOp: replaces one transition's stored paths
    CountsOp: retimes one set
    ChangeOp: tags the four change ops by their `op` field
    Highlight: points the designer at spots, an area, a point, a set, a transition or a named
        area the host defines
    EventPayload: unions the payloads a canvas event carries
    EVENT_PAYLOADS: maps each event kind to its payload type
    CanvasEvent: records one edit or pointer from either side of the canvas
    Proposal: holds an agent suggestion the designer accepts or rejects
    ProposalPreview: holds the ghost placements and paths a proposal would produce
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import (
    Field,
    model_validator,
)

from e8s.choreo.form_models import Form
from e8s.choreo.models import Placement
from e8s.choreo.path_models import TransitionSpec
from e8s.core.errors import SchemaError
from e8s.core.ids import (
    SetId,
)
from e8s.core.readonly import ReadOnlyMap
from e8s.core.types import FrozenModel
from e8s.show.anchors import Anchor
from e8s.show.models import Pin

type EventKind = Literal[
    "move", "edit_form", "pin", "unpin", "edit_paths", "comment", "set_counts", "highlight"
]
type ProposalState = Literal["open", "accepted", "rejected", "withdrawn"]


class Comment(FrozenModel):
    """Holds one review comment on the canvas.

    Attributes:
        id: Comment ID.
        anchor: What the comment points at.
        text: Comment body.
        resolved: Whether the discussion is closed.
        author: Who wrote it.
    """

    id: str
    anchor: Anchor
    text: str
    resolved: bool = False
    author: str = "designer"


class PlacementOp(FrozenModel):
    """Replaces one spot's placement in one set.

    Attributes:
        op: Tag.
        set_id: Set edited.
        placement: New placement; its entity names the spot.
    """

    op: Literal["placement"] = "placement"
    set_id: SetId
    placement: Placement


class FormOp(FrozenModel):
    """Replaces or adds one form.

    Attributes:
        op: Tag.
        form: New form; its set must exist.
    """

    op: Literal["form"] = "form"
    form: Form


class PathsOp(FrozenModel):
    """Replaces one transition's stored paths.

    Attributes:
        op: Tag.
        spec: New paths; counts are taken from the destination set.
    """

    op: Literal["paths"] = "paths"
    spec: TransitionSpec


class CountsOp(FrozenModel):
    """Retimes one set.

    Attributes:
        op: Tag.
        set_id: Set retimed.
        counts: New counts.
        rescale: Whether stored path windows scale with the counts.
    """

    op: Literal["counts"] = "counts"
    set_id: SetId
    counts: float
    rescale: bool = False


type ChangeOp = Annotated[PlacementOp | FormOp | PathsOp | CountsOp, Field(discriminator="op")]


class Highlight(FrozenModel):
    """Points the designer at part of the canvas for a while; it changes nothing.

    Attributes:
        anchor: What is pointed at, when it is spots, an area, a point, a set or a transition.
        zone: Name of an area the host defines, such as a pit or wings, when the pointer is one;
            the library keeps no zones and passes the name through.
        ttl_s: Seconds the pointer stays visible.
    """

    anchor: Anchor | None = None
    zone: str | None = None
    ttl_s: Annotated[float, Field(gt=0)]

    @model_validator(mode="after")
    def _require_one_target(self) -> "Highlight":
        if (self.anchor is None) == (self.zone is None):
            raise SchemaError(
                "highlight needs exactly one of anchor and zone",
                hint="set `anchor` for spots, an area, a point, a set or a transition, "
                "or `zone` for the name of an area the host defines",
            )
        return self


type EventPayload = PlacementOp | FormOp | PathsOp | CountsOp | Pin | Comment | Highlight

EVENT_PAYLOADS: Final[Mapping[str, type[EventPayload]]] = ReadOnlyMap(
    {
        "move": PlacementOp,
        "edit_form": FormOp,
        "edit_paths": PathsOp,
        "set_counts": CountsOp,
        "pin": Pin,
        "unpin": Pin,
        "comment": Comment,
        "highlight": Highlight,
    }
)


class CanvasEvent(FrozenModel):
    """Records one edit or pointer from either side of the canvas.

    The payload type follows the kind: `move` carries a `PlacementOp`, `edit_form` a `FormOp`,
    `edit_paths` a `PathsOp`, `set_counts` a `CountsOp`, `pin` and `unpin` a `Pin`, `comment` a
    `Comment` and `highlight` a `Highlight`. A payload given as a dict is validated as the
    model its kind names; a payload of another kind's model is refused.

    Attributes:
        id: Event ID.
        at: When it happened.
        kind: What happened.
        payload: Typed body for the kind.
        revision: Revision that holds the edit; empty for pointers such as highlights.
    """

    id: str
    at: datetime
    kind: EventKind
    payload: EventPayload
    revision: str = ""

    @model_validator(mode="before")
    @classmethod
    def _type_payload(cls, data: object) -> object:
        if not isinstance(data, Mapping):
            return data
        kind, payload = data.get("kind"), data.get("payload")
        model = EVENT_PAYLOADS.get(kind) if isinstance(kind, str) else None
        if model is None or not isinstance(payload, Mapping):
            return data
        return {**data, "payload": model._validated(payload)}

    @model_validator(mode="after")
    def _require_payload_of_kind(self) -> "CanvasEvent":
        expected = EVENT_PAYLOADS[self.kind]
        if type(self.payload) is not expected:
            raise SchemaError(
                f"event {self.id} of kind {self.kind} carries a {type(self.payload).__name__}",
                hint=f"give a {expected.__name__} payload for kind {self.kind}",
                context={"event": self.id},
            )
        return self


class Proposal(FrozenModel):
    """Holds an agent suggestion; the designer accepts or rejects it.

    Attributes:
        id: Proposal ID.
        base_revision: Revision the change was made against.
        change: Ops applied in order when accepted.
        note: Why the agent suggests it.
        state: open, accepted, rejected or withdrawn.
        reason: Why it was rejected.
        accepted_revision: Revision that holds the accepted change; empty until accepted.
    """

    id: str
    base_revision: str
    change: tuple[ChangeOp, ...]
    note: str = ""
    state: ProposalState = "open"
    reason: str = ""
    accepted_revision: str = ""


class ProposalPreview(FrozenModel):
    """Holds what a proposal would produce, for drawing ghosts on the canvas.

    Attributes:
        proposal_id: Proposal previewed.
        placements: Set ID to the placements that would change in that set.
        paths: Transitions whose effective paths would change, as they would be.
        counts: Set ID to new counts.
        forms: Forms that would be added or changed.
        blocked: Pinned or locked items the proposal touches; accepting it would fail.
    """

    proposal_id: str
    placements: dict[SetId, tuple[Placement, ...]] = {}
    paths: tuple[TransitionSpec, ...] = ()
    counts: dict[SetId, float] = {}
    forms: tuple[Form, ...] = ()
    blocked: tuple[str, ...] = ()
