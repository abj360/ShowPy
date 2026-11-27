"""
documents.py --- splits a Show into canonical JSON documents at stored paths and back

show.json starts with the format header and the rest of every document is canonical, so equal
shows give equal bytes. Reading accepts every readable format version: document_reading parses
strictly and migrates older trees in memory. The stored-path names of document_paths and the
canonical writers of document_dump are re-exported here for existing importers.

Contains:
    SHOW_DOC: names the document holding show metadata, set order and the format header
    dump_json(), dump_dumped_json(), dump_show_doc(), dump_headed_json(): re-export the
        canonical writers of document_dump
    require_storable(): refuses a show whose set IDs, keys, paths, forms or pins cannot be stored
    expected_show_docs(): lists the stored show-document paths decode reads for a show
    encode_show(): splits a show into documents stored by set key, show.json header first, each
        set as set.json and role documents and the cast as /cast/cast.json
    encode_documents(): encodes only the named stored documents of a show
    decode_show(): assembles a show from documents of any readable format version
    migrate_documents(): migrates a stored document set to the current format and reports it
    MigratedRead: names what read_migrated returns, so a caller can pass it on
    Migration: holds a rewrite's writes and the show read while computing them
    migration_of(): computes the rewrite of a tree, decoding its show once
    migration_changes(): lists the writes and deletes that store a migrated document set
    is_older_tree(): tells whether stored documents hold a show of an older format version
    needs_rewrite(): tells whether stored documents hold an older format or layout-1 documents
    MigratedView: reads stored documents as the migration would store them, reading blobs lazily
    migrated_view(): returns stored documents as the migration would store them
"""

from collections.abc import Collection, Iterator, Mapping
from typing import NamedTuple

from e8s.core.errors import StorageError
from e8s.core.json_types import JsonValue
from e8s.show.canvas_renames import renamed_canvas
from e8s.show.decode_cache import DecodeCache
from e8s.show.document_dump import dump_dumped_json as dump_dumped_json
from e8s.show.document_dump import dump_headed_json as dump_headed_json
from e8s.show.document_dump import dump_json as dump_json
from e8s.show.document_dump import dump_show_doc as dump_show_doc
from e8s.show.document_paths import CAST_DOC, docs_for_order, is_set_document
from e8s.show.document_paths import FORMS_NAME as FORMS_NAME
from e8s.show.document_paths import LEGACY_NOTES_DOC as LEGACY_NOTES_DOC
from e8s.show.document_paths import NOTES_DOC as NOTES_DOC
from e8s.show.document_paths import PATHS_NAME as PATHS_NAME
from e8s.show.document_paths import PLACEMENTS_NAME as PLACEMENTS_NAME
from e8s.show.document_paths import SCRIPT_NAME as SCRIPT_NAME
from e8s.show.document_paths import SET_ORDER_FIELD as SET_ORDER_FIELD
from e8s.show.document_paths import SHEET_DOC as SHEET_DOC
from e8s.show.document_paths import TIMELINE_DOC as TIMELINE_DOC
from e8s.show.document_paths import forms_doc as forms_doc
from e8s.show.document_paths import is_show_doc as is_show_doc
from e8s.show.document_paths import is_storable_folder as is_storable_folder
from e8s.show.document_paths import paths_doc as paths_doc
from e8s.show.document_paths import placements_doc as placements_doc
from e8s.show.document_paths import script_doc as script_doc
from e8s.show.document_paths import set_doc as set_doc
from e8s.show.document_paths import split_path as split_path
from e8s.show.document_reading import read_migrated, read_show
from e8s.show.document_sources import load_object, raw_source
from e8s.show.encode_cache import EncodeCache, document_groups, encode_groups
from e8s.show.migrate import (
    CURRENT_VERSION,
    MigrationReport,
    ReportBuilder,
    read_version,
    without_header,
)
from e8s.show.migrate import SHOW_DOC as SHOW_DOC
from e8s.show.models import Show
from e8s.show.role_documents import holds_layout1_documents
from e8s.show.set_keys import set_key
from e8s.show.storable import require_storable as require_storable

SETS_ROOT = "sets"
SET_FOLDER_INDEX = 2


