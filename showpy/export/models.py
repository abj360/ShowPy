"""
models.py --- export data classes: coordinate sheets, charts, text tables and publications

Contains:
    SheetRow: holds one set on a spot's coordinate sheet
    CoordinateSheet: holds one spot's part through the whole show
    ChartRow: holds one spot on a set chart
    Chart: holds every spot of one set, for the designer
    TextTable: holds a titled table of display strings, shared by CSV, text and PDF output
    JOIN_PARAMETER: names the query parameter that carries the join token in a joinable link
    Performer: describes a real person who joins through a publication link
    Claim: records a performer on a spot
    Publication: holds one published revision of a show and the claims made on it
    PerformerView: holds what one performer sees: their spot in every set and their path
"""

import re
from collections.abc import Mapping
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from showpy.core.errors import SchemaError
from showpy.core.ids import SetId, SpotId
from showpy.core.types import Count, Degrees, FrozenModel, Point
from showpy.time.models import MeasureLabel

type PublicationStatus = Literal["live", "paused", "closed"]
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
JOIN_PARAMETER = "t"
LINK_FIELD = "link"

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
        contact: How to reach the performer. Publications never keep it; the host keeps contacts
            in its own roster keyed by `Performer.id`.
        role: `performer` or `crew`; decides which spots the person may claim.
    """

    id: str
    name: str
    contact: str | None = None
    role: PerformerRole = "performer"


class Claim(FrozenModel):
    """Records a performer on a spot; the performer's contact is dropped on every construction.

    Hosts keep contacts in their own roster keyed by `Performer.id`.

    Attributes:
        spot: Spot claimed.
        performer: Who holds it, without a contact.
        claimed_at: When the claim was made; carries a time zone.
        locked: True once claimed; only the designer can move a locked claim.
    """

    spot: SpotId
    performer: Performer
    claimed_at: AwareDatetime
    locked: bool = True

    @field_validator("performer")
    @classmethod
    def _drop_contact(cls, value: Performer) -> Performer:
        return value if value.contact is None else value.replace(contact=None)


def _without_join_parameter(part: str) -> str:
    head, _, query = part.partition("?")
    kept = [p for p in query.split("&") if p and p.partition("=")[0].lower() != JOIN_PARAMETER]
    return f"{head}{'?' if kept else ''}{'&'.join(kept)}"


def _without_join_token(link: str) -> str:
    # Fragments are stripped too: client-side routers read `#/path?t=` like a query.
    head, hash_mark, fragment = link.partition("#")
    return f"{_without_join_parameter(head)}{hash_mark}{_without_join_parameter(fragment)}"


class Publication(FrozenModel):
    """Holds one published revision of a show and the claims made on it; never the join token.

    Attributes:
        id: Publication ID, stable across republishing.
        show_id: Show published.
        revision: Show revision the publication shows.
        link: Join URL without the token, `<base_url>/<publication id>`; `join_link` adds the
            token. A `t` parameter in a stored link is dropped on load, since a link never carries
            the token; `model_copy(update=)` skips that step, so hosts edit with `replace` instead.
        join_token_sha256: SHA-256 hex digest of the join token; hosts verify a join against it
            with `verify_join_token` instead of comparing raw tokens.
        status: `live`, `paused` or `closed`.
        published_at: When this revision was published; carries a time zone.
        claims: Claims in natural spot order.
    """

    id: str
    show_id: str
    revision: str
    link: str = Field(repr=False)
    join_token_sha256: str | None = None
    status: PublicationStatus = "live"
    published_at: AwareDatetime
    claims: tuple[Claim, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _drop_join_token(cls, data: object) -> object:
        # Runs before field checks, so an error echoing the input record never shows the token.
        if not isinstance(data, Mapping) or LINK_FIELD not in data:
            return data
        link = data[LINK_FIELD]
        if not isinstance(link, str):
            raise SchemaError(
                f"publication link is a {type(link).__name__}, not text",
                hint="store link as text, e.g. https://eight.example/p/pub-1",
                context={"type": type(link).__name__},
            )
        return {**data, LINK_FIELD: _without_join_token(link)}

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
