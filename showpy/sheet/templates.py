"""
templates.py --- built-in sheet templates, sheet lookup and storage, customization and edits

Every template puts (0, 0) at the front-center: the front sideline or downstage edge is y = 0
and x runs from -width/2 (audience left) to +width/2. The surfaces themselves are built in
showpy.sheet.surfaces.

Contains:
    SheetScope: names who a saved sheet is shared with
    SheetStore: describes the injected storage that saved sheets live in
    InMemorySheetStore: keeps saved sheets in memory, for tests and local sessions
    sheet_templates(): lists the built-in templates, optionally of one surface type
    get_sheet(): resolves a reference against the templates, then a store
    blank_sheet(): builds a plain rectangle for unusual spaces
    customize(): changes a sheet's name, grid, or size (blank sheets only)
    add_zone(): returns a sheet with one more zone
    add_landmark(): returns a sheet with one more landmark
    add_guide(): returns a sheet with one more guide line
    remove_zone(): returns a sheet without a zone
    remove_landmark(): returns a sheet without a landmark
    remove_guide(): returns a sheet without a guide line
    save_sheet(): saves a sheet under the next free version and returns its reference
"""

import functools
from collections.abc import Sequence
from typing import Literal, Protocol

from showpy.core.errors import (
    ConflictError,
    SchemaError,
    StorageError,
    UnknownRefError,
    VersionError,
)
from showpy.core.ids import SheetRef, format_ref, parse_ref
from showpy.core.types import Region
from showpy.core.units import convert
from showpy.core.util import close_matches, natural_sort
from showpy.sheet.models import (
    CoordinateSystem,
    Grid,
    GuideLine,
    Landmark,
    Sheet,
    SurfaceType,
    Zone,
)
from showpy.sheet.surfaces import (
    NCAA_HASH,
    NFHS_HASH,
    NFL_HASH,
    football_field,
    gym_floor,
    point_landmark,
    proscenium_stage,
    rect_ring,
    template_ref,
)

type SheetScope = Literal["mine", "team"]
type SheetItem = Zone | Landmark | GuideLine

FIRST_VERSION = "1"
MAX_SAVE_ATTEMPTS = 10_000
CUSTOMIZABLE = ("name", "grid", "size")
SHEET_SCOPES = ("mine", "team")

BLANK_SIZE_M = (20.0, 15.0)


class SheetStore(Protocol):
    """Describes the storage saved sheets live in; injected, never global."""

    def get(self, ref: SheetRef) -> Sheet | None:
        """Returns the sheet at `ref` (the latest version when it has none), or None."""
        ...

    def put(self, sheet: Sheet, scope: SheetScope) -> SheetRef:
        """Stores a sheet under its own reference and returns that reference."""
        ...


class InMemorySheetStore:
    """Keeps saved sheets in memory; saving is a side effect, so this object is mutable.

    A reference names one immutable version: putting the same sheet twice is a no-op, and
    putting different content under a used reference raises ConflictError.

    Attributes:
        sheets: Reference to saved sheet.
        scopes: Reference to the scope it was saved with.
    """

    def __init__(self) -> None:
        self.sheets: dict[SheetRef, Sheet] = {}
        self.scopes: dict[SheetRef, SheetScope] = {}

    def get(self, ref: SheetRef) -> Sheet | None:
        """Returns the sheet at `ref`, or its latest version when `ref` has no version."""
        kind, ref_id, version = parse_ref(ref)
        if version is not None:
            return self.sheets.get(ref)
        versions = [r for r in self.sheets if parse_ref(r)[:2] == (kind, ref_id)]
        return self.sheets[natural_sort(versions)[-1]] if versions else None

    def put(self, sheet: Sheet, scope: SheetScope) -> SheetRef:
        """Stores a sheet under its reference; identical re-puts are idempotent."""
        existing = self.sheets.get(sheet.ref)
        if existing is not None and existing != sheet:
            raise ConflictError(
                f"{sheet.ref} already holds a different sheet",
                hint="save through save_sheet(), which picks the next free version",
                context={"ref": sheet.ref},
            )
        self.sheets[sheet.ref] = sheet
        self.scopes[sheet.ref] = scope
        return sheet.ref


@functools.cache
def _built_in() -> tuple[Sheet, ...]:
    return (
        blank_sheet(*BLANK_SIZE_M),
        football_field("hs-football", "High school football field (NFHS)", NFHS_HASH),
        football_field("college-football", "College football field (NCAA)", NCAA_HASH),
        football_field("nfl-football", "NFL football field", NFL_HASH),
        proscenium_stage(),
        gym_floor(),
    )


def sheet_templates(surface_type: SurfaceType | None = None) -> list[Sheet]:
    """Lists the built-in sheet templates, optionally only those of one surface type.

    >>> [s.ref for s in sheet_templates(SurfaceType.STAGE)]
    ['sheet:proscenium-stage@1']
    """
    return [s for s in _built_in() if surface_type is None or s.surface_type == surface_type]