def expected_show_docs(show: Show) -> frozenset[str]:
    """Returns every stored show-document path decode_show() reads for `show`, by set key.

    >>> from e8s.examples import two_set_show
    >>> "/transitions/1-2/paths.json" in expected_show_docs(two_set_show())
    True
    """
    return docs_for_order([set_key(s) for s in show.sets])


def encode_show(show: Show, *, cache: EncodeCache | None = None) -> dict[str, bytes]:
    """Splits a show into documents under set keys; empty forms and paths are left out.

    show.json starts with `"format"` and `"version"`; the rest of every document is canonical.
    Each set is stored as set.json plus its drill, facing and labels role documents, and the
    cast as /cast/cast.json, out of show.json.

    Args:
        show: Show to encode; one that cannot be stored raises.
        cache: Encoded documents to reuse, keyed by the identity of their sources; the bytes
            are the same with or without it.

    Returns:
        documents: Stored path to canonical bytes.

    >>> from e8s.examples import two_set_show
    >>> sorted(encode_show(two_set_show()))[:4]
    ['/cast/cast.json', '/sets/1/drill.json', '/sets/1/facing.json', '/sets/1/labels.json']
    >>> encode_show(two_set_show())["/show/show.json"].splitlines()[1:3]
    [b'  "format": "e8.show",', b'  "version": 2,']
    """
    if cache is not None:
        return cache.documents(show)
    return encode_groups(document_groups(show))


def encode_documents(show: Show, paths: Collection[str]) -> dict[str, bytes]:
    """Encodes only the named stored documents of a show; paths the show does not write are skipped.

    >>> from e8s.examples import two_set_show
    >>> sorted(encode_documents(two_set_show(), ["/show/sheet.json", "/sets/9/set.json"]))
    ['/show/sheet.json']
    """
    wanted = set(paths)
    groups = [g for g in document_groups(show) if not wanted.isdisjoint(g.paths)]
    return {path: raw for path, raw in encode_groups(groups).items() if path in wanted}


def decode_show(
    docs: Mapping[str, bytes],
    revision: str = "",
    *,
    cache: DecodeCache | None = None,
    digests: Mapping[str, str] | None = None,
) -> Show:
    """Assembles a show from documents of any readable format; paths take the target's counts.

    Documents of an older format version are migrated in memory; nothing stored changes.

    Args:
        docs: Path to document bytes; documents other than show documents are ignored.
        revision: Revision the documents were read at.
        cache: Decoded documents to reuse; the show is still validated whole. Documents of an
            older format version are never cached.
        digests: Path to blob digest of each document, naming the cache entries; without it
            nothing is cached.

    Returns:
        show: The assembled show. A document that is not strict JSON or does not fit raises
            SchemaError; a format version newer than this release reads raises VersionError.

    >>> from e8s.examples import two_set_show
    >>> decode_show(encode_show(two_set_show())) == two_set_show()
    True
    """
    return read_show(docs, revision, cache=cache, digests=digests)


def migrate_documents(docs: Mapping[str, bytes]) -> tuple[dict[str, bytes], MigrationReport]:
    """Migrates a stored document set to the current format version and reports what changed.

    A host can show the report to the designer, and apply its renames to what it stores outside
    e8Show, before the first save writes the new documents. The notes, the canvas comments and
    the selection follow the set and spot renames here; `/publish/claims.json` is host-kept, so
    the host applies the report's spot renames to its claims.

    Args:
        docs: Stored path to document bytes, as a repository tree holds them.

    Returns:
        migrated: The show documents re-encoded in the current format, the notes document beside
            them when the tree held notes (`/notes/notes.json`, in place of `/show/notes.json`),
            the canvas comments and selection with renamed IDs, plus every other file of `docs`,
            moved to its set's new folder when the migration renamed the set's key; `docs`
            itself when it is already current.
        report: What the migration renamed, repaired and dropped, and the version it started from.

    >>> from e8s.examples import two_set_show
    >>> docs = encode_show(two_set_show())
    >>> migrated, report = migrate_documents(docs)
    >>> migrated == docs, report.from_version
    (True, 2)
    """
    version = read_version(docs)
    if version == CURRENT_VERSION:
        return dict(docs), ReportBuilder(version).build()
    _, encoded, folders, report = _migrated_parts(docs)
    others = {path: raw for path, raw in docs.items() if not is_show_doc(path)}
    return _moved_set_files(others, folders, set(docs)) | encoded, report


