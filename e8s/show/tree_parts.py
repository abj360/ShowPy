"""
tree_parts.py --- reads the show one stored tree holds part by part, decoding each document once

A whole-show decode assembles every part and validates the show. A tree's parts can instead be
read one at a time: each part is decoded from its own documents, checked as a whole-show decode
checks it, and reused through the decode cache for every tree that holds the same bytes.

Contains:
    TreeParts: reads one stored tree's show part by part
    TreeParts.has_show(): reports whether the tree holds a show
    TreeParts.set_keys(): lists the set keys show.json holds, in show order
    TreeParts.is_role_tree(): reports whether the tree is current and stores every set by role
    TreeParts.require_known_paths(): runs the checks a whole-show decode runs on the tree's paths
    TreeParts.set(): returns the set stored under a key
    TreeParts.stored_paths(): returns the paths stored for two consecutive sets, or None
    TreeParts.form_sources(): maps each form's item path to the form and its set's key
    TreeParts.form(): returns a form by ID, as Show.forms holds it
    TreeParts.sheet(), TreeParts.timeline(): return the sheet and the timeline
"""

from collections.abc import Mapping
from functools import cached_property

from e8s.choreo.form_models import Form
from e8s.choreo.models import Set
from e8s.choreo.path_models import TransitionSpec
from e8s.core.ids import TransitionId
from e8s.sheet.models import Sheet
from e8s.show.decode_cache import DecodeCache
from e8s.show.document_parts import (
    OlderTreeError,
    PartReading,
    read_checked_part,
    read_current_meta,
    read_forms,
    read_paths,
    read_set,
    set_order,
)
from e8s.show.document_paths import (
    CAST_DOC,
    SET_ORDER_FIELD,
    SHEET_DOC,
    TIMELINE_DOC,
    role_docs,
    set_doc,
)
from e8s.show.document_sources import raw_source
from e8s.show.integrity import LazyBlobs
from e8s.show.item_table import ItemPath
from e8s.show.migrate import CHECKED_CONTEXT, SHOW_DOC, without_header
from e8s.show.role_documents import require_known_cast_documents, require_listed_role_folders
from e8s.time.models import Timeline


class TreeParts:
    """Reads the show one stored tree holds part by part, in the current format's ID context.

    Each part is decoded once per object; through the cache, once per document digest. A part
    that fails its checks raises SchemaError, as a whole-show decode of the tree would.

    Attributes:
        tree: Stored path to document bytes.
    """

    def __init__(self, tree: Mapping[str, bytes], cache: DecodeCache | None = None) -> None:
        digests = tree.digests if isinstance(tree, LazyBlobs) else {}
        self.tree = tree
        self._reading = PartReading(raw_source(tree), CHECKED_CONTEXT, cache, digests)
        self._sets: dict[str, Set] = {}
        self._paths: dict[tuple[str, str], TransitionSpec | None] = {}

    def has_show(self) -> bool:
        """Reports whether the tree holds a show.json."""
        return SHOW_DOC in self.tree

    @cached_property
    def _keys(self) -> list[str]:
        meta = without_header(read_current_meta(self._reading))
        return set_order(meta.get(SET_ORDER_FIELD, []))

    def set_keys(self) -> list[str]:
        """Lists the set keys show.json holds, in show order; an older format raises OlderTreeError.

        >>> from e8s.examples import two_set_show
        >>> from e8s.show.documents import encode_show
        >>> TreeParts(encode_show(two_set_show())).set_keys()
        ['1', '2']
        """
        return self._keys

    def is_role_tree(self) -> bool:
        """Reports whether the tree is current, stores its cast and stores every listed set by role.

        That is the layout save_show() writes; an older format or a layout-1 tree is not.

        >>> from e8s.examples import two_set_show
        >>> from e8s.show.documents import encode_show
        >>> TreeParts(encode_show(two_set_show())).is_role_tree(), TreeParts({}).is_role_tree()
        (True, False)
        """
        if not self.has_show() or CAST_DOC not in self.tree:
            return False
        try:
            keys = self.set_keys()
        except OlderTreeError:
            return False
        return not any(set_doc(k) in self.tree and role_docs(k)[0] not in self.tree for k in keys)

    def require_known_paths(self) -> None:
        """Refuses role documents of an unlisted set folder and unknown documents under /cast/."""
        paths = list(self.tree)
        require_listed_role_folders(paths, self.set_keys())
        require_known_cast_documents(paths)

    def set(self, key: str) -> Set:
        """Returns the set stored under `key`, decoded from set.json and its role documents."""
        if key not in self._sets:
            self._sets[key] = read_set(self._reading, key)
        return self._sets[key]

    def stored_paths(self, folder: tuple[str, str]) -> TransitionSpec | None:
        """Returns the paths stored for the consecutive sets keyed `folder`, or None.

        The paths take the destination set's counts and must name the two sets' IDs.
        """
        if folder not in self._paths:
            first, second = self.set(folder[0]), self.set(folder[1])
            tid = TransitionId(first.id, second.id)
            self._paths[folder] = read_paths(self._reading, folder, tid, second.counts)
        return self._paths[folder]

    @cached_property
    def _form_sources(self) -> dict[ItemPath, tuple[Form, str]]:
        found: dict[ItemPath, tuple[Form, str]] = {}
        for key in self.set_keys():
            for form in read_forms(self._reading, key, self.set(key).id):
                found[(form.id,)] = (form, key)
        return found

    def form_sources(self) -> Mapping[ItemPath, tuple[Form, str]]:
        """Maps each form's item path to the form and the key of the set that stores it.

        Every set's forms document is read; a form ID two sets store is the later set's form,
        as in Show.forms.
        """
        return self._form_sources

    @cached_property
    def _forms(self) -> dict[str, Form]:
        return {path[0]: form for path, (form, _) in self._form_sources.items()}

    def form(self, form_id: str) -> Form:
        """Returns a form by ID, as Show.forms holds it."""
        return self._forms[form_id]

    def sheet(self) -> Sheet:
        """Returns the stored sheet."""
        return read_checked_part(self._reading, SHEET_DOC, Sheet)

    def timeline(self) -> Timeline:
        """Returns the stored timeline."""
        return read_checked_part(self._reading, TIMELINE_DOC, Timeline)
