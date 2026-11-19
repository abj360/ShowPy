"""
ids.py --- ID aliases, checked model ID fields, the ID checks, and reference and transition parsing

Contains:
    SpotIdField, SetIdField, SetKeyField, FormIdField, RouteIdField, ShowIdField, NoteIdField,
        SelectionNameField: model field types that run check_id on validation
    LandmarkIdField, ZoneIdField: model field types that run check_sheet_id on validation
    LEGACY_IDS_KEY: validation context key whose True value lets ID fields take any non-empty text
    parse_ref(): reads `kind:id@version` into its parts, checking the id and version
    format_ref(): writes a reference from its parts
    parse_tid(): reads `12→13` or `12->13` into a TransitionId
    format_tid(): writes a TransitionId as `12→13`, refusing anything but a pair
    ID_PATTERN: matches the one ID shape; check with fullmatch, never match
    SHEET_ID_PATTERN: matches sheet item and sheet reference IDs, which may hold an inner `-`
    ID_MAX_LENGTH: bounds the characters of one ID
    RESERVED_IDS: names no ID may take, compared ignoring case; device names also bar `con.5`
    check_id(): validates one ID and returns its NFC-normalized text
    check_sheet_id(): validates one sheet item or sheet reference ID the same way
    is_device_name(): tells whether text is a Windows device name, alone or before a dot
    find_case_clashes(): lists pairs of IDs that differ only by case
"""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from itertools import combinations
from typing import Annotated

from pydantic import AfterValidator, ValidationInfo

from showpy.core.errors import SchemaError

type SheetRef = str
type SpotId = str
type SetId = str
type SetKey = str
type FormId = str
type RouteId = str
type ZoneId = str
type LandmarkId = str
type TransitionId = tuple[SetId, SetId]
type ShowId = str
type NoteId = str
type SelectionName = str

REF_PATTERN = re.compile(r"^(?P<kind>[A-Za-z][\w-]*):(?P<id>[^@:\s]+)(?:@(?P<version>[^@\s]+))?$")
REF_ID_KIND = "reference"
REF_VERSION_KIND = "reference version"
ARROWS = ("→", "->")
ARROW = "→"
TRANSITION_PARTS = 2

# `$` also matches before a final newline, so `ID_PATTERN.match("a\n")` succeeds: validate with
# check_id or ID_PATTERN.fullmatch, never match or search.
ID_PATTERN = re.compile(r"^[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,62}[A-Za-z0-9_])?$")
SHEET_ID_PATTERN = re.compile(r"^[A-Za-z0-9_](?:[A-Za-z0-9_.\-]{0,62}[A-Za-z0-9_])?$")
ID_MAX_LENGTH = 64
DOUBLE_DOT = ".."
GRAMMAR_KEYWORD = "all"
# Microsoft's "Naming Files, Paths, and Namespaces" lists COM1 to COM9 and LPT1 to LPT9, not
# COM0 or LPT0.
WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{n}" for n in range(1, 10)}
    | {f"lpt{n}" for n in range(1, 10)}
)
RESERVED_IDS = frozenset({GRAMMAR_KEYWORD}) | WINDOWS_DEVICE_NAMES
ID_CONTEXT_TEXT_CHARS = 80
ID_BODY_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.")
SHEET_ID_BODY_CHARS = ID_BODY_CHARS | {"-"}
RESERVED_HINT = "not all or a Windows device name (con, com1, con.txt)"
ID_HINT = (
    f"use 1 to 64 ASCII letters, digits, _ and . matching {ID_PATTERN.pattern}"
    f" (no leading or trailing . and no ..), {RESERVED_HINT}"
)
SHEET_ID_HINT = (
    f"use 1 to 64 ASCII letters, digits, _, . and - matching {SHEET_ID_PATTERN.pattern}"
    f" (no leading or trailing . or - and no ..), {RESERVED_HINT}"
)
LEGACY_IDS_KEY = "legacy_ids"


@dataclass(frozen=True)
class _IdRule:
    """Holds one ID shape: its pattern, its characters and the characters barred at either end.

    Attributes:
        pattern: Full pattern; test it with fullmatch.
        body_chars: Characters allowed anywhere inside the ID.
        edge_chars: Body characters barred as the first or last character.
        hint: What the caller may write instead.
    """

    pattern: re.Pattern[str]
    body_chars: frozenset[str]
    edge_chars: frozenset[str]
    hint: str


