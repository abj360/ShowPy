"""
documents.py --- splits a Show into canonical JSON documents at stored paths and back

show.json starts with the format header and the rest of every document is canonical, so equal
shows give equal bytes. Reading accepts every readable format version: document_reading parses
strictly and migrates older trees in memory. The stored-path names of document_paths are
re-exported here for existing importers.

Contains:
    SHOW_DOC: names the document holding show metadata, cast, set order and the format header
    dump_json(): writes JSON canonically: sorted keys, sorted sets, two-space indent
    dump_dumped_json(): writes the JSON-mode dump of a model canonically, skipping a second walk
    dump_show_doc(): writes a show.json object canonically, the format header first
    require_storable(): refuses a show whose set IDs, keys, paths or forms cannot be stored
    expected_show_docs(): lists the stored show-document paths decode reads for a show
    encode_show(): splits a show into documents stored by set key, show.json header first
    encode_documents(): encodes only the named stored documents of a show
    decode_show(): assembles a show from documents of any readable format version
    migrate_documents(): migrates a stored document set to the current format and reports it
    migration_changes(): lists the writes and deletes that store a migrated document set
    is_older_tree(): tells whether stored documents hold a show of an older format version
"""

import json
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from functools import partial
from typing import Any

from pydantic import BaseModel

from showpy.core.canonical import MAX_INT_DIGITS, MAX_NESTING, canonical_value
from showpy.core.errors import SchemaError, StorageError
from showpy.core.json_types import JsonValue
from showpy.show.canvas_renames import renamed_canvas
from showpy.show.decode_cache import DecodeCache
from showpy.show.document_paths import FORMS_NAME as FORMS_NAME
from showpy.show.document_paths import NOTES_DOC as NOTES_DOC
from showpy.show.document_paths import PATHS_NAME as PATHS_NAME
from showpy.show.document_paths import PLACEMENTS_NAME as PLACEMENTS_NAME
from showpy.show.document_paths import SCRIPT_NAME as SCRIPT_NAME
from showpy.show.document_paths import SET_ORDER_FIELD as SET_ORDER_FIELD
from showpy.show.document_paths import SHEET_DOC as SHEET_DOC
from showpy.show.document_paths import SPLIT_FIELDS, docs_for_order
from showpy.show.document_paths import TIMELINE_DOC as TIMELINE_DOC
from showpy.show.document_paths import forms_doc as forms_doc
from showpy.show.document_paths import is_show_doc as is_show_doc
from showpy.show.document_paths import is_storable_folder as is_storable_folder
from showpy.show.document_paths import paths_doc as paths_doc
from showpy.show.document_paths import placements_doc as placements_doc
from showpy.show.document_paths import script_doc as script_doc
from showpy.show.document_paths import set_doc as set_doc
from showpy.show.document_paths import split_path as split_path
from showpy.show.document_reading import read_migrated, read_show
from showpy.show.manage import SET_ID_PATTERN
from showpy.show.migrate import (
    CURRENT_VERSION,
    MigrationReport,
    ReportBuilder,
    format_header,
    read_version,
    require_current_version,
    without_header,
)
from showpy.show.migrate import SHOW_DOC as SHOW_DOC
from showpy.show.models import Show
from showpy.show.set_keys import set_key

JSON_INDENT = 2
NEGATIVE_ZERO = re.compile(r"-0\.0\b")
RESERVED_KEY_PREFIX = "$showpy:"
TOO_DEEP = "\n" + " " * (JSON_INDENT * (MAX_NESTING + 1))
HEADER_CLOSE = b"\n}\n"
SETS_ROOT = "sets"
SET_FOLDER_INDEX = 2
OPEN_OBJECT = b"{\n"


# Any: accepts any JSON-like value or model dump.
def dump_json(value: Any) -> bytes:
    r"""Writes JSON canonically so equal content always gives equal bytes and hashes.

    >>> dump_json({"b": frozenset({"y", "x"}), "a": 1})
    b'{\n  "a": 1,\n  "b": [\n    "x",\n    "y"\n  ]\n}\n'
    """
    text = json.dumps(
        canonical_value(value), indent=JSON_INDENT, sort_keys=True, ensure_ascii=False
    )
    return f"{text}\n".encode()


# Any: a model_dump(mode="json") result, whose JSON types and string keys are guaranteed.
def dump_dumped_json(dumped: Any) -> bytes:
    """Writes the JSON-mode dump of a model with the same bytes as dump_json, much faster.

    The dump already holds only JSON types and string keys, so one `json.dumps` pass is enough
    unless the text shows a value canonical_value must treat: `-0.0`, a reserved key prefix, a
    line long enough to hold a huge int or indented deeper than canonical_value allows. NaN,
    infinity and lone surrogates make the pass fail, which hands the value to dump_json so it
    raises the same SchemaError.

    >>> dump_dumped_json({"b": [1.5, None], "a": -0.0}) == dump_json({"b": [1.5, None], "a": -0.0})
    True
    """
    try:
        text = json.dumps(
            dumped, indent=JSON_INDENT, sort_keys=True, ensure_ascii=False, allow_nan=False
        )
        encoded = f"{text}\n".encode()
    except (ValueError, UnicodeEncodeError):
        return dump_json(dumped)
    if _needs_normalizing(text):
        return dump_json(dumped)
    return encoded


