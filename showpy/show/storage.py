"""
storage.py --- the repository and asset protocols plus the file view of a show

Contains:
    ROOT_REVISION: names the revision before the first save
    Repository: versions a show's documents; every write checks the expected revision
    AssetStore: stores binary assets under content-hash URIs
    Workspace: exposes one show as files, reading at its own revision like a working copy
    Workspace.read(): returns a file's text or bytes
    Workspace.write(): validates and saves one file
    Workspace.ls(): lists a directory
    Workspace.commit(): saves a batch of file changes addressed by set ID as one revision
    Workspace.refresh(): moves the workspace to the head revision
    Workspace.show_at(): returns the show at a revision, or None before the first save
    Workspace.tree_at(): returns the stored documents at a revision
    open_workspace(): opens a workspace at the head revision
    load_show(): reads the whole show at the workspace revision
    save_show(): writes the whole show, refusing when another write landed meanwhile
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from showpy.choreo.models import Form, Set, TransitionSpec
from showpy.core.errors import StorageError, UnknownRefError
from showpy.core.ids import SetId, TransitionId
from showpy.core.util import close_matches
from showpy.show.documents import SHOW_DOC, decode_show, encode_documents, encode_show, split_path
from showpy.show.files import (
    all_files,
    directories,
    read_file,
    show_changes,
    staged_changes,
)
from showpy.show.models import Revision, Show
from showpy.show.set_keys import SetFolders
from showpy.time.models import Timeline

ROOT_REVISION = ""


class Repository(Protocol):
    """Versions each show's documents in a linear history of revisions.

    Paths are stored paths, which address sets by key (`/sets/<key>/set.json`, ADR 0004).
    Every write takes the revision the caller read at and raises ConflictError, listing what
    landed since, when the head moved.
    """

    def shows(self) -> list[str]:
        """Returns the IDs of stored shows."""
        ...

    def head(self, show_id: str) -> str:
        """Returns the head revision ID, or ROOT_REVISION before the first save."""
        ...

    def revisions(self, show_id: str) -> list[Revision]:
        """Returns every revision, oldest first."""
        ...

    def get_revision(self, show_id: str, revision_id: str) -> Revision:
        """Returns one revision."""
        ...

    def paths(self, show_id: str, revision: str | None = None) -> list[str]:
        """Returns the stored document paths at a revision (default head), sorted."""
        ...

    def read(self, show_id: str, path: str, revision: str | None = None) -> bytes | None:
        """Returns a document at a revision (default head), or None when absent."""
        ...

    def transaction(
        self,
        show_id: str,
        changes: Mapping[str, bytes | None],
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Writes (bytes) or deletes (None) documents atomically as one revision.

        An edit that changes nothing returns the head revision unchanged (idempotent upsert).
        """
        ...

    def get_show(self, show_id: str, revision: str | None = None) -> Show:
        """Returns the whole show at a revision (default head)."""
        ...

    def put_show(
        self,
        show_id: str,
        show: Show,
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Writes the whole show, deleting documents of sets and paths it no longer has."""
        ...

    def list_sets(self, show_id: str) -> list[SetId]:
        """Returns set IDs in show order."""
        ...

    def get_set(self, show_id: str, set_id: SetId) -> Set:
        """Returns one set."""
        ...

    def put_set(
        self,
        show_id: str,
        new_set: Set,
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Replaces one existing set."""
        ...

    def get_script(self, show_id: str, set_id: SetId) -> str:
        """Returns a set's build script."""
        ...

    def put_script(
        self,
        show_id: str,
        set_id: SetId,
        source: str,
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Replaces a set's build script."""
        ...

    def get_paths(self, show_id: str, tid: TransitionId) -> TransitionSpec:
        """Returns a transition's paths, defaulting to straight moves."""
        ...

    def put_paths(
        self,
        show_id: str,
        spec: TransitionSpec,
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Replaces a transition's paths; counts follow the destination set."""
        ...

    def get_forms(self, show_id: str, set_id: SetId) -> tuple[Form, ...]:
        """Returns a set's forms sorted by ID."""
        ...

    def put_forms(
        self,
        show_id: str,
        set_id: SetId,
        forms: tuple[Form, ...],
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Replaces a set's forms."""
        ...

    def get_timeline(self, show_id: str) -> Timeline:
        """Returns the show timeline."""
        ...

    def get_limits(self, show_id: str) -> str:
        """Returns the designer's limits YAML text; empty when none is stored."""
        ...

    def get_waivers(self, show_id: str) -> str:
        """Returns the designer's waivers YAML text; empty when none is stored."""
        ...


class AssetStore(Protocol):
    """Stores binary assets such as captured video and audio under content-hash URIs."""

    def put(self, data: bytes) -> str:
        """Stores bytes and returns their URI; storing equal bytes returns the same URI."""
        ...

    def get(self, uri: str) -> bytes:
        """Returns the bytes stored under a URI."""
        ...


class Workspace:
    """Exposes one show as files, addressing sets by display ID.

    Paths such as `/sets/2/set.py` map to the stored folder of the set's key and back, so a
    renumber moves no file (ADR 0004). The workspace reads at its own revision, like a working
    copy. Writes default to expecting
    that revision, so an edit made against a stale view raises ConflictError instead of
    overwriting a newer save. `refresh()` moves to the head; each successful write moves to
    the revision it created. Who may write what is the host's decision (ADR 0003).

    Attributes:
        repo: Storage backend.
        show_id: Show being edited.
    """

    def __init__(self, repo: Repository, show_id: str, revision: str):
        self.repo = repo
        self.show_id = show_id
        self._revision = revision
        self._trees: dict[str, dict[str, bytes]] = {}
        self._shows: dict[str, Show | None] = {}

    @property
    def revision(self) -> str:
        """Returns the revision this workspace reads at."""
        return self._revision

    def refresh(self) -> str:
        """Moves the workspace to the head revision and returns it."""
        self._revision = self.repo.head(self.show_id)
        return self._revision

    def tree_at(self, revision: str) -> dict[str, bytes]:
        """Returns the stored documents at a revision; revisions never change, so it caches."""
        if revision == ROOT_REVISION:
            return {}
        if revision not in self._trees:
            stored = {
                p: self.repo.read(self.show_id, p, revision)
                for p in self.repo.paths(self.show_id, revision)
            }
            self._trees = {revision: {p: d for p, d in stored.items() if d is not None}}
        return dict(self._trees[revision])

    def show_at(self, revision: str) -> Show | None:
        """Returns the show at a revision, or None before the first save; cached like trees."""
        if revision not in self._shows:
            tree = self.tree_at(revision)
            self._shows = {revision: decode_show(tree, revision) if SHOW_DOC in tree else None}
        return self._shows[revision]

    def _show(self) -> Show | None:
        return self.show_at(self._revision)

    def _tree(self) -> dict[str, bytes]:
        return self.tree_at(self._revision)

    def read(self, path: str) -> str | bytes:
        """Returns a file: text for documents, bytes for binary renders and exports.

        >>> from showpy.show.repositories import InMemoryRepository
        >>> from showpy.examples import two_set_show
        >>> from datetime import datetime, UTC
        >>> repo = InMemoryRepository()
        >>> _ = repo.put_show("show-1", two_set_show(), expected_revision="",
        ...                   at=datetime(2026, 11, 11, tzinfo=UTC))
        >>> open_workspace(repo, "show-1").read("/sets/2/placements.csv").splitlines()[1]
        'T1,0.0,9.0,0.0,,,False,True,'
        """
        split_path(path)
        return read_file(path, self._show(), self._tree())

    def ls(self, path: str = "/") -> list[str]:
        """Lists a directory: files by name, subdirectories with a trailing `/`."""
        segments = split_path(path)
        prefix = "/" + "".join(f"{s}/" for s in segments)
        show, tree = self._show(), self._tree()
        known = directories(show, tree)
        if prefix not in known:
            close = close_matches(prefix, sorted(known))
            raise UnknownRefError(
                f"no directory {path}",
                hint=f"did you mean {', '.join(close)}?" if close else "ls('/') lists the top",
                context={"path": path},
            )
        children = {
            f.removeprefix(prefix).split("/")[0] + ("/" if "/" in f.removeprefix(prefix) else "")
            for f in [*all_files(show, tree), *known]
            if f.startswith(prefix) and f != prefix
        }
        return sorted(children)

    def commit(
        self,
        changes: Mapping[str, bytes | None],
        *,
        at: datetime,
        expected_revision: str | None = None,
    ) -> Revision:
        """Saves file changes addressed by display set ID as one revision.

        A folder that names no set but matches another set's key, ignoring case, is refused
        with StorageError code AMBIGUOUS_PATH rather than written into that set's folder.

        Args:
            changes: Workspace path to new bytes, or None to delete.
            at: Time stamp of the revision.
            expected_revision: Revision the change was made against; default the workspace's.

        Returns:
            revision: The new head; the workspace moves to it.
        """
        base = self._revision if expected_revision is None else expected_revision
        for path in changes:
            split_path(path)
        folders = SetFolders(self.show_at(base))
        stored = {folders.to_stored(path): data for path, data in changes.items()}
        return self._commit_stored(stored, at=at, expected_revision=base)

    def _commit_stored(
        self,
        changes: Mapping[str, bytes | None],
        *,
        at: datetime,
        expected_revision: str | None = None,
    ) -> Revision:
        """Saves stored-document changes, addressed by set key, as one revision.

        Args:
            changes: Stored path to new bytes, or None to delete.
            at: Time stamp of the revision.
            expected_revision: Revision the change was made against; default the workspace's.

        Returns:
            revision: The new head; the workspace moves to it.
        """
        base = self._revision if expected_revision is None else expected_revision
        revision = self.repo.transaction(
            self.show_id,
            changes,
            expected_revision=base,
            at=at,
        )
        self._revision = revision.id
        return revision

    def write(
        self,
        path: str,
        data: str | bytes,
        expected_revision: str | None = None,
        *,
        at: datetime,
    ) -> Revision:
        """Validates and saves one file.

        Args:
            path: Workspace path.
            data: New content; text files take UTF-8.
            expected_revision: Revision the edit was made against; default the workspace's.
            at: Time stamp of the revision.

        Returns:
            revision: The new head.
        """
        split_path(path)
        raw = data.encode() if isinstance(data, str) else data
        changes = staged_changes(path, raw, self._show(), self._tree())
        return self._commit_stored(changes, at=at, expected_revision=expected_revision)


def open_workspace(repo: Repository, show_id: str) -> Workspace:
    """Opens a workspace on a show at its head revision.

    >>> from showpy.show.repositories import InMemoryRepository
    >>> open_workspace(InMemoryRepository(), "new-show").revision
    ''
    """
    return Workspace(repo, show_id, repo.head(show_id))


def load_show(ws: Workspace) -> Show:
    """Reads the whole show at the workspace revision; `show.revision` records it."""
    show = ws.show_at(ws.revision)
    if show is None:
        raise StorageError(
            f"show {ws.show_id} has no revisions yet",
            hint="save a show first with save_show()",
            context={"show": ws.show_id},
        )
    return show


def save_show(ws: Workspace, show: Show, at: datetime) -> Revision:
    """Writes the whole show against `show.revision`, the revision it was loaded at.

    Raises ConflictError listing every revision (short ID, time, paths) saved since, so the
    caller sees what changed meanwhile; reload, reapply and save again.

    >>> from showpy.show.repositories import InMemoryRepository
    >>> from showpy.examples import two_set_show
    >>> from datetime import datetime, UTC
    >>> ws = open_workspace(InMemoryRepository(), "show-1")
    >>> save_show(ws, two_set_show(), datetime(2026, 11, 11, tzinfo=UTC)).parent
    ''
    """
    base = ws.tree_at(show.revision)
    docs = encode_show(show)
    normalizing = _normalizing_changes(ws.show_at(show.revision), base, docs)
    if not normalizing:
        return ws._commit_stored(
            show_changes(show, base, docs), at=at, expected_revision=show.revision
        )
    normalized = ws._commit_stored(normalizing, at=at, expected_revision=show.revision)
    changes = show_changes(show, ws.tree_at(normalized.id), docs)
    return ws._commit_stored(changes, at=at, expected_revision=normalized.id)


def _normalizing_changes(
    stored: Show | None, tree: Mapping[str, bytes], edited: Mapping[str, bytes]
) -> dict[str, bytes | None]:
    """Lists stored show documents whose bytes differ from their canonical re-encoding.

    Only documents are listed, never scripts or other files, and nothing is deleted, so the
    decoded show is the same before and after (ADR 0014 item 5). A document the edited show
    encodes to its stored bytes is canonical already, so only the others are re-encoded.
    """
    if stored is None:
        return {}
    differing = [p for p, d in edited.items() if p in tree and tree[p] != d]
    canonical = encode_documents(stored, differing)
    return {p: d for p, d in canonical.items() if tree[p] != d}