type MigratedRead = tuple[Show, ReportBuilder, dict[str, JsonValue]]


def _migrated_parts(
    docs: Mapping[str, bytes], read: MigratedRead | None = None
) -> tuple[Show, dict[str, bytes], dict[str, str], MigrationReport]:
    """Migrates an older tree's show documents, notes and canvas files; maps renamed set folders.

    Returns the migrated show beside the documents it encodes to.
    """
    show, report, beside = read_migrated(docs) if read is None else read
    order = without_header(load_object(raw_source(docs), SHOW_DOC)).get(SET_ORDER_FIELD, [])
    stored_keys = [key for key in order if isinstance(key, str)] if isinstance(order, list) else []
    folders = {
        old: new
        for old, new in zip(stored_keys, (set_key(s) for s in show.sets), strict=True)
        if old != new
    }
    built = report.build()
    canvas = {path: dump_json(value) for path, value in renamed_canvas(docs, built).items()}
    written = {path: _dump_beside(value) for path, value in beside.items()}
    return show, encode_show(show) | written | canvas, folders, built


def _dump_beside(value: JsonValue) -> bytes:
    """Writes a document beside the show canonically, its header first when it is an object."""
    return dump_headed_json(value) if isinstance(value, dict) else dump_json(value)


def _set_folder(path: str) -> str | None:
    """Returns the set folder a stored path lies under, or None outside `/sets/<folder>/`."""
    segments = path.split("/")
    if len(segments) > SET_FOLDER_INDEX + 1 and segments[1] == SETS_ROOT:
        return segments[SET_FOLDER_INDEX]
    return None


def _moved_set_files(
    files: Mapping[str, bytes], folders: Mapping[str, str], stored: Collection[str]
) -> dict[str, bytes]:
    """Moves each file under a renamed set folder to the new folder, at the same relative path.

    Args:
        files: Stored path to bytes of files that are not show documents.
        folders: Old set folder to new set folder, for each set whose key the migration renamed.
        stored: Every path the tree stores, so a move never lands on one of them.

    Returns:
        files: The same files, those under a renamed folder at their new path. A file already
            stored at a new path raises StorageError, so nothing is overwritten.
    """
    moved: dict[str, bytes] = {}
    for path, raw in files.items():
        folder = _set_folder(path)
        target = path
        if folder is not None and folder in folders:
            relative = path[len(f"/{SETS_ROOT}/{folder}/") :]
            target = f"/{SETS_ROOT}/{folders[folder]}/{relative}"
        if target != path and target in stored:
            raise StorageError(
                f"{path} moves to {target} with its renamed set, but {target} is already stored",
                hint=f"move or delete {target} with showpy 0.3; the first save under this release"
                f" moves {path} there",
                context={"path": path, "target": target},
            )
        moved[target] = raw
    return moved


class Migration(NamedTuple):
    """Holds what the first save over a tree changes, and the show it reads from the tree.

    Attributes:
        show: The show the tree holds at revision "", or None when the tree needs no rewrite.
        changes: The writes, and None for each path the rewrite leaves behind.
    """

    show: Show | None
    changes: dict[str, bytes | None]


def migration_of(docs: Mapping[str, bytes], read: MigratedRead | None = None) -> Migration:
    """Computes the rewrite the first save makes over a tree, decoding the show once.

    Only show documents and the files of renamed set folders are read, so large renders and
    exports stay unread. A tree of the current format that still stores a layout-1 set or cast
    is re-encoded as role documents, which changes no content.

    Args:
        docs: Stored path to document bytes, as a repository tree holds them.
        read: What `read_migrated` returned for an older `docs`, when the caller already has it.

    Returns:
        migration: The tree's show with the writes, and None for each path the migration leaves
            behind (the old folder of a renamed set); no show and no changes when `docs` is
            already current and stored as role documents.

    >>> from e8s.examples import two_set_show
    >>> migration_of(encode_show(two_set_show()))
    Migration(show=None, changes={})
    """
    if not is_older_tree(docs):
        if not _holds_listed_layout1(docs):
            return Migration(None, {})
        show = read_show(docs)
        encoded = encode_show(show)
        return Migration(show, {p: d for p, d in encoded.items() if docs.get(p) != d})
    show, encoded, folders, _ = _migrated_parts(docs, read)
    moving = [p for p in docs if not is_show_doc(p) and _set_folder(p) in folders]
    changes: dict[str, bytes | None] = {p: d for p, d in encoded.items() if docs.get(p) != d}
    changes.update(_moved_set_files({p: docs[p] for p in moving}, folders, set(docs)))
    changes.update(dict.fromkeys(p for p in docs if is_show_doc(p) and p not in encoded))
    changes.update(dict.fromkeys(moving))
    return Migration(show, changes)