def _needs_normalizing(text: str) -> bool:
    """Reports whether JSON text may hold a value that only canonical_value treats correctly.

    With one value per line and two spaces per level, an int past the digit cap of canonical_value
    makes a line longer than the cap, and a value nested past its depth cap makes a line indented
    past TOO_DEEP, so line length and indent stand in for a slow scan of every digit run.
    """
    if RESERVED_KEY_PREFIX in text:
        return True
    if "-0.0" in text and NEGATIVE_ZERO.search(text):
        return True
    if TOO_DEEP in text:
        return True
    return max(map(len, text.split("\n"))) > MAX_INT_DIGITS


def require_storable(show: Show) -> None:
    """Refuses a show the stored layout cannot hold: bad set IDs or keys, loose paths or forms.

    >>> from showpy.examples import two_set_show
    >>> require_storable(two_set_show())
    """
    bad: list[str] = [s for s in show.set_ids() if not SET_ID_PATTERN.fullmatch(s) or ".." in s]
    bad += [set_key(s) for s in show.sets if not is_storable_folder(set_key(s))]
    pairs = set(show.transition_ids())
    loose = [spec.id for spec in show.transitions if spec.id not in pairs]
    orphans = [f for f, form in show.forms.items() if form.set not in show.set_ids()]
    if bad or loose or orphans:
        raise SchemaError(
            "show cannot be stored",
            hint="use set ids of letters, digits, _ and .; store paths only for consecutive "
            "sets; give every form an existing set",
            context={"bad_set_ids": bad, "loose_transitions": loose, "orphan_forms": orphans},
        )


def expected_show_docs(show: Show) -> frozenset[str]:
    """Returns every stored show-document path decode_show() reads for `show`, by set key.

    >>> from showpy.examples import two_set_show
    >>> "/transitions/1-2/paths.json" in expected_show_docs(two_set_show())
    True
    """
    return docs_for_order([set_key(s) for s in show.sets])


def encode_show(show: Show) -> dict[str, bytes]:
    """Splits a show into documents under set keys; empty forms and paths are left out.

    show.json starts with `"format"` and `"version"`; the rest of every document is canonical.

    >>> from showpy.examples import two_set_show
    >>> sorted(encode_show(two_set_show()))[:3]
    ['/sets/1/set.json', '/sets/2/set.json', '/show/notes.json']
    >>> encode_show(two_set_show())["/show/show.json"].splitlines()[1:3]
    [b'  "format": "showpy.show",', b'  "version": 2,']
    """
    return {path: encode() for path, encode in _encoders(show).items()}


def encode_documents(show: Show, paths: Collection[str]) -> dict[str, bytes]:
    """Encodes only the named stored documents of a show; paths the show does not write are skipped.

    >>> from showpy.examples import two_set_show
    >>> sorted(encode_documents(two_set_show(), ["/show/sheet.json", "/sets/9/set.json"]))
    ['/show/sheet.json']
    """
    encoders = _encoders(show)
    return {path: encoders[path]() for path in paths if path in encoders}


def _encoders(show: Show) -> dict[str, Callable[[], bytes]]:
    """Maps each stored document path of a show to a call that encodes it."""
    require_storable(show)
    keys = {s.id: set_key(s) for s in show.sets}
    meta = show.model_dump(mode="json", exclude=set(SPLIT_FIELDS))
    encoders: dict[str, Callable[[], bytes]] = {
        SHOW_DOC: lambda: dump_show_doc(
            {**format_header(), **meta, SET_ORDER_FIELD: list(keys.values())}
        ),
        SHEET_DOC: lambda: dump_dumped_json(show.sheet.model_dump(mode="json")),
        NOTES_DOC: lambda: dump_dumped_json([n.model_dump(mode="json") for n in show.notes]),
        TIMELINE_DOC: lambda: dump_dumped_json(show.timeline.model_dump(mode="json")),
    }
    for current in show.sets:
        folder = keys[current.id]
        encoders[set_doc(folder)] = partial(_dump_model, current)
        forms = [f for _, f in sorted(show.forms.items()) if f.set == current.id]
        if forms:
            encoders[forms_doc(folder)] = partial(_dump_models, forms)
    for spec in show.transitions:
        folder_pair = (keys[spec.id[0]], keys[spec.id[1]])
        encoders[paths_doc(folder_pair)] = partial(_dump_model, spec)
    return encoders


