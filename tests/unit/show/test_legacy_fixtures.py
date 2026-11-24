#!/usr/bin/env python3

"""
test_legacy_fixtures.py --- guards the repository fixture written by the v0.4.0 tag

Contains:
    test_v040_fixture_loads_every_revision: decodes each revision into the show expected.json
        holds for its ID
    test_v040_fixture_holds_v1_and_v2_revisions: finds no header in the two revisions copied from
        v0.3.0 and the `e8.show` header in every revision v0.4.0 wrote
"""

import json
import shutil
from pathlib import Path
from typing import Any

from e8s.show.documents import SHOW_DOC
from e8s.show.file_repository import FileRepository
from e8s.show.models import Show

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "repos" / "v0.4.0"
FORMAT_KEY = "format"
FORMAT_NAME = "e8.show"
COPIED_REVISIONS = 2
REVISION_COUNT = 5


def _repository(tmp_path: Path) -> tuple[FileRepository, dict[str, Any]]:
    """Copies the read-only fixture and loads its expected.json."""
    shutil.copytree(FIXTURE / "repo", tmp_path / "repo")
    expected: dict[str, Any] = json.loads((FIXTURE / "expected.json").read_text())
    return FileRepository(tmp_path / "repo"), expected


def test_v040_fixture_loads_every_revision(tmp_path: Path) -> None:
    repo, expected = _repository(tmp_path)
    revisions = [r.id for r in repo.revisions(expected["show_id"])]
    assert revisions == expected["revisions"]
    assert len(revisions) == REVISION_COUNT
    assert revisions[-1] == expected["head"]
    assert set(expected["shows"]) == set(revisions)
    for revision in revisions:
        loaded = repo.get_show(expected["show_id"], revision)
        oracle = Show.model_validate(expected["shows"][revision])
        assert loaded == oracle


def test_v040_fixture_holds_v1_and_v2_revisions(tmp_path: Path) -> None:
    repo, expected = _repository(tmp_path)
    headers = []
    for revision in expected["revisions"]:
        raw = repo.read(expected["show_id"], SHOW_DOC, revision)
        assert raw is not None
        headers.append(json.loads(raw).get(FORMAT_KEY))
    assert headers == [None] * COPIED_REVISIONS + [FORMAT_NAME] * (
        REVISION_COUNT - COPIED_REVISIONS
    )
