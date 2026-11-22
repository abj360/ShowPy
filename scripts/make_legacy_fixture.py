#!/usr/bin/env python3

"""
make_legacy_fixture.py --- writes a file-repository fixture with the library of a released tag

Run it from a checkout of the tag with that checkout's own environment, so the stored bytes are
the ones that release wrote:

    PYTHONPATH=. uv run --frozen python <repo>/scripts/make_legacy_fixture.py \
        --out <repo>/tests/fixtures/repos/<tag> [--package stage] [--show example|v030]

`--package stage` reads the library under its v0.1 import name; `--show` picks the content:
`seeded` (v0.2.0) and `example` (v0.1.1, which has no seeded generator) save the same three
revisions of edits, and `v030` saves a renumbered show with a text form and a selector zone,
then adds legacy set IDs by hand as v0.2 stored them. Library imports happen inside functions,
after the import name is set up. Models are edited through full validation and expected.json
lists every set sorted, so the oracle does not depend on PYTHONHASHSEED. Only APIs that exist in
every supported release are used; calls whose keywords changed go through `Callable[..., R]`.

Contains:
    oracle_json(): dumps a show as JSON data with every set sorted
    fixture_shows(): builds the shows of the three legacy revisions: the save, a path edit and a pin
    v030_show(): builds the v0.3.0 show: renumbered sets, a text form and a selector zone
    build_fixture(): writes a repository and its expected.json under a directory
    main(): parses the command line and builds the fixture
"""

import argparse
import hashlib
import importlib
import importlib.abc
import importlib.machinery
import inspect
import json
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

if TYPE_CHECKING:
    from showpy.show.file_repository import FileRepository
    from showpy.show.models import Revision, Show

SEED = 7
SPOT_COUNT = 24
SET_COUNT = 6
STAMP = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
SHOW_ID = "legacy-show"
NEGATIVE_ZERO_SET = "1"
TAGGED_SPOT_INDEX = 1
SELECTION_NAME = "front-four"
SELECTION_SIZE = 4
SCRIPT_SET = "2"
SCRIPT_SOURCE = "# v0.2.0 fixture script\nresult = None\n"
PINNED_SPOT_INDEX = 2
PATH_TRANSITION = ("1", "2")
PATH_WAYPOINT_XY = (1.5, 4.0)
EXPECTED_FILE = "expected.json"
REPO_DIR = "repo"
LIBRARY = "showpy"
SHOW_CHOICES = ("seeded", "example", "v030")
INSERTED_SET = "2A"
INSERTED_COUNTS = 8.0
TEXT_SET = "8"
TEXT_COUNTS = 16.0
TEXT_FORM_ID = "title"
TEXT = "GO"
TEXT_FONT = "simplex"
TEXT_HEIGHT_M = 8.0
TEXT_AT = (-10.0, 20.0)
TRUMPETS = "trumpets"
TRUMPET_PREFIX = "T"
TRUMPET_LINE = ((-1.0, 2.0), (1.0, 2.0))
TRUMPET_COUNT = 2
ZONE_ID = "front"
ZONE_SELECTOR = "T1 + T2 & T2"
ZONE_RING = ((-5.0, 0.0), (5.0, 0.0), (5.0, 3.0), (-5.0, 3.0))
HAND_SET_IDS = ("1a", "1A", "con")
HAND_SET_SOURCE = "8"
SHOW_DOC = "/show/show.json"
SET_ORDER_FIELD = "sets"


class _AliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serves `showpy.<name>` as the already-imported module `<package>.<name>`.

    Attributes:
        package: Import name of the library in the checkout, such as `stage`.
    """

    def __init__(self, package: str) -> None:
        self.package = package

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        """Claims every module under the `showpy` name."""
        del path, target
        if fullname != LIBRARY and not fullname.startswith(f"{LIBRARY}."):
            return None
        return importlib.machinery.ModuleSpec(fullname, self)

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType:
        """Returns the real module, so classes keep one identity under both names."""
        return importlib.import_module(self.package + spec.name.removeprefix(LIBRARY))

    def exec_module(self, module: ModuleType) -> None:
        """Leaves the real module as it is; it already ran."""


def _use_package(package: str) -> None:
    """Makes `import showpy...` load the checkout's library when its import name differs."""
    if package != LIBRARY:
        sys.meta_path.insert(0, _AliasFinder(package))


def _validated[M: BaseModel](model: M, **changes: object) -> M:
    """Returns a copy of `model` with `changes`, run through every validator.

    v0.2.0 has no `FrozenModel.replace`, and `model_copy(update=)` skips validation. The legacy
    context lets the current library take the v1 IDs these releases stored; older ones ignore it.
    """
    fields = {name: getattr(model, name) for name in type(model).model_fields}
    return type(model).model_validate(fields | changes, context={"legacy_ids": True})