def dump_show_doc(document: Mapping[str, JsonValue]) -> bytes:
    """Writes a show.json object canonically, with the format header as its first two keys.

    Use it to commit a hand-edited show.json: dump_json() would sort the header among the
    other keys, which reads the same but is not the canonical encoding.

    Args:
        document: Parsed show.json object in the current format version, header included.

    Returns:
        raw: Its canonical bytes. A document of another format version raises SchemaError
            code OLD_FORMAT, or VersionError when it is newer.

    >>> from showpy.examples import two_set_show
    >>> raw = encode_show(two_set_show())[SHOW_DOC]
    >>> dump_show_doc(json.loads(raw)) == raw
    True
    """
    require_current_version(document, name=SHOW_DOC)
    head = dump_dumped_json(format_header())
    body = without_header(document)
    if not body:
        return head
    rest = dump_dumped_json(body).removeprefix(OPEN_OBJECT)
    return head.removesuffix(HEADER_CLOSE) + b",\n" + rest


def _dump_model(model: BaseModel) -> bytes:
    return dump_dumped_json(model.model_dump(mode="json"))


def _dump_models(models: Sequence[BaseModel]) -> bytes:
    return dump_dumped_json([m.model_dump(mode="json") for m in models])


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

    >>> from showpy.examples import two_set_show
    >>> decode_show(encode_show(two_set_show())) == two_set_show()
    True
    """
    return read_show(docs, revision, cache=cache, digests=digests)


def migrate_documents(docs: Mapping[str, bytes]) -> tuple[dict[str, bytes], MigrationReport]:
    """Migrates a stored document set to the current format version and reports what changed.

    A host can show the report to the designer, and apply its renames to what it stores outside
    ShowPy, before the first save writes the new documents. The canvas comments and selection
    follow the set and spot renames here; `/publish/claims.json` is host-kept, so the host
    applies the report's spot renames to its claims.

    Args:
        docs: Stored path to document bytes, as a repository tree holds them.

    Returns:
        migrated: The show documents re-encoded in the current format, the canvas comments and
            selection with renamed IDs, plus every other file of `docs`, moved to its set's
            new folder when the migration renamed the set's key; `docs` itself when it is
            already current.
        report: What the migration renamed and repaired, and the version it started from.

    >>> from showpy.examples import two_set_show
    >>> docs = encode_show(two_set_show())
    >>> migrated, report = migrate_documents(docs)
    >>> migrated == docs, report.from_version
    (True, 2)
    """
    version = read_version(docs)
    if version == CURRENT_VERSION:
        return dict(docs), ReportBuilder(version).build()
    encoded, folders, report = _migrated_parts(docs)
    others = {path: raw for path, raw in docs.items() if not is_show_doc(path)}
    return _moved_set_files(others, folders, set(docs)) | encoded, report


def _migrated_parts(
    docs: Mapping[str, bytes],
) -> tuple[dict[str, bytes], dict[str, str], MigrationReport]:
    """Migrates the show documents and canvas files of an older tree; maps renamed set folders."""
    show, report = read_migrated(docs)
    order = without_header(json.loads(docs[SHOW_DOC])).get(SET_ORDER_FIELD, [])
    stored_keys = [key for key in order if isinstance(key, str)] if isinstance(order, list) else []
    folders = {
        old: new
        for old, new in zip(stored_keys, (set_key(s) for s in show.sets), strict=True)
        if old != new
    }
    built = report.build()
    canvas = {path: dump_json(value) for path, value in renamed_canvas(docs, built).items()}
    return encode_show(show) | canvas, folders, built


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
                hint=f"remove or rename {target} with showpy 0.3, then load the show again",
                context={"path": path, "target": target},
            )
        moved[target] = raw
    return moved


def migration_changes(docs: Mapping[str, bytes]) -> dict[str, bytes | None]:
    """Lists the writes and deletes that turn a stored document set into its migrated form.

    Only show documents and the files of renamed set folders are read, so large renders and
    exports stay unread.

    Args:
        docs: Stored path to document bytes, as a repository tree holds them.

    Returns:
        changes: Stored path to new bytes, or None for a path the migration leaves behind
            (the old folder of a renamed set); empty when `docs` is already current.

    >>> from showpy.examples import two_set_show
    >>> migration_changes(encode_show(two_set_show()))
    {}
    """
    if not is_older_tree(docs):
        return {}
    encoded, folders, _ = _migrated_parts(docs)
    moving = [p for p in docs if not is_show_doc(p) and _set_folder(p) in folders]
    changes: dict[str, bytes | None] = {p: d for p, d in encoded.items() if docs.get(p) != d}
    changes.update(_moved_set_files({p: docs[p] for p in moving}, folders, set(docs)))
    changes.update(dict.fromkeys(p for p in docs if is_show_doc(p) and p not in encoded))
    changes.update(dict.fromkeys(moving))
    return changes


def is_older_tree(docs: Mapping[str, bytes]) -> bool:
    """Tells whether stored documents hold a show whose format version is older than current.

    >>> from showpy.examples import two_set_show
    >>> is_older_tree(encode_show(two_set_show())), is_older_tree({})
    (False, False)
    """
    return SHOW_DOC in docs and read_version(docs) < CURRENT_VERSION
