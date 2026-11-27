"""
ids.py --- ID aliases, checked model ID fields, the ID checks, and reference and transition parsing

Contains:
    SpotIdField, SetIdField, SetKeyField, FormIdField, RouteIdField, NoteIdField,
        SelectionNameField: model field types that run check_id on validation
    ShowIdField: model field type that runs check_sheet_id, because stored show IDs keep an
        inner `-`
    EventIdField: model field type that runs check_sheet_id, because derived event IDs join a
        revision prefix and a number with `-`
    TransitionId: names the transition between two sets; `str()` writes `12→13`
    LEGACY_IDS_KEY: validation context key whose True value lets ID fields take any non-empty text
    parse_ref(): reads `kind:id@version` into its parts, checking the id and version
    format_ref(): writes a reference from its parts
    make_tid(): checks two set IDs and pairs them into a TransitionId
    parse_tid(): reads `12→13` or `12->13` into a TransitionId
    format_tid(): writes a TransitionId as `12→13`, refusing anything but a pair
    ID_PATTERN: matches the one ID shape; check with fullmatch, never match
    SHEET_ID_PATTERN: matches sheet item and sheet reference IDs, which may hold an inner `-`
    ID_MAX_LENGTH: bounds the characters of one ID
    RESERVED_IDS: names no ID may take, compared ignoring case (`all`, `props`, device names);
        device names also bar `con.5`
    WINDOWS_DEVICE_NAMES: names Windows opens as devices
    ID_BODY_CHARS, SHEET_ID_BODY_CHARS: list the characters an ID may hold
    check_id(): validates one ID and returns its NFC-normalized text
    check_sheet_id(): validates one sheet item or sheet reference ID the same way
    is_device_name(): tells whether text is a Windows device name, alone or before a dot
    find_case_clashes(): lists pairs of IDs that differ only by case
"""

import re
from collections.abc import Callable, Mapping
from typing import Annotated, NamedTuple, NewType

from pydantic import AfterValidator, ValidationInfo

from e8s._core.core import (
    ID_MAX_LENGTH as ID_MAX_LENGTH,
)
from e8s._core.core import (
    ID_PATTERN_SOURCE,
    SHEET_ID_PATTERN_SOURCE,
    split_tid,
)
from e8s._core.core import (
    RESERVED_IDS as RESERVED_IDS,
)
from e8s._core.core import check_id as check_id
from e8s._core.core import check_sheet_id as check_sheet_id
from e8s._core.core import find_case_clashes as find_case_clashes
from e8s._core.core import format_ref as format_ref
from e8s._core.core import format_tid as format_tid
from e8s._core.core import is_device_name as is_device_name
from e8s._core.core import parse_ref as parse_ref
from e8s.core.errors import SchemaError

type SheetRef = str
SpotId = NewType("SpotId", str)
SetId = NewType("SetId", str)
SetKey = NewType("SetKey", str)
FormId = NewType("FormId", str)
RouteId = NewType("RouteId", str)
ShowId = NewType("ShowId", str)
NoteId = NewType("NoteId", str)
SelectionName = NewType("SelectionName", str)

ARROW = "→"

ID_PATTERN = re.compile(ID_PATTERN_SOURCE)
SHEET_ID_PATTERN = re.compile(SHEET_ID_PATTERN_SOURCE)
WINDOWS_DEVICE_NAMES = RESERVED_IDS - {"all", "props"}
ID_BODY_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.")
SHEET_ID_BODY_CHARS = ID_BODY_CHARS | {"-"}
LEGACY_IDS_KEY = "legacy_ids"


class TransitionId(NamedTuple):
    """Names the transition from one set to the next; a tuple, so `("1", "2")` compares equal.

    Attributes:
        from_set: The set the transition leaves.
        to_set: The set the transition reaches.

    >>> str(TransitionId("1", "2"))
    '1→2'
    """

    from_set: SetId
    to_set: SetId

    def __str__(self) -> str:
        """Writes the canonical spelling, such as `1→2`."""
        return f"{self.from_set}{ARROW}{self.to_set}"


def make_tid(from_set: str, to_set: str) -> TransitionId:
    """Checks two set IDs with check_id and pairs them into a TransitionId.

    Args:
        from_set: ID of the set the transition leaves.
        to_set: ID of the set the transition reaches.

    Returns:
        tid: The transition ID, each part NFC-normalized.

    >>> str(make_tid("1", "2"))
    '1→2'
    """
    return TransitionId(SetId(check_id(from_set, kind="set")), SetId(check_id(to_set, kind="set")))


def parse_tid(text: str) -> TransitionId:
    """Reads a transition ID written with `→` or `->`; a third part is refused.

    Only the shape is checked: stored pins and paths name sets that may predate the ID rule
    (a v1 set `con`). Use make_tid to check new set IDs.

    >>> parse_tid("12->13")
    TransitionId(from_set='12', to_set='13')
    """
    from_set, to_set = split_tid(text)
    return TransitionId(SetId(from_set), SetId(to_set))


def _allows_legacy_ids(context: object) -> bool:
    """Tells whether a validation context asks ID fields to take any non-empty text."""
    return isinstance(context, Mapping) and context.get(LEGACY_IDS_KEY) is True


def _checked(kind: str, check: Callable[..., str]) -> Callable[[str, ValidationInfo], str]:
    """Builds the field validator that checks one ID kind, or only non-emptiness for legacy IDs.

    Args:
        kind: What the field's ID names, used in the error message.
        check: The ID check the field enforces, `check_id` or `check_sheet_id`.

    Returns:
        validator: A pydantic after-validator returning the NFC-normalized ID.
    """

    def validate(value: str, info: ValidationInfo) -> str:
        if _allows_legacy_ids(info.context):
            if not value:
                raise ValueError(f"empty {kind} id; a stored id needs at least one character")
            return value
        try:
            return check(value, kind=kind)
        except SchemaError as error:
            raise ValueError(f"{error.message}; {error.hint}") from error

    return validate


SpotIdField = Annotated[SpotId, AfterValidator(_checked("spot", check_id))]
SetIdField = Annotated[SetId, AfterValidator(_checked("set", check_id))]
SetKeyField = Annotated[SetKey, AfterValidator(_checked("set key", check_id))]
FormIdField = Annotated[FormId, AfterValidator(_checked("form", check_id))]
RouteIdField = Annotated[RouteId, AfterValidator(_checked("route", check_id))]
# Show IDs name repository folders, and stored shows keep IDs with an inner `-`, so the field
# applies the sheet rule; refusing `-` in new show IDs belongs to the code that creates shows.
ShowIdField = Annotated[ShowId, AfterValidator(_checked("show", check_sheet_id))]
EventIdField = Annotated[str, AfterValidator(_checked("event", check_sheet_id))]
NoteIdField = Annotated[NoteId, AfterValidator(_checked("note", check_id))]
SelectionNameField = Annotated[SelectionName, AfterValidator(_checked("selection", check_id))]