_ID_RULE = _IdRule(ID_PATTERN, ID_BODY_CHARS, frozenset("."), ID_HINT)
_SHEET_ID_RULE = _IdRule(SHEET_ID_PATTERN, SHEET_ID_BODY_CHARS, frozenset(".-"), SHEET_ID_HINT)


def parse_ref(text: str) -> tuple[str, str, str | None]:
    """Reads a reference such as `sheet:hs-field@2`.

    The id and version must pass check_sheet_id, so `..`, `/` and spaces never reach a path.

    Args:
        text: Reference text in `kind:id@version` form; the version is optional.

    Returns:
        parts: Kind, id and version (None when absent), the id and version NFC-normalized.

    >>> parse_ref("sheet:gym@1")
    ('sheet', 'gym', '1')
    """
    match = REF_PATTERN.match(text.strip())
    if match is None:
        raise SchemaError(
            f"bad reference {text!r}",
            hint="write references as kind:id or kind:id@version",
            context={"text": text},
        )
    ref_id = check_sheet_id(match["id"], kind=REF_ID_KIND)
    version = match["version"]
    if version is not None:
        version = check_sheet_id(version, kind=REF_VERSION_KIND)
    return match["kind"], ref_id, version


def format_ref(kind: str, ref_id: str, version: str | None = None) -> str:
    """Writes a reference from its parts.

    >>> format_ref("sheet", "gym", "2")
    'sheet:gym@2'
    """
    text = f"{kind}:{ref_id}" if version is None else f"{kind}:{ref_id}@{version}"
    parse_ref(text)
    return text


def parse_tid(text: str) -> TransitionId:
    """Reads a transition ID written with `→` or `->`; a third part is refused.

    >>> parse_tid("12->13")
    ('12', '13')
    """
    normalized = text.replace(ARROWS[1], ARROWS[0])
    parts = [part.strip() for part in normalized.split(ARROW)]
    if len(parts) != TRANSITION_PARTS or not all(parts):
        raise SchemaError(
            f"bad transition id {text!r}",
            hint="write transitions as 12→13 or 12->13",
            context={"text": text},
        )
    return parts[0], parts[1]


def format_tid(tid: TransitionId) -> str:
    """Writes a transition ID as `a→b`, refusing a tuple that is not a pair.

    >>> format_tid(("12", "13"))
    '12→13'
    """
    if len(tid) != TRANSITION_PARTS:
        raise SchemaError(
            f"bad transition id {tid!r}",
            hint="pass a pair of set IDs, such as ('12', '13')",
            context={"parts": len(tid)},
        )
    return f"{tid[0]}{ARROW}{tid[1]}"


def check_id(text: str, *, kind: str) -> str:
    """Validates one ID against the ID pattern and returns its NFC-normalized text.

    Reserved names are rejected, and so is a Windows device name followed by an extension
    (`con.5`), which Windows opens as the device.

    Args:
        text: Candidate ID as typed or stored.
        kind: What the ID names (`spot`, `set`, `form`), used in the message and context.

    Returns:
        normalized_id: The ID after NFC normalization. Store this, not `text`: NFC can turn
            non-ASCII input into a valid ID (the Kelvin sign becomes `K`).

    >>> check_id("12A", kind="set")
    '12A'
    """
    return _checked_text(text, kind, _ID_RULE)


def check_sheet_id(text: str, *, kind: str) -> str:
    """Validates one sheet item or sheet reference ID and returns its NFC-normalized text.

    The rule is check_id's, except that `-` may appear anywhere but the first or last character:
    sheet items and references never enter the selection grammar or a transition folder name.

    Args:
        text: Candidate ID as typed or stored.
        kind: What the ID names (`landmark`, `zone`, `reference`), used in the message.

    Returns:
        normalized_id: The ID after NFC normalization; store this, not `text`.

    >>> check_sheet_id("end-zone-left", kind="landmark")
    'end-zone-left'
    """
    return _checked_text(text, kind, _SHEET_ID_RULE)


def _checked_text(text: str, kind: str, rule: _IdRule) -> str:
    """Returns the NFC-normalized ID when it passes the rule, else raises SchemaError."""
    normalized = text if text.isascii() else unicodedata.normalize("NFC", text)
    if _passes(normalized, rule):
        return normalized
    shown = normalized[:ID_CONTEXT_TEXT_CHARS]
    raise SchemaError(
        f"bad {kind} id {shown!r}",
        hint=rule.hint,
        context={
            "kind": kind,
            "text": shown,
            "position": _first_bad_position(normalized, rule),
            "pattern": rule.pattern.pattern,
        },
    )


