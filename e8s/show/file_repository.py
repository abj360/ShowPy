"""
file_repository.py --- a repository that keeps every revision as files under a root directory

Contains:
    FileRepository: keeps every revision as JSON and blob files under a root directory
"""

from pathlib import Path

from e8s.show.document_repository import DocumentRepository
from e8s.show.layout1 import _FileBackend


class FileRepository(DocumentRepository):
    """Keeps every revision as JSON and blob files under a root directory.

    Writes are atomic (a temp file hard-linked into place, never replacing a file) and blobs are
    content-addressed, so writing equal content twice is a no-op. Two processes racing on one
    head cannot both land.

    Attributes:
        root: Directory holding one folder per show.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        super().__init__(_FileBackend(self.root))
