"""
workspace.py --- the file view of one show at one revision, with its commit and write paths

Contains:
    WORKSPACE_DECODE_CACHE_SIZE: bounds the documents one workspace keeps decoded
    Workspace: exposes one show as files, reading at its own revision like a working copy
    Workspace.read(): returns a file's text or bytes
    Workspace.write(): validates and saves one file
    Workspace.ls(): lists a directory
    Workspace.commit(): saves a batch of file changes addressed by set ID as one revision
    Workspace.refresh(): moves the workspace to the head revision
    Workspace.show_at(): returns the show at a revision, or None before the first save
    Workspace.tree_at(): returns the stored documents at a revision
    open_workspace(): opens a workspace at the head revision
"""

from collections.abc import Collection, Mapping
from datetime import datetime

from e8s.core.errors import UnknownRefError
from e8s.core.util import close_matches
from e8s.show.decode_cache import DecodeCache
from e8s.show.documents import (
    SHOW_DOC,
    decode_show,
    encode_documents,
    is_older_tree,
    is_show_doc,
    migrated_view,
    migration_changes,
    split_path,
)
from e8s.show.files import (
    all_files,
    directories,
    read_file,
    staged_changes,
)
from e8s.show.integrity import LazyBlobs
from e8s.show.models import Revision, Show
from e8s.show.set_keys import SetFolders
from e8s.show.storage import ROOT_REVISION, Repository

# A tree keeps three documents per set (set, forms, paths): two trees of a 60-set show are about
# 360 documents, so 512 holds the revision being read and its parent, about 35 MB at 300 spots.
WORKSPACE_DECODE_CACHE_SIZE = 512


