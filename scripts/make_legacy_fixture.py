#!/usr/bin/env python3

"""
make_legacy_fixture.py --- writes the v0.2.0 file-repository fixture used by compatibility tests

Run it from a checkout of tag v0.2.0 with that checkout's own interpreter, so the stored bytes
are the ones v0.2.0 wrote:

    PYTHONPATH=. uv run python <repo>/scripts/make_legacy_fixture.py \
        --out <repo>/tests/fixtures/repos/v0.2.0

Models are edited through full validation and the expected.json oracle lists every set sorted,
so the oracle does not depend on PYTHONHASHSEED. Only APIs that exist in v0.2.0 are used.

Contains:
    oracle_json(): dumps a show as JSON data with every set sorted
    fixture_shows(): builds the shows of the three revisions: the save, a path edit and a pin
    build_fixture(): writes a three-revision repository and its expected.json under a directory
    main(): parses --out and builds the fixture
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from showpy.choreo.path_models import PathRow, PathStyle, Waypoint
from showpy.core.util import content_hash
from showpy.show.documents import script_doc
from showpy.show.file_repository import FileRepository
from showpy.show.files import show_changes
from showpy.show.models import Show
from showpy.show.pinning import pin
from tests.fixtures.generators import seeded_show

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
PATH_WAYPOINT = Waypoint(x=1.5, y=4.0)
EXPECTED_FILE = "expected.json"
REPO_DIR = "repo"


def _validated[M: BaseModel](model: M, **changes: object) -> M:
    """Returns a copy of `model` with `changes`, run through every validator.

    v0.2.0 has no `FrozenModel.replace`, and `model_copy(update=)` skips validation.
    """
    fields = {name: getattr(model, name) for name in type(model).model_fields}
    return type(model).model_validate(fields | changes)


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


def oracle_json(show: Show) -> object:
    """Dumps a show as JSON data with every set sorted, so the oracle ignores the hash seed."""
    return to_jsonable_python(_sorted_sets(show.model_dump()))


def _edited_show() -> Show:
    """Builds the seeded show plus a negative-zero placement, tags and a named selection."""
    show = seeded_show(seed=SEED, spots=SPOT_COUNT, sets=SET_COUNT)
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


def _path_edit(show: Show) -> Show:
    """Gives the first spot a curved row on the first transition."""
    spec = show.transition(PATH_TRANSITION)
    entity = show.cast.spots[0].id
    row = PathRow(entity=entity, style=PathStyle.CURVE, waypoints=(PATH_WAYPOINT,))
    edited = _validated(spec, rows={**spec.rows, entity: row})
    others = tuple(t for t in show.transitions if t.id != spec.id)
    return _validated(show, transitions=(*others, edited))


def fixture_shows() -> tuple[Show, Show, Show]:
    """Builds the shows the three revisions save: the seeded show, a path edit and a pin.

    Returns:
        shows: The show of each revision, oldest first.
    """
    saved = _edited_show()
    edited = _path_edit(saved)
    pinned = pin(edited, "entity", edited.cast.spots[PINNED_SPOT_INDEX].id, "soloist")
    return saved, edited, pinned


def build_fixture(out: Path) -> None:
    """Writes the three-revision repository and expected.json under `out`.

    Args:
        out: Empty or missing directory that receives the repository and expected.json.
    """
    repo = FileRepository(out / REPO_DIR)
    saved, edited, pinned = fixture_shows()
    changes = show_changes(saved, {})
    changes[script_doc(SCRIPT_SET)] = SCRIPT_SOURCE.encode()
    # This script runs from tag v0.2.0, so its writes use the v0.2.0 API (actor= and message=).
    first = repo.transaction(  # type: ignore[call-arg]
        SHOW_ID,
        changes,
        expected_revision="",
        actor="designer",  # pyright: ignore[reportCallIssue]
        at=STAMP,
        message="Save",  # pyright: ignore[reportCallIssue]
    )
    second = repo.put_show(  # type: ignore[call-arg]
        SHOW_ID,
        edited,
        expected_revision=first.id,
        actor="agent",  # pyright: ignore[reportCallIssue]
        at=STAMP,
        message="Edit paths",  # pyright: ignore[reportCallIssue]
    )
    third = repo.put_show(  # type: ignore[call-arg]
        SHOW_ID,
        pinned,
        expected_revision=second.id,
        actor="designer",  # pyright: ignore[reportCallIssue]
        at=STAMP,
        message="Pin",  # pyright: ignore[reportCallIssue]
    )
    head = repo.get_show(SHOW_ID)
    expected = {
        "show_id": SHOW_ID,
        "head": third.id,
        "revisions": [first.id, second.id, third.id],
        "legacy_content_hash": content_hash(head),
        "show": oracle_json(head),
    }
    (out / EXPECTED_FILE).write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")


def main() -> int:
    """Parses the command line and builds the fixture."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    out: Path = parser.parse_args().out
    if out.exists() and any(out.iterdir()):
        sys.stderr.write(f"E: {out} is not empty\n")
        return 1
    out.mkdir(parents=True, exist_ok=True)
    build_fixture(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
