#!/usr/bin/env python3

"""
upgrade_repos.py --- copies released layout-1 fixtures and builds layout-1 roots for upgrade tests

Contains:
    FIXTURES: names the folder of the released repository fixtures
    copied(): copies a released fixture's repository under a test's temp folder
    built(): writes a layout-1 root holding one show with a given number of revisions
    fingerprint(): maps every path under a root to its content hash, mtime and mode
    refuse_links_from(): makes every hard link out of a folder fail with an errno
    expect_unfinished(): checks that a target holds no layout file and that readers refuse it
"""

import hashlib
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from e8s.core.errors import StorageError
from e8s.examples import two_set_show
from e8s.show.document_repository import DocumentRepository
from e8s.show.file_repository import FileRepository
from e8s.show.memory_backend import InMemoryRepository
from tests.fixtures.layout1 import write_layout1_root

type StrPath = str | os.PathLike[str]

FIXTURES = Path(__file__).resolve().parent / "repos"
LATEST = "v0.4.0"
SHOW_ID = "show_1"
AT = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def copied(tmp_path: Path, release: str = LATEST) -> Path:
    source = tmp_path / "old"
    shutil.copytree(FIXTURES / release / "repo", source)
    return source


def built(root: Path, revisions: int) -> DocumentRepository:
    """Writes a layout-1 root holding one show with `revisions` revisions."""
    memory = InMemoryRepository()
    head = memory.put_show(SHOW_ID, two_set_show(), expected_revision="", at=AT).id
    for n in range(1, revisions):
        docs = {f"/renders/{n}.txt": f"render {n}".encode()}
        at = AT + timedelta(minutes=n)
        head = memory.transaction(SHOW_ID, docs, expected_revision=head, at=at).id
    write_layout1_root(memory, root)
    return memory


def fingerprint(root: Path) -> dict[str, tuple[str, int, int]]:
    """Maps every path under a root, the root too, to its content hash, mtime and mode."""
    prints: dict[str, tuple[str, int, int]] = {}
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        is_file = path.is_file() and not path.is_symlink()
        content = hashlib.sha256(path.read_bytes()).hexdigest() if is_file else ""
        prints[path.relative_to(root).as_posix()] = (content, info.st_mtime_ns, info.st_mode)
    return prints


def refuse_links_from(monkeypatch: pytest.MonkeyPatch, source: Path, code: int) -> list[Path]:
    """Makes every hard link out of `source` fail with `code`, as across two file systems."""
    real = os.link
    refused: list[Path] = []

    def link(src: StrPath, dst: StrPath, *, follow_symlinks: bool = True) -> None:
        if Path(src).is_relative_to(source):
            refused.append(Path(src))
            raise OSError(code, os.strerror(code), str(src))
        real(src, dst, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "link", link)
    return refused


def expect_unfinished(target: Path) -> None:
    assert not (target / "layout.json").exists()
    with pytest.raises(StorageError) as refused:
        FileRepository(target)
    assert refused.value.code == "CORRUPT"