class Workspace:
    """Exposes one show as files, addressing sets by display ID.

    Paths such as `/sets/2/set.py` map to the stored folder of the set's key and back, so a
    renumber moves no file. The workspace reads at its own revision, like a working
    copy. Writes default to expecting
    that revision, so an edit made against a stale view raises ConflictError instead of
    overwriting a newer save. `refresh()` moves to the head; each successful write moves to
    the revision it created. Who may write what is the host's decision.

    Attributes:
        repo: Storage backend.
        show_id: Show being edited.
    """

    def __init__(self, repo: Repository, show_id: str, revision: str):
        self.repo = repo
        self.show_id = show_id
        self._revision = revision
        self._trees: dict[str, Mapping[str, bytes]] = {}
        self._views: dict[str, Mapping[str, bytes]] = {}
        self._shows: dict[str, Show | None] = {}
        # Serves only show_at, which validates with one context, so entries never mix contexts.
        self._decode_cache = DecodeCache(WORKSPACE_DECODE_CACHE_SIZE)
        self._canonical_revisions: set[str] = set()

    @property
    def revision(self) -> str:
        """Returns the revision this workspace reads at."""
        return self._revision

    def refresh(self) -> str:
        """Moves the workspace to the head revision and returns it."""
        self._revision = self.repo.head(self.show_id)
        return self._revision

    def tree_at(self, revision: str) -> Mapping[str, bytes]:
        """Returns the stored documents at a revision, reading each file's bytes on lookup.

        Revisions never change, so the last tree is cached.
        """
        if revision == ROOT_REVISION:
            return {}
        if revision not in self._trees:
            self._trees = {revision: self.repo.documents(self.show_id, revision)}
        return self._trees[revision]

    def show_at(self, revision: str) -> Show | None:
        """Returns the show at a revision, or None before the first save; cached like trees.

        Documents whose content digest an earlier call already decoded are reused, so stepping
        through revisions decodes only what each one changed; the show is still validated whole.
        """
        if revision not in self._shows:
            tree = self.tree_at(revision)
            self._shows = {revision: self._decoded(tree, revision) if SHOW_DOC in tree else None}
        return self._shows[revision]

    def _decoded(self, tree: Mapping[str, bytes], revision: str) -> Show:
        """Decodes a tree, reusing documents whose blob digest an earlier revision decoded."""
        digests = tree.digests if isinstance(tree, LazyBlobs) else None
        return decode_show(tree, revision, cache=self._decode_cache, digests=digests)

    def _show(self) -> Show | None:
        return self.show_at(self._revision)

    def _tree(self) -> Mapping[str, bytes]:
        return self.tree_at(self._revision)

    def _view(self) -> Mapping[str, bytes]:
        """Returns the tree at the workspace revision as the first save would store it."""
        if self._revision not in self._views:
            self._views = {self._revision: migrated_view(self._tree())}
        return self._views[self._revision]

    def read(self, path: str) -> str | bytes:
        """Returns a file: text for documents, bytes for binary renders and exports.

        At a revision of an older format version, files read where the first save stores them,
        so a set's files read from the folder its migrated key names.

        >>> from e8s.show.memory_backend import InMemoryRepository
        >>> from e8s.examples import two_set_show
        >>> from datetime import datetime, UTC
        >>> repo = InMemoryRepository()
        >>> _ = repo.put_show("show-1", two_set_show(), expected_revision="",
        ...                   at=datetime(2026, 11, 11, tzinfo=UTC))
        >>> open_workspace(repo, "show-1").read("/sets/2/placements.csv").splitlines()[1]
        'T1,0.0,9.0,0.0,,,False,True,'
        """
        split_path(path)
        return read_file(path, self._show(), self._view())

    def ls(self, path: str = "/") -> list[str]:
        """Lists a directory: files by name, subdirectories with a trailing `/`."""
        segments = split_path(path)
        prefix = "/" + "".join(f"{s}/" for s in segments)
        show, tree = self._show(), self._view()
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
        return self._commit_normalizing(stored, at=at, expected_revision=base)

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

    def _commit_normalizing(
        self,
        changes: Mapping[str, bytes | None],
        *,
        at: datetime,
        expected_revision: str | None = None,
        is_complete: bool = False,
    ) -> Revision:
        """Saves stored-document changes after re-encoding non-canonical documents on their own.

        When any stored show document of the base revision is not canonical, one revision holding
        only those re-encoded documents lands first and the change follows, so the change's own
        revision lists only what it edits and later writes add no second one. If the
        change then conflicts, the normalizing revision stays; it changes no content. A base of
        an older format version is migrated instead: the normalizing revision holds the whole
        migration, files of renamed set folders moved. A revision found or made canonical is
        remembered, so the check runs once per revision.

        Args:
            changes: Stored path to new bytes, or None to delete.
            at: Time stamp of the revisions.
            expected_revision: Revision the change was made against; default the workspace's.
            is_complete: Whether `changes` holds every show document that differs from the
                canonical encoding of the edited show, as show_changes() lists them; then only
                those documents need re-encoding to find the non-canonical ones.

        Returns:
            revision: The new head; the workspace moves to it.
        """
        base = self._revision if expected_revision is None else expected_revision
        normalizing = self._normalizing_for(base, changes, is_complete=is_complete)
        if not normalizing:
            return self._remembered(
                self._commit_stored(changes, at=at, expected_revision=base),
                changes,
                is_complete=is_complete,
            )
        normalized = self._commit_stored(normalizing, at=at, expected_revision=base)
        self._canonical_revisions.add(normalized.id)
        stored = self.tree_at(normalized.id)
        remaining = {p: d for p, d in changes.items() if stored.get(p) != d}
        return self._remembered(
            self._commit_stored(remaining, at=at, expected_revision=normalized.id),
            changes,
            is_complete=is_complete,
        )

    def _normalizing_for(
        self, base: str, changes: Mapping[str, bytes | None], *, is_complete: bool
    ) -> dict[str, bytes | None]:
        """Lists the re-encodings a change over `base` needs first, empty for a known revision."""
        if base in self._canonical_revisions:
            return {}
        tree = self.tree_at(base)
        if is_older_tree(tree):
            return migration_changes(tree)
        if is_complete:
            paths = [p for p, d in changes.items() if d is not None and p in tree and tree[p] != d]
        else:
            paths = [p for p in tree if is_show_doc(p)]
        normalizing = _normalizing_changes(self.show_at(base), tree, paths)
        if not normalizing:
            self._canonical_revisions.add(base)
        return normalizing

    def _remembered(
        self, revision: Revision, changes: Mapping[str, bytes | None], *, is_complete: bool
    ) -> Revision:
        """Remembers a new revision as canonical when its change wrote only canonical documents."""
        if is_complete or not any(is_show_doc(p) for p in changes):
            self._canonical_revisions.add(revision.id)
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
        return self._commit_normalizing(
            changes, at=at, expected_revision=expected_revision, is_complete=is_show_doc(path)
        )


def open_workspace(repo: Repository, show_id: str) -> Workspace:
    """Opens a workspace on a show at its head revision.

    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> open_workspace(InMemoryRepository(), "new-show").revision
    ''
    """
    return Workspace(repo, show_id, repo.head(show_id))


def _normalizing_changes(
    stored: Show | None, tree: Mapping[str, bytes], paths: Collection[str]
) -> dict[str, bytes | None]:
    """Lists the named stored show documents whose bytes differ from their canonical encoding.

    Only documents are listed, never scripts or other files, and nothing is deleted, so the
    decoded show is the same before and after.
    """
    if stored is None:
        return {}
    canonical = encode_documents(stored, paths)
    return {p: d for p, d in canonical.items() if tree[p] != d}
