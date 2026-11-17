"""
file_repository.py --- a repository that keeps every revision as files under a root directory

Contains:
    REVISION_ID_PATTERN: matches the 64-digit hex IDs allowed in next/ links and tree digests
    FileRepository: keeps every revision as JSON and blob files under a root directory
"""

import json
import os
import re
import stat
import tempfile
from pathlib import Path

from showpy.core.errors import StorageError
from showpy.show.documents import dump_json
from showpy.show.models import Revision
from showpy.show.repositories import DocumentRepository, conflict_error

ROOT_LINK = "root"
TREE_DIR = "trees"
LINK_DIR = "next"
OBJECT_DIR = "objects"
TEMP_PREFIX = ".tmp-"
REVISION_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _temp_copy(directory: Path, data: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, delete=False, prefix=TEMP_PREFIX) as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return Path(handle.name)


def _atomic_write(path: Path, data: bytes) -> None:
    temp = _temp_copy(path.parent, data)
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _link_exclusive(path: Path, data: bytes) -> bool:
    """Creates `path` with `data` only if it does not exist; os.link refuses to overwrite."""
    temp = _temp_copy(path.parent, data)
    try:
        os.link(temp, path)
    except FileExistsError:
        return False
    finally:
        temp.unlink(missing_ok=True)
    return True


class _FileBackend:
    """Lays out `<root>/<show>/objects/<sha256>`, `trees/<rev>.json` and `next/<parent>`.

    The chain of `next/<parent>` files is the history: each holds its child's ID, and creating
    one with os.link is the atomic compare-and-swap that makes a commit land.
    """

    def __init__(self, root: Path):
        self.root = root

    def _dir(self, show_id: str) -> Path:
        return self.root / show_id

    def show_ids(self) -> list[str]:
        if not self.root.exists():
            return []
        return [p.name for p in self.root.iterdir() if (p / LINK_DIR / ROOT_LINK).exists()]

    def _corrupt(self, show_id: str, path: Path, reason: str) -> StorageError:
        return StorageError(
            f"repository of {show_id} is corrupt: {reason}",
            hint="restore the show folder from a backup or remove the damaged file",
            context={"show": show_id, "path": str(path)},
            code="CORRUPT",
        )

    def _checked_id(self, show_id: str, value: str, source: Path) -> str:
        if not REVISION_ID_PATTERN.fullmatch(value):
            raise self._corrupt(show_id, source, f"{value!r} is not a 64-digit hex id")
        return value

    def _guard_dirs(self, show_id: str) -> None:
        base = self._dir(show_id)
        for directory in (base, base / OBJECT_DIR, base / TREE_DIR, base / LINK_DIR):
            if directory.is_symlink():
                raise self._corrupt(show_id, directory, "directory is a symbolic link")

    def _read(self, show_id: str, path: Path) -> bytes:
        try:
            if path.is_symlink():
                raise self._corrupt(show_id, path, "file is a symbolic link")
            if not stat.S_ISREG(path.lstat().st_mode):
                raise self._corrupt(show_id, path, "file is missing or not a regular file")
            return path.read_bytes()
        except (FileNotFoundError, IsADirectoryError) as error:
            raise self._corrupt(show_id, path, "file is missing or not a file") from error

    def _read_link(self, show_id: str, path: Path) -> str:
        try:
            return self._read(show_id, path).decode().strip()
        except UnicodeDecodeError as error:
            raise self._corrupt(show_id, path, "link is not valid UTF-8") from error

    def _tree_path(self, show_id: str, revision_id: str, source: Path) -> Path:
        name = self._checked_id(show_id, revision_id, source)
        return self._dir(show_id) / TREE_DIR / f"{name}.json"

    def _record(self, show_id: str, revision_id: str, source: Path) -> dict[str, object]:
        path = self._tree_path(show_id, revision_id, source)
        try:
            record = json.loads(self._read(show_id, path))
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
            raise self._corrupt(
                show_id, path, "tree file is not valid JSON or too deeply nested"
            ) from error
        if not isinstance(record, dict) or "revision" not in record or "tree" not in record:
            raise self._corrupt(show_id, path, "tree file lacks revision or tree")
        return record

    def log(self, show_id: str) -> list[Revision]:
        self._guard_dirs(show_id)
        links = self._dir(show_id) / LINK_DIR
        log: list[Revision] = []
        seen: set[str] = set()
        current = ROOT_LINK
        while os.path.lexists(links / current):
            link = links / current
            previous = "" if current == ROOT_LINK else current
            current = self._checked_id(show_id, self._read_link(show_id, link), link)
            if current in seen:
                raise self._corrupt(show_id, link, "next/ links form a cycle")
            seen.add(current)
            revision = Revision.model_validate(self._record(show_id, current, link)["revision"])
            self._check_revision(show_id, revision, current, previous, link)
            log.append(revision)
        return log

    def _check_revision(
        self, show_id: str, revision: Revision, expected_id: str, parent: str, source: Path
    ) -> None:
        if revision.id != expected_id:
            raise self._corrupt(show_id, source, "revision id differs from its link")
        if revision.parent != parent:
            raise self._corrupt(show_id, source, "revision parent differs from the chain")

    def tree(self, show_id: str, revision_id: str) -> dict[str, str]:
        self._guard_dirs(show_id)
        path = self._tree_path(show_id, revision_id, self._dir(show_id) / TREE_DIR)
        tree = self._record(show_id, revision_id, path)["tree"]
        if not isinstance(tree, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in tree.items()
        ):
            raise self._corrupt(show_id, path, "tree is not a mapping of paths to digests")
        for value in tree.values():
            self._checked_id(show_id, value, path)
        return dict(tree)

    def blob(self, show_id: str, digest: str) -> bytes:
        self._guard_dirs(show_id)
        objects = self._dir(show_id) / OBJECT_DIR
        return self._read(show_id, objects / self._checked_id(show_id, digest, objects))

    def commit(
        self, show_id: str, revision: Revision, tree: dict[str, str], blobs: dict[str, bytes]
    ) -> None:
        self._guard_dirs(show_id)
        base = self._dir(show_id)
        if revision.parent:
            self._checked_id(show_id, revision.parent, base / LINK_DIR)
        for digest, data in blobs.items():
            target = base / OBJECT_DIR / self._checked_id(show_id, digest, base / OBJECT_DIR)
            if target.is_symlink():
                raise self._corrupt(show_id, target, "file is a symbolic link")
            if not target.exists():
                _atomic_write(target, data)
        record = {"revision": revision.model_dump(mode="json"), "tree": tree}
        _atomic_write(base / TREE_DIR / f"{revision.id}.json", dump_json(record))
        link = base / LINK_DIR / (revision.parent or ROOT_LINK)
        if not _link_exclusive(link, revision.id.encode()):
            raise conflict_error(show_id, revision.parent, self.log(show_id)[-1:])


class FileRepository(DocumentRepository):
    """Keeps every revision as JSON and blob files under a root directory.

    Writes are atomic (temp file plus os.replace) and blobs are content-addressed, so writing
    equal content twice is a no-op. Two processes racing on one head cannot both land.

    Attributes:
        root: Directory holding one folder per show.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        super().__init__(_FileBackend(self.root))
