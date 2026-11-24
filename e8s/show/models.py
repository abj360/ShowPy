"""
models.py --- the Show aggregate and its index

Contains:
    Pin: records one designer lock on a spot, form, set or transition
    Show: holds a whole show: sheet, cast, timeline, sets, forms and paths
    ShowLookup: looks up a show's sets, transitions and forms by ID without scanning
    Show.lookup: returns the show's cached ShowLookup
    Show.set(): returns a set by ID
    Show.set_index(): returns a set's position in the show
    Show.set_ids(): returns set IDs in show order
    Show.transition_ids(): returns consecutive set pairs
    Show.transition_id(): resolves a pair of set IDs to the show's transition
    Show.transition(): returns a transition's paths, defaulting to straight moves
    ShowIndexEntry: holds one set's place on the clock: counts, start, measure, seconds, cue
    ShowIndex: looks up sets and transitions by global count
    require_set_count(): refuses a show size over MAX_SETS before anything is allocated
"""

import bisect
import itertools
import json
import re
from collections import Counter
from collections.abc import Mapping, Sized
from functools import cached_property
from typing import Annotated, ClassVar, Literal

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    ValidationInfo,
    field_validator,
    model_serializer,
    model_validator,
)

from e8s.cast.models import Cast
from e8s.choreo.form_models import Form
from e8s.choreo.models import Set
from e8s.choreo.path_models import FacingOverride, TransitionSpec
from e8s.core.caps import MAX_EXTENSION_BYTES, MAX_SETS
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, TimeRangeError, UnknownRefError
from e8s.core.ids import (
    LEGACY_IDS_KEY,
    FormId,
    FormIdField,
    SetId,
    TransitionId,
)
from e8s.core.ids import format_tid as _format_tid
from e8s.core.json_types import FrozenJsonValue, JsonValue
from e8s.core.readonly import ReadOnly, ReadOnlyMap
from e8s.core.types import Count, FrozenModel, JsonObject, Seconds
from e8s.core.util import close_matches
from e8s.sheet.models import Sheet
from e8s.time.models import MeasureLabel, Timeline

INDEX_TOL = 1e-9
EXTENSION_KEY = re.compile(r"^x-")
EXTENSION_PREFIX = "x-"
EXTENSIONS_FIELD = "extensions"


type PinTarget = Literal["entity", "form", "set", "transition"]


class Pin(FrozenModel):
    """Records one designer lock; the flags it sets live on placements, forms and path rows.

    Attributes:
        target: What is locked.
        id: Spot, form or set ID, or a transition written as `a→b`.
        by: Who pinned it.
        reason: Why, shown to the agent when an edit is refused.
    """

    target: PinTarget
    id: str
    by: str = "designer"
    reason: str = ""


class ShowLookup(FrozenModel):
    """Looks up a show's sets, transitions and forms by ID; built once per Show.

    Attributes:
        positions: Set ID to its position in show order.
        consecutive: Each consecutive set pair to the show's own TransitionId for it.
        stored: TransitionId to its stored spec; the first spec wins when IDs repeat.
        forms: Form ID to form.
    """

    positions: Annotated[Mapping[SetId, int], ReadOnly()]
    consecutive: Annotated[Mapping[TransitionId, TransitionId], ReadOnly()]
    stored: Annotated[Mapping[TransitionId, TransitionSpec], ReadOnly()]
    forms: Annotated[Mapping[FormId, Form], ReadOnly()]
    _json_refusal: ClassVar[str | None] = (
        "a lookup is derived from its show; store the Show and read show.lookup after loading it"
    )


def _no_forms() -> ReadOnlyMap[FormId, Form]:
    return ReadOnlyMap({})


def _no_extensions() -> ReadOnlyMap[str, FrozenJsonValue]:
    return ReadOnlyMap({})


def require_extension_size(size: int) -> None:
    """Raises SchemaError when extension values would take more than MAX_EXTENSION_BYTES."""
    if size <= MAX_EXTENSION_BYTES:
        return
    raise SchemaError(
        f"extensions take {size} bytes as JSON, over the cap of {MAX_EXTENSION_BYTES}",
        hint=f"keep host values under {MAX_EXTENSION_BYTES} bytes; store large data as an"
        " asset and keep its URI here",
        context={"bytes": size, "max_bytes": MAX_EXTENSION_BYTES},
    )