def migration_changes(docs: Mapping[str, bytes]) -> dict[str, bytes | None]:
    """Lists the writes and deletes that turn a stored document set into its migrated form.

    Args:
        docs: Stored path to document bytes, as a repository tree holds them.

    Returns:
        changes: Stored path to new bytes, or None for a path the migration leaves behind
            (the old folder of a renamed set); empty when `docs` is already current and stored
            as role documents.

    >>> from e8s.examples import two_set_show
    >>> migration_changes(encode_show(two_set_show()))
    {}
    """
    return migration_of(docs).changes


def _holds_listed_layout1(docs: Mapping[str, bytes]) -> bool:
    """Tells whether a tree stores layout-1 documents of a set it lists, ignoring unlisted sets.

    A set document of a set the show does not list stays as it is (nothing reads it), so it must
    not keep a re-encoded tree looking like layout 1.
    """
    if not holds_layout1_documents(docs):
        return False
    if CAST_DOC not in docs:
        return True
    order = without_header(load_object(raw_source(docs), SHOW_DOC)).get(SET_ORDER_FIELD, [])
    listed = {key for key in order if isinstance(key, str)} if isinstance(order, list) else set()
    return holds_layout1_documents(
        {p for p in docs if not is_set_document(p) or p.split("/")[2] in listed}
    )


def needs_rewrite(docs: Mapping[str, bytes]) -> bool:
    """Tells whether the first save rewrites stored documents: an older format or layout 1.

    Layout-1 documents of a set the show does not list are ignored, since the rewrite keeps them.

    >>> from e8s.examples import two_set_show
    >>> needs_rewrite(encode_show(two_set_show())), needs_rewrite({})
    (False, False)
    """
    return is_older_tree(docs) or _holds_listed_layout1(docs)


def is_older_tree(docs: Mapping[str, bytes]) -> bool:
    """Tells whether stored documents hold a show whose format version is older than current.

    >>> from e8s.examples import two_set_show
    >>> is_older_tree(encode_show(two_set_show())), is_older_tree({})
    (False, False)
    """
    return SHOW_DOC in docs and read_version(docs) < CURRENT_VERSION


class MigratedView(Mapping[str, bytes]):
    """Reads stored documents as the migration would store them, reading blobs lazily.

    Attributes:
        changes: The migration's writes, and None for each path it leaves behind.
        show: The show the migration read from the documents, at revision "".
    """

    def __init__(self, docs: Mapping[str, bytes], migration: Migration) -> None:
        self._docs = docs
        self.changes = migration.changes
        self.show = migration.show

    def __getitem__(self, path: str) -> bytes:
        if path not in self.changes:
            return self._docs[path]
        written = self.changes[path]
        if written is None:
            raise KeyError(path)
        return written

    def __iter__(self) -> Iterator[str]:
        yield from (p for p in self._docs if p not in self.changes)
        yield from (p for p, d in self.changes.items() if d is not None)

    def __len__(self) -> int:
        return sum(1 for _ in self)


def migrated_view(
    docs: Mapping[str, bytes], read: MigratedRead | None = None
) -> Mapping[str, bytes]:
    """Returns stored documents as the first save would store them, so reads see renamed folders.

    Args:
        docs: Stored path to document bytes, as a repository tree holds them.
        read: What `read_migrated` returned for an older `docs`, when the caller already has it.

    Returns:
        view: `docs` itself when it is current; otherwise a MigratedView in which every file of
            a renamed set folder reads at its new path and nothing reads at the old one.

    >>> from e8s.examples import two_set_show
    >>> docs = encode_show(two_set_show())
    >>> migrated_view(docs) is docs
    True
    """
    migration = migration_of(docs, read)
    return MigratedView(docs, migration) if migration.changes else docs
