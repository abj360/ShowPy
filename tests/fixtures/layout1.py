#!/usr/bin/env python3

"""
layout1.py --- encodes a show as layout 1 stored it, before sets were split into role documents

Contains:
    layout1_documents(): encodes a show with placements in set.json and the cast in show.json
    role_split_documents(): rewrites current-format layout-1 documents as the role documents
        encode_show writes for the same show
"""

import json
from collections.abc import Mapping

from e8s.show.document_paths import CAST_DOC, CAST_FIELD, is_set_document, set_doc
from e8s.show.documents import SHOW_DOC, dump_json, dump_show_doc, encode_show
from e8s.show.models import Show
from e8s.show.role_documents import PLACEMENT_ORDER_FIELD, PLACEMENTS_FIELD, ROLE_FIELDS
from e8s.show.set_keys import set_key

ENTITY_FIELD = "entity"
SET_DOC_SUFFIX = "/set.json"


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