def _extension_bytes(extensions: Mapping[str, JsonValue | FrozenJsonValue]) -> int:
    """Measures extension values as UTF-8 JSON; NaN and infinity raise SchemaError."""
    try:
        return len(
            json.dumps(extensions, default=dict, allow_nan=False, ensure_ascii=False).encode(
                "utf-8"
            )
        )
    except (ValueError, UnicodeEncodeError) as exc:
        raise SchemaError(
            f"extensions are not strict JSON: {exc}",
            hint="use finite numbers and valid Unicode text in extension values",
            context={"problem": type(exc).__name__},
        ) from exc


def _no_dependencies() -> ReadOnlyMap[str, str]:
    return ReadOnlyMap({})


def require_set_count(count: int) -> None:
    """Refuses a show of more than MAX_SETS sets, before the caller allocates anything.

    Args:
        count: Number of sets the show holds, or would hold after an edit or a decode.

    >>> require_set_count(MAX_SETS)
    >>> require_set_count(MAX_SETS + 1)
    Traceback (most recent call last):
        ...
    e8s.core.errors.SchemaError: TOO_MANY_SETS: a show of 1001 sets is over MAX_SETS (1,000) \
(hint: keep at most 1,000 sets in one show; split a longer show into several shows)
    """
    if count <= MAX_SETS:
        return
    raise SchemaError(
        f"a show of {count} sets is over MAX_SETS ({MAX_SETS:,})",
        hint=f"keep at most {MAX_SETS:,} sets in one show; split a longer show into several shows",
        context={"sets": count, "max_sets": MAX_SETS},
        code=ErrorCode.TOO_MANY_SETS,
    )