def _passes(text: str, rule: _IdRule) -> bool:
    """Tells whether NFC-normalized text passes the rule's pattern, `..` ban and reserved names."""
    return (
        rule.pattern.fullmatch(text) is not None
        and DOUBLE_DOT not in text
        and not _is_reserved(text)
    )


def _first_bad_position(text: str, rule: _IdRule) -> int:
    """Finds the index of the first character that breaks the rule in text that fails it."""
    if not text or text[0] in rule.edge_chars or _is_reserved(text):
        return 0
    candidates = [len(text) - 1] if text[-1] in rule.edge_chars else []
    bad_char = next((i for i, char in enumerate(text) if char not in rule.body_chars), None)
    if bad_char is not None:
        candidates.append(bad_char)
    if len(text) > ID_MAX_LENGTH:
        candidates.append(ID_MAX_LENGTH)
    doubled = text.find(DOUBLE_DOT)
    if doubled != -1:
        candidates.append(doubled + 1)
    return min(candidates)


def is_device_name(text: str) -> bool:
    """Tells whether text is a Windows device name, alone or before a dot, ignoring case.

    Windows opens `con`, and `con.txt` too, as the console device instead of a file or folder.

    Args:
        text: Candidate name, such as an ID that becomes a path segment.

    Returns:
        is_device: True for `con`, `NUL` or `aux.1`; False for `cons`, `com10` or `x.con`.

    >>> is_device_name("COM1.txt"), is_device_name("com10")
    (True, False)
    """
    return text.casefold().partition(".")[0] in WINDOWS_DEVICE_NAMES


def _is_reserved(text: str) -> bool:
    """Tells whether an ID is reserved, or is a Windows device name followed by an extension."""
    return text.casefold() in RESERVED_IDS or is_device_name(text)


def _allows_legacy_ids(context: object) -> bool:
    """Tells whether a validation context asks ID fields to take any non-empty text."""
    return isinstance(context, Mapping) and context.get(LEGACY_IDS_KEY) is True


def _checked(kind: str, rule: _IdRule) -> Callable[[str, ValidationInfo], str]:
    """Builds the field validator that checks one ID kind, or only non-emptiness for legacy IDs.

    Args:
        kind: What the field's ID names, used in the error message.
        rule: The ID shape the field enforces.

    Returns:
        validator: A pydantic after-validator returning the NFC-normalized ID.
    """

    def validate(value: str, info: ValidationInfo) -> str:
        if _allows_legacy_ids(info.context):
            if not value:
                raise ValueError(f"empty {kind} id; a stored id needs at least one character")
            return value
        try:
            return _checked_text(value, kind, rule)
        except SchemaError as error:
            raise ValueError(f"{error.message}; {error.hint}") from error

    return validate


SpotIdField = Annotated[SpotId, AfterValidator(_checked("spot", _ID_RULE))]
SetIdField = Annotated[SetId, AfterValidator(_checked("set", _ID_RULE))]
SetKeyField = Annotated[SetKey, AfterValidator(_checked("set key", _ID_RULE))]
FormIdField = Annotated[FormId, AfterValidator(_checked("form", _ID_RULE))]
RouteIdField = Annotated[RouteId, AfterValidator(_checked("route", _ID_RULE))]
ShowIdField = Annotated[ShowId, AfterValidator(_checked("show", _ID_RULE))]
NoteIdField = Annotated[NoteId, AfterValidator(_checked("note", _ID_RULE))]
SelectionNameField = Annotated[SelectionName, AfterValidator(_checked("selection", _ID_RULE))]
LandmarkIdField = Annotated[LandmarkId, AfterValidator(_checked("landmark", _SHEET_ID_RULE))]
ZoneIdField = Annotated[ZoneId, AfterValidator(_checked("zone", _SHEET_ID_RULE))]


def find_case_clashes(ids: Iterable[str]) -> list[tuple[str, str]]:
    """Lists pairs of distinct IDs that are equal once case is folded.

    Args:
        ids: IDs of one namespace, such as the spots of a cast.

    Returns:
        clashes: Sorted pairs `(a, b)` with `a < b` and `a.casefold() == b.casefold()`.

    >>> find_case_clashes(["T1", "t1", "B2"])
    [('T1', 't1')]
    """
    folded: defaultdict[str, set[str]] = defaultdict(set)
    for item in ids:
        folded[item.casefold()].add(item)
    return sorted(pair for group in folded.values() for pair in combinations(sorted(group), 2))