def get_sheet(ref: SheetRef, store: SheetStore | None = None) -> Sheet:
    """Resolves a sheet reference against the templates first, then the store.

    Args:
        ref: `sheet:<id>@<version>`; without a version, the template or latest saved version.
        store: Where saved sheets live; None searches the templates only.

    Returns:
        sheet: The resolved sheet.

    >>> get_sheet("sheet:nfl-football").name
    'NFL football field'
    """
    kind, ref_id, version = parse_ref(ref)
    for template in _built_in():
        t_kind, t_id, t_version = parse_ref(template.ref)
        if (t_kind, t_id) == (kind, ref_id) and version in (None, t_version):
            return template
    found = store.get(ref) if store is not None else None
    if found is not None:
        return found
    known = [parse_ref(t.ref)[1] for t in _built_in()]
    close = close_matches(ref_id, known)
    raise UnknownRefError(
        f"unknown sheet {ref!r}",
        hint=f"did you mean {', '.join(close)}?" if close else f"templates: {', '.join(known)}",
        context={"ref": ref, "searched_store": store is not None},
    )


def blank_sheet(
    width_m: float, depth_m: float, grid_spacing: float = 1.0, units: str = "m"
) -> Sheet:
    """Builds a plain rectangle with a center line, for spaces no template fits.

    Args:
        width_m: Width in meters, along x.
        depth_m: Depth in meters, away from the audience.
        grid_spacing: Grid cell size, in `units`.
        units: Unit the grid and default coordinates are labeled in.

    >>> blank_sheet(10.0, 6.0).ref
    'sheet:blank-10x6@1'
    """
    if not (width_m > 0 and depth_m > 0 and grid_spacing > 0):
        raise SchemaError(
            f"blank sheet needs positive sizes, got {width_m} x {depth_m}, grid {grid_spacing}",
            hint="pass width_m, depth_m and grid_spacing above 0",
            context={"width_m": width_m, "depth_m": depth_m, "grid_spacing": grid_spacing},
        )
    spacing = convert(grid_spacing, units, "m")
    half = width_m / 2
    ring = rect_ring(-half, 0.0, half, depth_m)
    return Sheet(
        ref=template_ref(f"blank-{width_m:g}x{depth_m:g}"),
        name=f"Blank {width_m:g} x {depth_m:g} m",
        surface_type=SurfaceType.BLANK,
        size_m=(width_m, depth_m),
        outline=ring,
        walkable=Region(exterior=ring),
        guides=(
            GuideLine(id="edge-front", kind="edge", points=((-half, 0.0), (half, 0.0))),
            GuideLine(id="center-line", kind="center", points=((0.0, 0.0), (0.0, depth_m))),
        ),
        landmarks=(
            point_landmark("front-center", "front center", (0.0, 0.0)),
            point_landmark("center", "center", (0.0, depth_m / 2)),
        ),
        grid=Grid(spacing=(spacing, spacing), unit_name=units),
        default_coordinates=CoordinateSystem(units=units),
    )


def _merge_built_ins[T: SheetItem](fresh: tuple[T, ...], old: tuple[T, ...]) -> tuple[T, ...]:
    fresh_ids = {item.id for item in fresh}
    return (*fresh, *(item for item in old if item.id not in fresh_ids))


def _resized(sheet: Sheet, size: object) -> Sheet:
    if sheet.surface_type != SurfaceType.BLANK:
        raise SchemaError(
            f"{sheet.ref} is a {sheet.surface_type} sheet; only blank sheets resize",
            hint="pick a template of the right size, or start from blank_sheet()",
            context={"ref": sheet.ref},
        )
    if not (isinstance(size, Sequence) and len(size) == 2):
        raise SchemaError("size must be (width_m, depth_m)", hint="pass size=(20.0, 15.0)")
    fresh = blank_sheet(float(size[0]), float(size[1]))
    return sheet.replace(
        size_m=fresh.size_m,
        outline=fresh.outline,
        walkable=fresh.walkable,
        guides=_merge_built_ins(fresh.guides, sheet.guides),
        landmarks=_merge_built_ins(fresh.landmarks, sheet.landmarks),
    )


def customize(sheet: Sheet, **changes: object) -> Sheet:
    """Changes a sheet's name, grid, or size; resizing applies to blank sheets only.

    Resizing regenerates the outline, walkable area and the blank sheet's own guides and
    landmarks; zones and items the designer added stay.

    >>> customize(blank_sheet(10.0, 6.0), size=(12.0, 6.0)).size_m
    (12.0, 6.0)
    """
    unknown = sorted(set(changes) - set(CUSTOMIZABLE))
    if unknown:
        raise SchemaError(
            f"cannot customize {', '.join(unknown)}",
            hint=f"customize accepts {', '.join(CUSTOMIZABLE)}",
            context={"fields": unknown},
        )
    result = _resized(sheet, changes["size"]) if "size" in changes else sheet
    update = {key: changes[key] for key in ("name", "grid") if key in changes}
    return Sheet.model_validate({**result.model_dump(), **update})