class Show(FrozenModel):
    """Holds a whole show.

    Transitions are stored sparsely: a consecutive pair without a stored spec moves every
    spot straight over the destination set's counts.

    Attributes:
        id: Show ID.
        title: Display title.
        pack: Domain pack key; empty when no pack applies.
        sheet: The 2D design surface.
        cast: Groups and spots.
        timeline: Show clock.
        sets: Sets in show order; at most MAX_SETS.
        forms: Form ID to form, across every set.
        transitions: Stored path instructions for consecutive set pairs.
        facing_overrides: Facings forced over global count ranges.
        dependencies: Sheet reference (without version) to pinned version.
        pins: Designer locks with their reasons.
        extensions: Host-owned JSON values under keys that start with `x-`; the library keeps them
            through every save, load and export and never reads them. Together they take at most
            `MAX_EXTENSION_BYTES` bytes as JSON.
        revision: Storage revision this show was loaded at.
    """

    id: str
    title: str
    pack: str = ""
    sheet: Sheet
    cast: Cast = Cast()
    timeline: Timeline
    sets: tuple[Set, ...] = Field(default=(), json_schema_extra={"maxItems": MAX_SETS})
    forms: Annotated[Mapping[FormIdField, Form], ReadOnly()] = Field(default_factory=_no_forms)
    transitions: tuple[TransitionSpec, ...] = ()
    facing_overrides: tuple[FacingOverride, ...] = ()
    dependencies: Annotated[Mapping[str, str], ReadOnly()] = Field(default_factory=_no_dependencies)
    pins: tuple[Pin, ...] = ()
    extensions: JsonObject = Field(
        default_factory=_no_extensions,
        json_schema_extra={"propertyNames": {"pattern": EXTENSION_KEY.pattern}},
    )
    revision: str = ""

    @field_validator("sets", mode="before")
    @classmethod
    def _set_count_within_cap(cls, value: object) -> object:
        if isinstance(value, Sized):
            require_set_count(len(value))
        return value

    @field_validator("sets", mode="after")
    @classmethod
    def _sets_within_cap(cls, value: tuple[Set, ...]) -> tuple[Set, ...]:
        require_set_count(len(value))
        return value

    @model_validator(mode="after")
    def _form_keys_match_ids(self) -> "Show":
        wrong = {key: form.id for key, form in self.forms.items() if key != form.id}
        if wrong:
            raise SchemaError(
                f"form keys differ from form ids: {', '.join(sorted(wrong))}",
                hint="store every form under its own id",
                context={"forms": wrong},
            )
        return self

    @model_validator(mode="after")
    def _extensions_are_host_keys_within_cap(self) -> "Show":
        stray = sorted(key for key in self.extensions if not EXTENSION_KEY.match(key))
        if stray:
            raise SchemaError(
                f"extension keys must start with {EXTENSION_PREFIX!r}: {', '.join(stray)}",
                hint=f"rename the key to {EXTENSION_PREFIX}<name>",
                context={"keys": stray},
            )
        require_extension_size(_extension_bytes(self.extensions))
        return self

    # The dump omits an empty map, so stored documents of shows without extensions keep their bytes.
    @model_serializer(mode="wrap")
    def _omit_empty_extensions(self, handler: SerializerFunctionWrapHandler) -> object:
        dumped: dict[str, JsonValue] = handler(self)
        if not self.extensions:
            dumped.pop(EXTENSIONS_FIELD, None)
        return dumped

    @model_validator(mode="after")
    def _unique_set_ids(self, info: ValidationInfo) -> "Show":
        ids = self.set_ids()
        if len(set(ids)) != len(ids):
            raise SchemaError("set ids repeat", hint="give every set a unique id")
        if isinstance(info.context, Mapping) and info.context.get(LEGACY_IDS_KEY) is True:
            return self
        keys = [s.key or s.id for s in self.sets]
        folded = Counter(k.casefold() for k in keys)
        repeated = sorted({k for k in keys if folded[k.casefold()] > 1})
        if repeated:
            raise SchemaError(
                f"set keys repeat ignoring case: {', '.join(repeated)}",
                hint="give every set a key unique ignoring case; add_set() picks a free one",
                context={"keys": repeated},
            )
        return self

    def set_ids(self) -> list[SetId]:
        """Returns set IDs in show order."""
        return [s.id for s in self.sets]

    @cached_property
    def lookup(self) -> ShowLookup:
        """Returns the show's lookup tables, built on first use and dropped by every copy."""
        ids = self.set_ids()
        positions: dict[SetId, int] = {}
        for position, set_id in enumerate(ids):
            positions.setdefault(set_id, position)
        stored: dict[TransitionId, TransitionSpec] = {}
        for spec in self.transitions:
            stored.setdefault(spec.id, spec)
        pairs = [TransitionId(a, b) for a, b in itertools.pairwise(ids)]
        return ShowLookup._unchecked(
            positions=ReadOnlyMap(positions),
            consecutive=ReadOnlyMap({pair: pair for pair in pairs}),
            stored=ReadOnlyMap(stored),
            forms=ReadOnlyMap(self.forms),
        )

    def set_index(self, set_id: str) -> int:
        """Returns the position of a set, raising UnknownRefError with suggestions."""
        position = self.lookup.positions.get(SetId(set_id))
        if position is not None:
            return position
        close = close_matches(set_id, self.set_ids())
        raise UnknownRefError(
            f"unknown set {set_id!r}",
            hint=f"did you mean {', '.join(close)}?"
            if close
            else f"known sets: {', '.join(self.set_ids())}",
            context={"set": set_id},
        )

    def set(self, set_id: str) -> Set:
        """Returns a set by ID."""
        return self.sets[self.set_index(set_id)]

    def transition_ids(self) -> list[TransitionId]:
        """Returns consecutive set pairs in show order."""
        return list(self.lookup.consecutive.values())

    def transition_id(self, tid: tuple[str, str]) -> TransitionId:
        """Resolves a pair of set IDs to the show's transition between them.

        Args:
            tid: Pair of set IDs, such as `("1", "2")` or a TransitionId.

        Returns:
            transition_id: The show's own TransitionId for that pair.

        >>> from e8s.examples import two_set_show
        >>> str(two_set_show().transition_id(("1", "2")))
        '1→2'
        """
        known = self.lookup.consecutive.get(TransitionId(SetId(tid[0]), SetId(tid[1])))
        if known is not None:
            return known
        raise UnknownRefError(
            f"{_format_tid(tid)} is not a transition between consecutive sets",
            hint="use a pair from show.transition_ids()",
            context={"tid": tid},
        )

    def transition(self, tid: tuple[str, str]) -> TransitionSpec:
        """Returns stored paths for a consecutive pair, or straight moves when none are stored."""
        known = self.transition_id(tid)
        spec = self.lookup.stored.get(known)
        if spec is not None:
            return spec
        return TransitionSpec(id=known, counts=self.set(known.to_set).counts)


