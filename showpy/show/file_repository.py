"""
file_repository.py --- a repository that keeps every revision as files under a root directory

Contains:
    FileRepository: keeps every revision as JSON and blob files under a root directory
"""

import json
import os
import tempfile
from pathlib import Path

from showpy.show.documents import dump_json
from showpy.show.models import Revision
from showpy.show.repositories import DocumentRepository, conflict_error

ROOT_LINK = "root"
TREE_DIR = "trees"
LINK_DIR = "next"
OBJECT_DIR = "objects"
TEMP_PREFIX = ".tmp-"


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
        os.replace(temp, path)
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

    def _record(self, show_id: str, revision_id: str) -> dict[str, object]:
        raw = (self._dir(show_id) / TREE_DIR / f"{revision_id}.json").read_bytes()
        record: dict[str, object] = json.loads(raw)
        return record

    def log(self, show_id: str) -> list[Revision]:
        links = self._dir(show_id) / LINK_DIR
        log: list[Revision] = []
        current = ROOT_LINK
        while (links / current).exists():
            current = (links / current).read_text().strip()
            log.append(Revision.model_validate(self._record(show_id, current)["revision"]))
        return log

    def tree(self, show_id: str, revision_id: str) -> dict[str, str]:
        tree = self._record(show_id, revision_id)["tree"]
        return dict(tree) if isinstance(tree, dict) else {}

    def blob(self, show_id: str, digest: str) -> bytes:
        return (self._dir(show_id) / OBJECT_DIR / digest).read_bytes()

    def commit(
        self, show_id: str, revision: Revision, tree: dict[str, str], blobs: dict[str, bytes]
    ) -> None:
        base = self._dir(show_id)
        for digest, data in blobs.items():
            if not (base / OBJECT_DIR / digest).exists():
                _atomic_write(base / OBJECT_DIR / digest, data)
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
