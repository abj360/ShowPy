#!/usr/bin/env python3

"""
legacy_v020.py --- opens copies of the repository fixture written by v0.2.0

Contains:
    V020_FIXTURE: names the read-only v0.2.0 fixture folder
    NEGATIVE_ZERO: matches a stored `-0.0`, which v0.2 wrote and canonical JSON never does
    v020_expected(): loads the fixture's expected.json
    v020_workspace(): copies the fixture and opens a repository and workspace on the copy
    retitled(): returns a workspace's show.json with a new title, not canonically encoded
"""

import json
import re
import shutil
from pathlib import Path
from typing import Any

from e8s.show.file_repository import FileRepository
from e8s.show.workspace import Workspace, open_workspace

V020_FIXTURE = Path(__file__).resolve().parent / "repos" / "v0.2.0"
NEGATIVE_ZERO = re.compile(rb"-0\.0\b")


def v020_expected() -> dict[str, Any]:
    """Loads the v0.2.0 fixture's expected.json."""
    loaded: dict[str, Any] = json.loads((V020_FIXTURE / "expected.json").read_text())
    return loaded


def v020_workspace(tmp_path: Path) -> tuple[FileRepository, Workspace, dict[str, Any]]:
    """Copies the read-only v0.2.0 fixture under `tmp_path` and opens it.

    Returns:
        repo: The repository on the copy.
        ws: A workspace on the fixture's show.
        expected: The fixture's expected.json.
    """
    expected = v020_expected()
    shutil.copytree(V020_FIXTURE / "repo", tmp_path / "repo")
    repo = FileRepository(tmp_path / "repo")
    return repo, open_workspace(repo, expected["show_id"]), expected


def retitled(ws: Workspace, title: str) -> bytes:
    """Returns the workspace's show.json with `title`, as plain `json.dumps` writes it."""
    document = json.loads(ws.read("/show/show.json"))
    document["title"] = title
    return json.dumps(document).encode()
