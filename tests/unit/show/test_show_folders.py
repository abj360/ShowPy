#!/usr/bin/env python3

"""
test_show_folders.py --- tests the encoded folder names of shows in a layout-2 repository

Contains:
    test_show_folder_is_lowercase_slug_and_sixteen_hex: names a folder from the lower-cased slug
        and the domain-separated SHA-256 of the ID
    test_show_folder_is_never_a_device_name_or_over_49_characters: keeps every folder name short,
        free of trailing dots and spaces, and never a Windows device name
    test_regression_a_show_id_with_a_surrogate_is_refused_by_the_file_backend: refuses an ID
        UTF-8 cannot encode with StorageError BAD_PATH in every FileBackend read, never a raw
        UnicodeEncodeError
"""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from e8s.core.errors import StorageError
from e8s.core.ids import WINDOWS_DEVICE_NAMES, is_device_name
from e8s.examples import two_set_show
from e8s.show.document_repository import DocumentRepository
from e8s.show.file_backend import FileBackend
from e8s.show.show_folders import SHOW_FOLDER_PATTERN, show_folder

FOLDER_MAX_CHARS = 49
HOSTILE_IDS = [
    *sorted(WINDOWS_DEVICE_NAMES),
    "con.txt",
    "abc.",
    "abc ",
    "a" * 300,
    "Ünïcode-名前",
    "../../escape",
    "",
]


def test_show_folder_is_lowercase_slug_and_sixteen_hex() -> None:
    """Names a folder from the lower-cased slug and the domain-separated hash of the exact ID."""
    expected_hash = hashlib.sha256(b"e8s.show-folder\nShow_1.v2").hexdigest()[:16]
    assert show_folder("Show_1.v2") == f"show_1_v2-{expected_hash}"
    assert show_folder("Show") == "show-f2227dcc569c0978"
    assert show_folder("show") == "show-7e56e2f6502dbdf4"
    assert show_folder("abc.") == "abc_-ff1041c1bbd813cd"
    assert show_folder("x" * 40) == "x" * 32 + "-" + show_folder("x" * 40)[-16:]


@pytest.mark.parametrize("show_id", HOSTILE_IDS)
def test_show_folder_is_never_a_device_name_or_over_49_characters(show_id: str) -> None:
    """Keeps every folder name within 49 characters, without a trailing dot or a device name."""
    folder = show_folder(show_id)
    assert SHOW_FOLDER_PATTERN.fullmatch(folder)
    assert len(folder) <= FOLDER_MAX_CHARS
    assert not is_device_name(folder)
    assert folder == folder.rstrip(". ")
    assert folder == folder.lower()


SURROGATE_IDS = [
    pytest.param("a\udcffb", id="escaped-byte"),
    pytest.param("a\ud800b", id="unpaired"),
]
DIGEST = "0" * 64
READS: list[tuple[str, Callable[[FileBackend, str], object]]] = [
    ("head", lambda backend, show_id: backend.head(show_id)),
    ("log", lambda backend, show_id: backend.log(show_id)),
    ("tree", lambda backend, show_id: backend.tree(show_id, DIGEST)),
    ("blob", lambda backend, show_id: backend.blob(show_id, DIGEST)),
]


@pytest.mark.parametrize("show_id", SURROGATE_IDS)
@pytest.mark.parametrize("read", [pytest.param(read, id=name) for name, read in READS])
def test_regression_a_show_id_with_a_surrogate_is_refused_by_the_file_backend(
    tmp_path: Path, show_id: str, read: Callable[[FileBackend, str], object]
) -> None:
    at = datetime(2026, 11, 27, tzinfo=UTC)
    DocumentRepository(FileBackend(tmp_path)).put_show(
        "show_1", two_set_show(), expected_revision="", at=at
    )
    with pytest.raises(StorageError) as caught:
        read(FileBackend(tmp_path), show_id)
    assert caught.value.code == "BAD_PATH"
    assert "show id" in caught.value.message
