#!/usr/bin/env python3

"""
test_legacy_repository.py --- guards that a repository written by v0.2.0 stays readable

Contains:
    test_v020_repository_loads_without_loss: lists the fixture's revisions and decodes a head show
        equal to the one stored in expected.json
    test_v020_revision_records_carry_legacy_keys: finds actor, message and kind in every stored
        revision record
    test_commit_on_v020_repository_gets_a_new_scheme_id: commits on a copy and checks that the new
        ID uses the domain-tagged scheme while stored IDs and the head chain stay as written
"""

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from showpy.core.util import content_hash
from showpy.show.file_repository import FileRepository
from showpy.show.models import Show

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "repos" / "v0.2.0"
LEGACY_KEYS = {"actor", "message", "kind"}
COMMITTED_AT = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
HEX_LENGTH = 64


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


def test_commit_on_v020_repository_gets_a_new_scheme_id(tmp_path: Path) -> None:
    expected = _expected()
    shutil.copytree(FIXTURE / "repo", tmp_path / "repo")
    repo = FileRepository(tmp_path / "repo")
    show_id = expected["show_id"]
    head_show = repo.get_show(show_id)
    revision = repo.put_show(
        show_id,
        head_show.model_copy(update={"title": f"{head_show.title} v2"}),
        expected_revision=expected["head"],
        actor="designer",
        at=COMMITTED_AT,
        message="rename",
    )
    tree = repo.backend.tree(show_id, revision.id)
    body = {
        "parent": expected["head"],
        "at": COMMITTED_AT.isoformat(),
        "actor": "designer",
        "message": "rename",
        "kind": revision.kind,
        "target": revision.target,
        "changes": {path: tree.get(path) for path in revision.changed},
    }
    legacy_scheme = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
    assert revision.changed
    assert revision.id == content_hash(body, domain="showpy.revision")
    assert revision.id != hashlib.sha256(legacy_scheme).hexdigest()
    assert len(revision.id) == HEX_LENGTH
    assert [r.id for r in repo.revisions(show_id)] == [*expected["revisions"], revision.id]
    assert repo.get_show(show_id, expected["head"]) == Show.model_validate(expected["show"])
    assert content_hash(head_show) != expected["legacy_content_hash"]
