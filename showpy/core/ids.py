"""
ids.py --- ID aliases plus parsing and formatting of sheet references and transition IDs

Contains:
    parse_ref(): reads `kind:id@version` into its parts
    format_ref(): writes a reference from its parts
    parse_tid(): reads `12→13` or `12->13` into a TransitionId
    format_tid(): writes a TransitionId as `12→13`
"""

import re

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