def _ids(items: tuple[SheetItem, ...]) -> list[str]:
    return [item.id for item in items]


def _add(sheet: Sheet, field: str, item: SheetItem) -> Sheet:
    items: tuple[SheetItem, ...] = getattr(sheet, field)
    if item.id in _ids(items):
        raise SchemaError(
            f"{sheet.ref} already has a {field[:-1]} {item.id!r}",
            hint=f"pick an unused id or remove_{field[:-1]}() first",
            context={"ref": sheet.ref, "id": item.id},
        )
    return sheet.replace(**{field: (*items, item)})


def _remove(sheet: Sheet, field: str, item_id: str) -> Sheet:
    items: tuple[SheetItem, ...] = getattr(sheet, field)
    if item_id not in _ids(items):
        close = close_matches(item_id, _ids(items))
        raise UnknownRefError(
            f"{sheet.ref} has no {field[:-1]} {item_id!r}",
            hint=f"did you mean {', '.join(close)}?" if close else f"known: {_ids(items)}",
            context={"ref": sheet.ref, "id": item_id},
        )
    return sheet.replace(**{field: tuple(i for i in items if i.id != item_id)})


def add_zone(sheet: Sheet, item: Zone) -> Sheet:
    """Returns a sheet with one more zone, such as a pit or props area.

    >>> from showpy.sheet.models import ZoneKind
    >>> box = Region(exterior=((0, 0), (1, 0), (1, 1)))
    >>> len(add_zone(blank_sheet(4, 4), Zone(id="pit", kind=ZoneKind.PIT, polygon=box)).zones)
    1
    """
    return _add(sheet, "zones", item)


def add_landmark(sheet: Sheet, item: Landmark) -> Sheet:
    """Returns a sheet with one more landmark.

    >>> mark = Landmark(id="prop", name="prop table", kind="point", geometry=((1.0, 1.0),))
    >>> add_landmark(blank_sheet(4, 4), mark).landmarks[-1].name
    'prop table'
    """
    return _add(sheet, "landmarks", item)


def add_guide(sheet: Sheet, item: GuideLine) -> Sheet:
    """Returns a sheet with one more guide line.

    >>> tape = GuideLine(id="tape", kind="custom", points=((0.0, 1.0), (1.0, 1.0)))
    >>> add_guide(blank_sheet(4, 4), tape).guides[-1].id
    'tape'
    """
    return _add(sheet, "guides", item)


def remove_zone(sheet: Sheet, zone_id: str) -> Sheet:
    """Returns a sheet without the zone `zone_id`.

    >>> remove_zone(get_sheet("sheet:proscenium-stage"), "wing-sr").zones[0].id
    'wing-sl'
    """
    return _remove(sheet, "zones", zone_id)


def remove_landmark(sheet: Sheet, landmark_id: str) -> Sheet:
    """Returns a sheet without the landmark `landmark_id`.

    >>> [lm.id for lm in remove_landmark(blank_sheet(4, 4), "center").landmarks]
    ['front-center']
    """
    return _remove(sheet, "landmarks", landmark_id)


def remove_guide(sheet: Sheet, guide_id: str) -> Sheet:
    """Returns a sheet without the guide line `guide_id`.

    >>> [g.id for g in remove_guide(blank_sheet(4, 4), "edge-front").guides]
    ['center-line']
    """
    return _remove(sheet, "guides", guide_id)


def _version_number(ref: SheetRef) -> int:
    version = parse_ref(ref)[2] or FIRST_VERSION
    if not version.isdigit():
        raise VersionError(
            f"cannot bump non-numeric version {version!r} of {ref}",
            hint="save sheets whose reference ends in @<number>",
            context={"ref": ref},
        )
    return int(version)


def save_sheet(store: SheetStore, sheet: Sheet, scope: SheetScope = "mine") -> SheetRef:
    """Saves a sheet under the next free version, recording the version it came from.

    Saving identical content again returns the existing reference instead of a new one.

    Args:
        store: Where saved sheets live.
        sheet: The sheet to keep, e.g. a customized template.
        scope: `mine` or `team`.

    Returns:
        ref: Reference of the saved version.

    >>> save_sheet(InMemorySheetStore(), blank_sheet(4, 4), scope="team")
    'sheet:blank-4x4@2'
    """
    if scope not in SHEET_SCOPES:
        raise SchemaError(
            f"unknown scope {scope!r}", hint="use 'mine' or 'team'", context={"scope": scope}
        )
    kind, ref_id, _ = parse_ref(sheet.ref)
    first = _version_number(sheet.ref) + 1
    for number in range(first, first + MAX_SAVE_ATTEMPTS):
        candidate = sheet.replace(ref=format_ref(kind, ref_id, str(number)), based_on=sheet.ref)
        existing = store.get(candidate.ref)
        if existing is None or existing == candidate:
            return store.put(candidate, scope)
    raise StorageError(
        f"no free version of {kind}:{ref_id} within {MAX_SAVE_ATTEMPTS} tries",
        hint="save under a new sheet id",
        context={"ref": sheet.ref},
    )
