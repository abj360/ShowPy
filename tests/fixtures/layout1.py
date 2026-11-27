#!/usr/bin/env python3

"""
layout1.py --- encodes a show as layout 1 stored it, and writes layout-1 repository roots

Contains:
    write_layout1_root(): writes every show of a repository into the layout-1 files v0.4.0 wrote
    layout1_documents(): encodes a show with placements in set.json and the cast in show.json
    role_split_documents(): rewrites current-format layout-1 documents as the role documents
        encode_show writes for the same show
"""

import json
from collections.abc import Mapping
from pathlib import Path

from e8s.show.document_paths import CAST_DOC, CAST_FIELD, is_set_document, set_doc
from e8s.show.document_repository import DocumentRepository
from e8s.show.documents import SHOW_DOC, dump_json, dump_show_doc, encode_show
from e8s.show.item_table import ENTITY_FIELD
from e8s.show.models import Show
from e8s.show.role_documents import PLACEMENT_ORDER_FIELD, PLACEMENTS_FIELD, ROLE_FIELDS
from e8s.show.set_keys import set_key

SET_DOC_SUFFIX = "/set.json"
ROOT_LINK = "root"


def write_layout1_root(repo: DocumentRepository, root: Path) -> None:
    """Writes every show of a repository into the files the layout-1 writer of v0.4.0 made.

    Each show gets `<root>/<show>/objects/<sha256>`, `trees/<revision>.json` holding the revision
    and its tree, and `next/<parent>` (`next/root` for the first) holding the child's ID, so a
    layout-1 root is built from any history without the deleted writer.

    Args:
        repo: Repository whose shows and histories are written.
        root: Folder to write into; it holds no layout file afterwards.
    """
    for show_id in repo.shows():
        base = root / show_id
        for name in ("objects", "trees", "next"):
            (base / name).mkdir(parents=True, exist_ok=True)
        for revision in repo.revisions(show_id):
            tree = dict(repo.backend.tree(show_id, revision.id))
            for digest in tree.values():
                (base / "objects" / digest).write_bytes(repo.backend.blob(show_id, digest))
            record = dump_json({"revision": revision.to_json(), "tree": tree})
            (base / "trees" / f"{revision.id}.json").write_bytes(record)
            (base / "next" / (revision.parent or ROOT_LINK)).write_text(revision.id)


def layout1_documents(show: Show) -> dict[str, bytes]:
    """Encodes a show with the bytes the layout-1 encoder wrote for it.

    Args:
        show: The show to encode.

    Returns:
        docs: Stored path to bytes: each set.json holds the whole set with its placements, there
            are no role documents and no cast document, and show.json holds the cast.
    """
    docs = encode_show(show)
    layout1 = {p: raw for p, raw in docs.items() if not is_set_document(p) and p != CAST_DOC}
    for current in show.sets:
        layout1[set_doc(set_key(current))] = dump_json(current.to_json())
    meta = json.loads(docs[SHOW_DOC])
    layout1[SHOW_DOC] = dump_show_doc({**meta, CAST_FIELD: show.cast.to_json()})
    return layout1


def role_split_documents(layout1: Mapping[str, bytes]) -> dict[str, bytes]:
    """Rewrites current-format layout-1 documents, byte for byte, as encode_show writes them now.

    Args:
        layout1: Stored path to bytes of a format-2 tree with placements in set.json and the cast
            in show.json.

    Returns:
        docs: The same documents with each set.json split into set.json and role documents and
            the cast moved to the cast document; other files unchanged.
    """
    docs = dict(layout1)
    meta = json.loads(docs[SHOW_DOC])
    docs[CAST_DOC] = dump_json(meta.pop(CAST_FIELD))
    docs[SHOW_DOC] = dump_show_doc(meta)
    for path in [p for p in layout1 if is_set_document(p) and p.endswith(SET_DOC_SUFFIX)]:
        body = json.loads(docs[path])
        rows = body.pop(PLACEMENTS_FIELD)
        order = [row[ENTITY_FIELD] for row in rows]
        docs[path] = dump_json({**body, PLACEMENT_ORDER_FIELD: order})
        folder = path.removesuffix(SET_DOC_SUFFIX)
        for name, fields in ROLE_FIELDS.items():
            roles = {row[ENTITY_FIELD]: {f: row[f] for f in fields} for row in rows}
            docs[f"{folder}/{name}"] = dump_json(roles)
    return docs