class ShowIndexEntry(FrozenModel):
    """Holds one set's place on the show clock.

    Attributes:
        set: Set ID.
        counts: Counts of the move into the set.
        start_count: Global count where the set is reached.
        measure: Measure label of the start count; None when measures are off.
        seconds: Show seconds of the start count, holds included.
        cue: The set's cue or rehearsal mark.
    """

    set: SetId
    counts: float
    start_count: Count
    measure: MeasureLabel | None = None
    seconds: Seconds
    cue: str | None = None


class ShowIndex(FrozenModel):
    """Looks up sets and transitions by global count.

    Count `c` is AT set `s` when `c` equals the start count of `s`; it lies IN transition
    `(a, b)` when `start(a) < c < start(b)`. Exactly one of the two holds on `[0, total]`.

    Attributes:
        entries: One entry per set, in show order.
    """

    entries: tuple[ShowIndexEntry, ...]

    @property
    def total_counts(self) -> Count:
        """Returns the global count of the last set; 0 for an empty show."""
        return self.entries[-1].start_count if self.entries else 0.0

    def _starts(self) -> list[Count]:
        return [entry.start_count for entry in self.entries]

    def _require_in_show(self, count: Count) -> None:
        if not self.entries or count < -INDEX_TOL or count > self.total_counts + INDEX_TOL:
            raise TimeRangeError(
                f"count {count} is outside the show",
                hint=f"use a count from 0 to {self.total_counts}",
                context={"count": count, "total": self.total_counts},
            )

    def set_at(self, count: Count) -> SetId | None:
        """Returns the set reached exactly at `count`, or None while moving between sets.

        >>> rows = (("1", 0.0), ("2", 8.0))
        >>> idx = ShowIndex(entries=tuple(
        ...     ShowIndexEntry(set=i, counts=s, start_count=s, seconds=0) for i, s in rows))
        >>> idx.set_at(8.0), idx.set_at(3.0)
        ('2', None)
        """
        self._require_in_show(count)
        starts = self._starts()
        position = bisect.bisect_left(starts, count - INDEX_TOL)
        if position < len(starts) and abs(starts[position] - count) <= INDEX_TOL:
            return self.entries[position].set
        return None

    def transition_at(self, count: Count) -> tuple[TransitionId, Count] | None:
        """Returns the transition `count` lies in plus its local count tau, or None at a set.

        >>> rows = (("1", 0.0), ("2", 8.0))
        >>> idx = ShowIndex(entries=tuple(
        ...     ShowIndexEntry(set=i, counts=s, start_count=s, seconds=0) for i, s in rows))
        >>> idx.transition_at(3.0)
        (TransitionId(from_set='1', to_set='2'), 3.0)
        """
        if self.set_at(count) is not None:
            return None
        position = bisect.bisect_right(self._starts(), count) - 1
        before, after = self.entries[position], self.entries[position + 1]
        return TransitionId(before.set, after.set), count - before.start_count

    def _position(self, set_id: str) -> int:
        ids: list[str] = [entry.set for entry in self.entries]
        if set_id not in ids:
            close = close_matches(set_id, ids)
            raise UnknownRefError(
                f"unknown set {set_id!r}",
                hint=f"did you mean {', '.join(close)}?" if close else "check the set id",
                context={"set": set_id},
            )
        return ids.index(set_id)

    def next(self, set_id: str) -> SetId | None:
        """Returns the set after `set_id`, or None for the last set."""
        position = self._position(set_id) + 1
        return self.entries[position].set if position < len(self.entries) else None

    def prev(self, set_id: str) -> SetId | None:
        """Returns the set before `set_id`, or None for the first set."""
        position = self._position(set_id) - 1
        return self.entries[position].set if position >= 0 else None
