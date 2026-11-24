#!/usr/bin/env python3

"""
test_role_documents.py --- checks that storing sets and the cast as role documents keeps content

Set E8S_UPDATE_GOLDEN=1 to rewrite the interchange goldens; the diff then needs human review.

Contains:
    test_export_json_bytes_are_unchanged_by_the_role_split: exports a seeded show and the head of
        the v0.4.0 fixture with the bytes pinned before sets were stored as role documents
    test_reserved_roots_are_refused_until_a_format_defines_them: refuses a path under `/props/`
        or `/tracks/` with BAD_PATH and a hint naming the reserved root
    test_cast_proposals_and_assets_roots_are_known: accepts documents under `/cast/`,
        `/proposals/` and `/assets/`
"""

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
from e8s.show.file_repository import FileRepository
from e8s.show.integrity import KNOWN_ROOTS, require_known_path
from e8s.show.interchange import export_json
from e8s.show.models import Show
from tests.fixtures.generators import seeded_show

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
V040 = FIXTURES / "repos" / "v0.4.0"
GOLDEN = FIXTURES / "interchange"
UPDATE_FLAG = "E8S_UPDATE_GOLDEN"
SEED, SPOTS, SETS = 7, 16, 5
JSON_INDENT = 1


def _v040_head(tmp_path: Path) -> Show:
    """Copies the read-only v0.4.0 fixture and loads its head show."""
    shutil.copytree(V040 / "repo", tmp_path / "repo")
    expected: dict[str, Any] = json.loads((V040 / "expected.json").read_text())
    return FileRepository(tmp_path / "repo").get_show(expected["show_id"], expected["head"])


def _export_bytes(show: Show) -> bytes:
    """Writes the interchange document of a show, keeping the key order export_json gives."""
    text = json.dumps(export_json(show), indent=JSON_INDENT, ensure_ascii=False)
    return f"{text}\n".encode()


@pytest.mark.parametrize("name", ["seeded_show_export", "v040_head_export"])
def test_export_json_bytes_are_unchanged_by_the_role_split(name: str, tmp_path: Path) -> None:
    show = seeded_show(SEED, SPOTS, SETS) if name == "seeded_show_export" else _v040_head(tmp_path)
    golden = GOLDEN / f"{name}.json"
    exported = _export_bytes(show)
    if os.environ.get(UPDATE_FLAG) == "1":
        golden.write_bytes(exported)
    assert exported == golden.read_bytes()


@pytest.mark.parametrize("path", ["/props/p1.json", "/tracks/state.json", "/props/a/b.json"])
def test_reserved_roots_are_refused_until_a_format_defines_them(path: str) -> None:
    with pytest.raises(StorageError) as caught:
        require_known_path(path)
    assert caught.value.code == ErrorCode.BAD_PATH
    root = path[: path.index("/", 1) + 1]
    assert root in caught.value.message
    assert "reserved" in caught.value.hint
    assert root not in KNOWN_ROOTS


@pytest.mark.parametrize("path", ["/cast/cast.json", "/proposals/p1.json", "/assets/a.png"])
def test_cast_proposals_and_assets_roots_are_known(path: str) -> None:
    require_known_path(path)
