"""
file_repository.py --- a repository that keeps every revision as files under a root directory

Contains:
    FileRepository: keeps every revision as files under a root, in the layout the root holds
"""

from pathlib import Path

from e8s.show.document_repository import DocumentRepository
from e8s.show.file_backend import FileBackend
from e8s.show.layout import LEGACY_LAYOUT, read_layout
from e8s.show.layout1 import Layout1Reader


class FileRepository(DocumentRepository):
    """Keeps every revision as files under a root directory, in the layout the root holds.

    An empty or missing root, or one holding `layout.json`, is stored in layout 2: each show in
    its own encoded folder, its history as numbered slots. A root written by a release before
    v0.5.0 is layout 1, which opens read-only: every read works, and every write raises
    VersionError OLD_LAYOUT until `upgrade_repository` copies the root into layout 2. Files are
    written to a temp file and linked into place, never replaced, and blobs are
    content-addressed, so writing equal content twice is a no-op. Two processes racing on one
    head cannot both land.

    Attributes:
        root: Directory holding the repository.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        layout = read_layout(self.root)
        backend = Layout1Reader(self.root) if layout == LEGACY_LAYOUT else FileBackend(self.root)
        super().__init__(backend)