def _sorted_sets(value: object) -> object:
    """Turns every set inside a model dump into a list sorted by its JSON text."""
    if isinstance(value, set | frozenset):
        items = [_sorted_sets(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(to_jsonable_python(item), sort_keys=True))
    if isinstance(value, dict):
        return {key: _sorted_sets(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_sorted_sets(item) for item in value]
    return value


def oracle_json(show: "Show") -> object:
    """Dumps a show as JSON data with every set sorted, so the oracle ignores the hash seed."""
    return to_jsonable_python(_sorted_sets(show.model_dump()))


def _base_show(kind: str) -> "Show":
    """Builds the seeded show, or the example show where the release has no seeded generator."""
    if kind == "example":
        from showpy.examples import two_set_show

        return two_set_show()
    from tests.fixtures.generators import seeded_show

    return seeded_show(seed=SEED, spots=SPOT_COUNT, sets=SET_COUNT)


def _edited_show(kind: str) -> "Show":
    """Builds the base show plus a negative-zero placement, tags and a named selection."""
    show = _base_show(kind)
    spots = list(show.cast.spots)
    spots[TAGGED_SPOT_INDEX] = _validated(
        spots[TAGGED_SPOT_INDEX], tags=frozenset({"lead", "horn"})
    )
    selection = frozenset(s.id for s in spots[:SELECTION_SIZE])
    cast = _validated(show.cast, spots=tuple(spots), selections={SELECTION_NAME: selection})
    first = show.set(NEGATIVE_ZERO_SET)
    placements = list(first.placements)
    placements[0] = _validated(placements[0], x=-0.0)
    edited = _validated(first, placements=tuple(placements))
    sets = tuple(edited if s.id == first.id else s for s in show.sets)
    return _validated(show, cast=cast, sets=sets)


def _path_edit(show: "Show") -> "Show":
    """Gives the first spot a curved row on the first transition."""
    try:
        from showpy.choreo.path_models import PathRow, PathStyle, Waypoint
    except ImportError:
        from showpy.choreo.models import PathRow, PathStyle, Waypoint

    spec = show.transition(PATH_TRANSITION)
    entity = show.cast.spots[0].id
    waypoint = Waypoint(x=PATH_WAYPOINT_XY[0], y=PATH_WAYPOINT_XY[1])
    row = PathRow(entity=entity, style=PathStyle.CURVE, waypoints=(waypoint,))
    edited = _validated(spec, rows={**spec.rows, entity: row})
    others = tuple(t for t in show.transitions if t.id != spec.id)
    return _validated(show, transitions=(*others, edited))


def fixture_shows(kind: str = "seeded") -> tuple["Show", "Show", "Show"]:
    """Builds the shows the three legacy revisions save: the base show, a path edit and a pin.

    Args:
        kind: `seeded` for the seeded show, `example` for the example show.

    Returns:
        shows: The show of each revision, oldest first.
    """
    from showpy.show.pinning import pin

    saved = _edited_show(kind)
    edited = _path_edit(saved)
    pinned = pin(edited, "entity", edited.cast.spots[PINNED_SPOT_INDEX].id, "soloist")
    return saved, edited, pinned


def _with_trumpets_and_zone(show: "Show") -> "Show":
    """Adds two trumpets and a zone whose selector mixes `+` and `&` without parentheses."""
    from showpy.cast.models import Group
    from showpy.cast.spots import add_group, add_spots_along
    from showpy.core.types import Region
    from showpy.geo.shapes import line
    from showpy.sheet.models import Zone, ZoneKind

    show = add_group(show, Group(key=TRUMPETS, label_prefix=TRUMPET_PREFIX))
    show = add_spots_along(show, line(*TRUMPET_LINE), TRUMPET_COUNT, TRUMPETS, NEGATIVE_ZERO_SET)
    zone = Zone.model_validate(
        {
            "id": ZONE_ID,
            "kind": ZoneKind.CUSTOM,
            "polygon": Region(exterior=ZONE_RING),
            "applies_to": ZONE_SELECTOR,
        }
    )
    sheet = _validated(show.sheet, zones=(*show.sheet.zones, zone))
    return _validated(show, sheet=sheet)


def _with_text_form(show: "Show") -> "Show":
    """Adds a last set holding a text form that writes TEXT with every seeded spot."""
    from showpy.choreo.form_place import place
    from showpy.choreo.forms import text_form
    from showpy.choreo.match_ops import with_set
    from showpy.show.changes import apply_change
    from showpy.show.manage import add_set
    from showpy.show.models import FormOp

    show = add_set(show, TEXT_SET, TEXT_COUNTS)
    members = [s.id for s in show.cast.spots if s.group != TRUMPETS]
    form = text_form(
        TEXT_FORM_ID,
        members,
        TEXT,
        fonts=[TEXT_FONT],
        height=TEXT_HEIGHT_M,
        at=TEXT_AT,
        set_id=TEXT_SET,
        n=len(members),
    )
    placed, filled = place(show, TEXT_SET, [form])
    return apply_change(with_set(show, placed), [FormOp(form=f) for f in filled])


def v030_show() -> "Show":
    """Builds the v0.3.0 show: a renumbered set with explicit keys, a text form and a zone.

    Returns:
        show: The seeded show with set 2A inserted and renumbered, trumpets, a selector zone and
            a last set written as text.
    """
    from showpy.show.manage import add_set, renumber

    show = _base_show("seeded")
    show = add_set(show, INSERTED_SET, INSERTED_COUNTS, after=PATH_TRANSITION[1])
    show, _ = renumber(show)
    return _with_text_form(_with_trumpets_and_zone(show))


# Any: v0.2.0 and earlier take actor= and message=, which later releases removed.
def _authorship(write: Callable[..., Any], actor: str, message: str) -> dict[str, str]:
    """Returns the authorship keywords a release's write calls require, if any."""
    if "actor" in inspect.signature(write).parameters:
        return {"actor": actor, "message": message}
    return {}


def _save_legacy(repo: "FileRepository", kind: str) -> list["Revision"]:
    """Saves the three legacy revisions: the show with a script, a path edit and a pin."""
    from showpy.show.documents import script_doc
    from showpy.show.files import show_changes

    saved, edited, pinned = fixture_shows(kind)
    changes = show_changes(saved, {})
    changes[script_doc(SCRIPT_SET)] = SCRIPT_SOURCE.encode()
    transaction: Callable[..., Revision] = repo.transaction
    put_show: Callable[..., Revision] = repo.put_show
    first = transaction(
        SHOW_ID,
        changes,
        expected_revision="",
        at=STAMP,
        **_authorship(transaction, "designer", "Save"),
    )
    second = put_show(
        SHOW_ID,
        edited,
        expected_revision=first.id,
        at=STAMP,
        **_authorship(put_show, "agent", "Edit paths"),
    )
    third = put_show(
        SHOW_ID,
        pinned,
        expected_revision=second.id,
        at=STAMP,
        **_authorship(put_show, "designer", "Pin"),
    )
    return [first, second, third]


def _hand_documents(docs: dict[str, bytes]) -> dict[str, bytes]:
    """Writes sets 1a, 1A and con as v0.2 stored them: folders named by ID, no `key` field."""
    from showpy.show.documents import dump_json, set_doc

    meta = json.loads(docs[SHOW_DOC])
    meta[SET_ORDER_FIELD] = [*meta[SET_ORDER_FIELD], *HAND_SET_IDS]
    changed = {SHOW_DOC: dump_json(meta)}
    body = json.loads(docs[set_doc(HAND_SET_SOURCE)])
    for set_id in HAND_SET_IDS:
        placements = [{**p, "form": None, "order": None} for p in body["placements"]]
        stored = {**body, "id": set_id, "forms": [], "placements": placements}
        stored.pop("key", None)
        changed[set_doc(set_id)] = dump_json(stored)
    return changed


def _save_v030(repo: "FileRepository") -> list["Revision"]:
    """Saves the v0.3.0 show, then commits the hand-written legacy sets past the commit check."""
    from showpy.show.integrity import blob_digest
    from showpy.show.models import Revision

    first = repo.put_show(SHOW_ID, v030_show(), expected_revision="", at=STAMP)
    tree = dict(repo.backend.tree(SHOW_ID, first.id))
    docs = {path: repo.backend.blob(SHOW_ID, digest) for path, digest in tree.items()}
    changed = _hand_documents(docs)
    tree.update({path: blob_digest(raw) for path, raw in changed.items()})
    body = {"parent": first.id, "at": STAMP.isoformat(), "tree": tree}
    revision_id = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    second = Revision(id=revision_id, parent=first.id, at=STAMP, changed=tuple(sorted(changed)))
    blobs = {blob_digest(raw): raw for raw in changed.values()}
    repo.backend.commit(SHOW_ID, second, tree, blobs)
    return [first, second]


def build_fixture(out: Path, kind: str) -> None:
    """Writes the repository and expected.json under `out`.

    Args:
        out: Empty or missing directory that receives the repository and expected.json.
        kind: Content to save, one of SHOW_CHOICES.
    """
    from showpy.core.util import content_hash
    from showpy.show.file_repository import FileRepository

    repo = FileRepository(out / REPO_DIR)
    revisions = _save_v030(repo) if kind == "v030" else _save_legacy(repo, kind)
    head = repo.get_show(SHOW_ID)
    expected = {
        "show_id": SHOW_ID,
        "head": revisions[-1].id,
        "revisions": [r.id for r in revisions],
        "legacy_content_hash": content_hash(head),
        "show": oracle_json(head),
    }
    (out / EXPECTED_FILE).write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")


def main() -> int:
    """Parses the command line and builds the fixture."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--package", default=LIBRARY)
    parser.add_argument("--show", choices=SHOW_CHOICES, default=SHOW_CHOICES[0])
    args = parser.parse_args()
    out: Path = args.out
    if out.exists() and any(out.iterdir()):
        sys.stderr.write(f"E: {out} is not empty\n")
        return 1
    _use_package(args.package)
    out.mkdir(parents=True, exist_ok=True)
    build_fixture(out, args.show)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
