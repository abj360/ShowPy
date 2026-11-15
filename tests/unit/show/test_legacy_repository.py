#!/usr/bin/env python3

"""
test_legacy_repository.py --- guards that a repository written by v0.2.0 stays readable

Contains:
    test_v020_repository_loads_without_loss: lists the fixture's revisions and decodes a head show
        equal to the one stored in expected.json
    test_v020_revision_records_carry_legacy_keys: finds actor, message and kind in every stored
        revision record
"""

import json
import shutil
from pathlib import Path
from typing import Any

from showpy.show.file_repository import FileRepository
from showpy.show.models import Show

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "repos" / "v0.2.0"
LEGACY_KEYS = {"actor", "message", "kind"}


def _expected() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURE / "expected.json").read_text())
    return loaded


def test_v020_repository_loads_without_loss(tmp_path: Path) -> None:
    expected = _expected()
    shutil.copytree(FIXTURE / "repo", tmp_path / "repo")
    repo = FileRepository(tmp_path / "repo")
    show_id = expected["show_id"]
    assert [r.id for r in repo.revisions(show_id)] == expected["revisions"]
    assert repo.head(show_id) == expected["head"]
    assert repo.get_show(show_id) == Show.model_validate(expected["show"])


def test_v020_revision_records_carry_legacy_keys() -> None:
    expected = _expected()
    trees = FIXTURE / "repo" / expected["show_id"] / "trees"
    for revision_id in expected["revisions"]:
        record = json.loads((trees / f"{revision_id}.json").read_text())
        assert set(record["revision"]) >= LEGACY_KEYS
