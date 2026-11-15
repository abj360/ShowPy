"""
models.py --- export data classes: coordinate sheets, charts, text tables and publications

Contains:
    SheetRow: holds one set on a spot's coordinate sheet
    CoordinateSheet: holds one spot's part through the whole show
    ChartRow: holds one spot on a set chart
    Chart: holds every spot of one set, for the designer
    TextTable: holds a titled table of display strings, shared by CSV, text and PDF output
    Performer: describes a real person who joins through a publication link
    Claim: records a performer on a spot
    Publication: holds one published revision of a show and the claims made on it
    PerformerView: holds what one performer sees: their spot in every set and their path
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from showpy.core.ids import SetId, SpotId
from showpy.core.types import Count, Degrees, FrozenModel, Point
from showpy.time.models import MeasureLabel

type PublicationStatus = Literal["live", "paused", "closed"]
SHA256_HEX = re.compile(r"[0-9a-f]{64}")

type PerformerRole = Literal["performer", "crew"]


class SheetRow(FrozenModel):
    """Holds one set on a spot's coordinate sheet.

    Attributes:
        set: Set ID.
        counts: Counts of the move into this set.
        cumulative: Global count where the set is reached.
        measure: Measure label of that count, when measures are on.
        position_text: Position worded by the show's coordinate system.
        xy: Stage-plane position in meters.
        facing: Facing at the set, in degrees.
        behavior: Terse wording of the move into the set, e.g. `delay 2, move 6`.
        crossings: Labels of guide lines crossed on the way into the set, in crossing order.
        note: The placement's printed note.
    """

    set: SetId
    counts: float
    cumulative: Count
    measure: MeasureLabel | None = None
    position_text: str
    xy: Point
    facing: Degrees
    behavior: str
    crossings: tuple[str, ...] = ()
    note: str | None = None


class CoordinateSheet(FrozenModel):
    """Holds one spot's part through the whole show.

    Attributes:
        entity: Spot ID.
        performer_name: Name of the performer who claimed the spot, once claimed.
        rows: One row per set, in show order.
    """

    entity: SpotId
    performer_name: str | None = None
    rows: tuple[SheetRow, ...] = ()


class ChartRow(FrozenModel):
    """Holds one spot on a set chart.

    Attributes:
        entity: Spot ID.
        group: Key of the spot's group.
        performer_name: Name of the performer on the spot, once claimed.
        position_text: Position worded by the show's coordinate system.
        xy: Stage-plane position in meters.
        facing: Facing at the set, in degrees.
        form: Form the spot belongs to in this set.
        on_field: Whether the spot is on the field in this set.
        note: The placement's printed note.
    """

    entity: SpotId
    group: str
    performer_name: str | None = None
    position_text: str
    xy: Point
    facing: Degrees
    form: str | None = None
    on_field: bool = True
    note: str | None = None


class Chart(FrozenModel):
    """Holds every spot of one set, for the designer.

    Attributes:
        set_id: Set charted.
        rows: One row per spot, in cast order.
    """

    set_id: SetId
    rows: tuple[ChartRow, ...] = ()


class TextTable(FrozenModel):
    """Holds a titled table of display strings, shared by CSV, text and PDF output.

    Attributes:
        title: Heading printed above the table; empty for none.
        header: Column names.
        rows: Cell text, one tuple per row, aligned with the header.
    """

    title: str = ""
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...] = ()


class Performer(FrozenModel):
    """Describes a real person who joins through a publication link.

    Attributes:
        id: Stable performer ID.
        name: Display name.
        contact: How to reach the performer, when given.
        role: `performer` or `crew`; decides which spots the person may claim.
    """

    id: str
    name: str
    contact: str | None = None
    role: PerformerRole = "performer"


class Claim(FrozenModel):
    """Records a performer on a spot.

    Attributes:
        spot: Spot claimed.
        performer: Who holds it.
        claimed_at: When the claim was made.
        locked: True once claimed; only the designer can move a locked claim.
    """

    spot: SpotId
    performer: Performer
    claimed_at: datetime
    locked: bool = True


class Publication(FrozenModel):
    """Holds one published revision of a show and the claims made on it.

    Attributes:
        id: Publication ID, stable across republishing.
        show_id: Show published.
        revision: Show revision the publication shows.
        link: Public link performers open. It carries the join secret as `?t=<token>`: share it
            only with performers and do not persist it where viewers can read it.
        join_token_sha256: SHA-256 hex digest of the join token; hosts verify a join against it
            with `verify_join_token` instead of comparing raw tokens.
        status: `live`, `paused` or `closed`.
        published_at: When this revision was published.
        claims: Claims in natural spot order.
    """

    id: str
    show_id: str
    revision: str
    link: str = Field(repr=False)
    join_token_sha256: str | None = None
    status: PublicationStatus = "live"
    published_at: datetime
    claims: tuple[Claim, ...] = ()

    @field_validator("join_token_sha256")
    @classmethod
    def _check_digest(cls, value: str | None) -> str | None:
        if value is not None and SHA256_HEX.fullmatch(value) is None:
            raise ValueError("join_token_sha256 must be 64 lowercase hex characters")
        return value


class PerformerView(FrozenModel):
    """Holds what one performer sees: their spot in every set and their path.

    Attributes:
        performer: Who is looking.
        spot: Their claimed spot.
        sets: (set ID, position text, counts) for every set, in show order.
        path_animation_url: Link to the animation of the spot's path.
    """

    performer: Performer
    spot: SpotId
    sets: tuple[tuple[SetId, str, float], ...]
    path_animation_url: str
