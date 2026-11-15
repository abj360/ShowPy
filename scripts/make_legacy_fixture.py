#!/usr/bin/env python3

"""
make_legacy_fixture.py --- writes the v0.2.0 file-repository fixture used by compatibility tests

Run it from a checkout of tag v0.2.0 with that checkout's own interpreter, so the stored bytes
are the ones v0.2.0 wrote:

    PYTHONPATH=. uv run python <repo>/scripts/make_legacy_fixture.py \
        --out <repo>/tests/fixtures/repos/v0.2.0

Contains:
    build_fixture(): writes a three-revision repository and its expected.json under a directory
    main(): parses --out and builds the fixture
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from showpy.choreo.models import PathRow, PathStyle, Waypoint
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


def _edited_show() -> Show:
    """Builds the seeded show plus a negative-zero placement, tags and a named selection."""
    show = seeded_show(seed=SEED, spots=SPOT_COUNT, sets=SET_COUNT)
    spots = list(show.cast.spots)
    spots[TAGGED_SPOT_INDEX] = spots[TAGGED_SPOT_INDEX].model_copy(
        update={"tags": frozenset({"lead", "horn"})}
    )
    selection = frozenset(s.id for s in spots[:SELECTION_SIZE])
    cast = show.cast.model_copy(
        update={"spots": tuple(spots), "selections": {SELECTION_NAME: selection}}
    )
    first = show.set(NEGATIVE_ZERO_SET)
    placements = list(first.placements)
    placements[0] = placements[0].model_copy(update={"x": -0.0})
    edited = first.model_copy(update={"placements": tuple(placements)})
    sets = tuple(edited if s.id == first.id else s for s in show.sets)
    return show.model_copy(update={"cast": cast, "sets": sets})


def _path_edit(show: Show) -> Show:
    """Gives the first spot a curved row on the first transition."""
    spec = show.transition(PATH_TRANSITION)
    entity = show.cast.spots[0].id
    row = PathRow(entity=entity, style=PathStyle.CURVE, waypoints=(PATH_WAYPOINT,))
    edited = spec.model_copy(update={"rows": {**spec.rows, entity: row}})
    others = tuple(t for t in show.transitions if t.id != spec.id)
    return show.model_copy(update={"transitions": (*others, edited)})


def build_fixture(out: Path) -> None:
    """Writes the three-revision repository and expected.json under `out`.

    Args:
        out: Empty or missing directory that receives the repository and expected.json.
    """
    repo = FileRepository(out / REPO_DIR)
    show = _edited_show()
    changes = show_changes(show, {})
    changes[script_doc(SCRIPT_SET)] = SCRIPT_SOURCE.encode()
    first = repo.transaction(
        SHOW_ID, changes, expected_revision="", actor="designer", at=STAMP, message="Save"
    )
    show = _path_edit(show)
    second = repo.put_show(
        SHOW_ID, show, expected_revision=first.id, actor="agent", at=STAMP, message="Edit paths"
    )
    show = pin(show, "entity", show.cast.spots[PINNED_SPOT_INDEX].id, "soloist")
    third = repo.put_show(
        SHOW_ID, show, expected_revision=second.id, actor="designer", at=STAMP, message="Pin"
    )
    head = repo.get_show(SHOW_ID)
    expected = {
        "show_id": SHOW_ID,
        "head": third.id,
        "revisions": [first.id, second.id, third.id],
        "legacy_content_hash": content_hash(head),
        "show": head.model_dump(mode="json"),
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
