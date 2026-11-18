"""
ids.py --- ID aliases, the ID check, and sheet-reference and transition-ID parsing

Contains:
    parse_ref(): reads `kind:id@version` into its parts
    format_ref(): writes a reference from its parts
    parse_tid(): reads `12→13` or `12->13` into a TransitionId
    format_tid(): writes a TransitionId as `12→13`
    ID_PATTERN: matches the one ID shape; check with fullmatch, never match
    ID_MAX_LENGTH: bounds the characters of one ID
    RESERVED_IDS: names no ID may take, compared ignoring case; device names also bar `con.5`
    check_id(): validates one ID and returns its NFC-normalized text
    is_device_name(): tells whether text is a Windows device name, alone or before a dot
    find_case_clashes(): lists pairs of IDs that differ only by case
"""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from itertools import combinations

from showpy.core.errors import SchemaError

type SheetRef = str
type SpotId = str
type SetId = str
type TransitionId = tuple[SetId, SetId]
type FormId = str
type RouteId = str
type ZoneId = str
type LandmarkId = str

REF_PATTERN = re.compile(r"^(?P<kind>[A-Za-z][\w-]*):(?P<id>[^@:\s]+)(?:@(?P<version>[^@\s]+))?$")
ARROWS = ("→", "->")
ARROW = "→"

# `$` also matches before a final newline, so `ID_PATTERN.match("a\n")` succeeds: validate with
# check_id or ID_PATTERN.fullmatch, never match or search.
ID_PATTERN = re.compile(r"^[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,62}[A-Za-z0-9_])?$")
ID_MAX_LENGTH = 64
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
ID_HINT = (
    "use 1 to 64 ASCII letters, digits, _ and . (no leading or trailing . and no ..),"
    " not all or a Windows device name (con, com1, con.txt)"
)


def parse_ref(text: str) -> tuple[str, str, str | None]:
    """Reads a reference such as `sheet:hs-field@2`.

    Args:
        text: Reference text in `kind:id@version` form; the version is optional.

    Returns:
        parts: Kind, id and version (None when absent).

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
    return match["kind"], match["id"], match["version"]


def format_ref(kind: str, ref_id: str, version: str | None = None) -> str:
    """Writes a reference from its parts.

    >>> format_ref("sheet", "gym", "2")
    'sheet:gym@2'
    """
    text = f"{kind}:{ref_id}" if version is None else f"{kind}:{ref_id}@{version}"
    parse_ref(text)
    return text


def parse_tid(text: str) -> TransitionId:
    """Reads a transition ID written with `→` or `->`.

    >>> parse_tid("12->13")
    ('12', '13')
    """
    normalized = text.replace(ARROWS[1], ARROWS[0])
    parts = [part.strip() for part in normalized.split(ARROW)]
    if len(parts) != 2 or not all(parts):
        raise SchemaError(
            f"bad transition id {text!r}",
            hint="write transitions as 12→13 or 12->13",
            context={"text": text},
        )
    return parts[0], parts[1]


def format_tid(tid: TransitionId) -> str:
    """Writes a transition ID as `a→b`.

    >>> format_tid(("12", "13"))
    '12→13'
    """
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
    normalized = unicodedata.normalize("NFC", text)
    position = _first_bad_position(normalized)
    if position is None:
        return normalized
    raise SchemaError(
        f"bad {kind} id {normalized[:ID_CONTEXT_TEXT_CHARS]!r}",
        hint=ID_HINT,
        context={
            "kind": kind,
            "text": normalized[:ID_CONTEXT_TEXT_CHARS],
            "position": position,
            "pattern": ID_PATTERN.pattern,
        },
    )


def _first_bad_position(text: str) -> int | None:
    """Finds the index of the first character that breaks the ID rules, or None if none does."""
    for index, char in enumerate(text):
        if char not in ID_BODY_CHARS:
            return index
    if len(text) > ID_MAX_LENGTH:
        return ID_MAX_LENGTH
    doubled = text.find("..")
    if doubled != -1:
        return doubled + 1
    if _is_reserved(text) or ID_PATTERN.fullmatch(text) is None:
        return _edge_position(text)
    return None


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


def _edge_position(text: str) -> int:
    """Finds the index that breaks the pattern or reserved-word rule in an otherwise clean ID."""
    if not text or text.startswith(".") or _is_reserved(text):
        return 0
    return len(text) - 1


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
