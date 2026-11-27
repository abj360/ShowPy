"""
encode_cache.py --- encodes a show document by document, reusing the bytes of unchanged sources

Each stored document is encoded from a few source objects only: a set, the forms of one set, a
transition's paths, the cast, the sheet, the timeline, or show.json's own fields and the set
order. Models are frozen, so while a source object lives its identity names its content; a
cache entry holds its sources, so no identity it is keyed by can be reused while it is cached.
A one-spot edit builds one new set and shares every other object, so only that set's documents
are encoded again.

A show decoded from storage holds new objects, so seeding stores its stored documents under
their identities, but only where encoding them gives back exactly the stored bytes. That is
checked once per content: decoding is a function of the documents' bytes and the inputs a group
names, so a later decode of the same bytes with the same inputs encodes to them too.

Contains:
    ENCODE_CACHE_BYTES: default bound on the encoded bytes a cache keeps
    DocumentGroup: names the documents a group of sources encodes to, and how to encode them
    document_groups(): lists the document groups of a show, after checking it can be stored
    encode_groups(): encodes every document of some groups, without a cache
    EncodeCache: bounded, thread-safe LRU of encoded documents keyed by source identity
    EncodeCache.documents(): encodes every stored document of a show, reusing cached groups
    EncodeCache.seed(): stores the documents of a decoded show, where they provably encode back
"""

import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from functools import partial
from typing import NamedTuple

from e8s.choreo.form_models import Form
from e8s.choreo.models import Set
from e8s.choreo.path_models import TransitionSpec
from e8s.core.ids import SetId
from e8s.core.types import FrozenModel
from e8s.show.document_dump import dump_dumped_json, dump_show_doc
from e8s.show.document_paths import (
    CAST_DOC,
    CAST_FIELD,
    ROLE_NAMES,
    SET_DOC_NAME,
    SET_ORDER_FIELD,
    SHEET_DOC,
    SPLIT_FIELDS,
    TIMELINE_DOC,
    forms_doc,
    paths_doc,
    role_docs,
    set_doc,
)
from e8s.show.migrate import SHOW_DOC, format_header
from e8s.show.models import Show
from e8s.show.role_documents import set_documents
from e8s.show.set_keys import set_key
from e8s.show.storable import require_storable

# A repository-wide default shared by every show it stores: the 300-spot, 64-set benchmark show
# encodes to 14.7 MiB, so four such shows stay cached.
ENCODE_CACHE_BYTES = 64 * 2**20
SET_PARTS = (SET_DOC_NAME, *ROLE_NAMES)
SHOW_FIELDS = tuple(n for n in Show._field_names() if n not in SPLIT_FIELDS and n != CAST_FIELD)


class DocumentGroup(NamedTuple):
    """Names the documents a group of sources encodes to, and how to encode them.

    Attributes:
        key: The paths, the identities of the sources and any inputs compared by value.
        sources: Objects the documents are encoded from; a cache entry holds them.
        paths: Stored paths of the documents, in the order `encode` returns them.
        encode: Encodes the documents, one bytes value per path.
        inputs: Values decoding takes from outside the documents, such as the counts a
            transition is given from its destination set; part of the content a seed matches.
    """

    key: Hashable
    sources: tuple[object, ...]
    paths: tuple[str, ...]
    encode: Callable[[], tuple[bytes, ...]]
    inputs: tuple[str, ...] = ()


def _group(
    paths: tuple[str, ...], sources: Sequence[object], encode: Callable[[], tuple[bytes, ...]]
) -> DocumentGroup:
    return DocumentGroup((paths, tuple(map(id, sources))), tuple(sources), paths, encode)


def _dump_models(models: Sequence[FrozenModel]) -> tuple[bytes, ...]:
    return (dump_dumped_json([m.to_json() for m in models]),)


def _dump_model(model: FrozenModel) -> tuple[bytes, ...]:
    return (dump_dumped_json(model.to_json()),)


def _model_group(path: str, model: FrozenModel) -> DocumentGroup:
    return _group((path,), (model,), partial(_dump_model, model))


def _dump_set(current: Set) -> tuple[bytes, ...]:
    split = set_documents(current)
    return tuple(dump_dumped_json(split[name]) for name in SET_PARTS)


def _dump_show_doc(show: Show, order: tuple[str, ...]) -> tuple[bytes, ...]:
    fields = show._json_dumped(include=set(SHOW_FIELDS))
    return (dump_show_doc({**format_header(), **fields, SET_ORDER_FIELD: list(order)}),)


def _show_group(show: Show, order: tuple[str, ...]) -> DocumentGroup:
    """Keys show.json on the identity of each of its fields, item by item for a mapping.

    Copying a show rebuilds its mapping fields around the same items, so a mapping is keyed
    by its keys and the identity of each value.
    """
    sources: list[object] = []
    shape: list[Hashable] = []
    for name in SHOW_FIELDS:
        value = getattr(show, name)
        if isinstance(value, Mapping):
            items = tuple(value.items())
            sources.extend(item for _, item in items)
            shape.append(tuple((key, id(item)) for key, item in items))
        else:
            sources.append(value)
            shape.append(id(value))
    encode = partial(_dump_show_doc, show, order)
    return DocumentGroup((SHOW_DOC, order, tuple(shape)), tuple(sources), (SHOW_DOC,), encode)


def _forms_by_set(show: Show) -> dict[str, list[Form]]:
    """Groups the forms of a show by set, each set's forms in form ID order."""
    by_set: dict[str, list[Form]] = {}
    for _, form in sorted(show.forms.items()):
        by_set.setdefault(form.set, []).append(form)
    return by_set


def document_groups(show: Show) -> list[DocumentGroup]:
    """Lists the document groups of a show under set keys; empty forms and paths are left out.

    show.json is first, then the sheet, timeline and cast, each set's set and role documents
    followed by its forms, and the paths of each stored transition. A show that cannot be
    stored raises, so every encode checks the whole show.

    >>> from e8s.examples import two_set_show
    >>> [group.paths[0] for group in document_groups(two_set_show())][:3]
    ['/show/show.json', '/show/sheet.json', '/time/timeline.json']
    """
    require_storable(show)
    keys = {s.id: set_key(s) for s in show.sets}
    groups = [
        _show_group(show, tuple(keys.values())),
        _model_group(SHEET_DOC, show.sheet),
        _model_group(TIMELINE_DOC, show.timeline),
        _model_group(CAST_DOC, show.cast),
    ]
    forms = _forms_by_set(show)
    for current in show.sets:
        folder = keys[current.id]
        groups.append(
            _group((set_doc(folder), *role_docs(folder)), (current,), partial(_dump_set, current))
        )
        if current.id in forms:
            found = forms[current.id]
            groups.append(_group((forms_doc(folder),), found, partial(_dump_models, found)))
    groups.extend(_transition_group(spec, keys) for spec in show.transitions)
    return groups


def _transition_group(spec: TransitionSpec, keys: Mapping[SetId, str]) -> DocumentGroup:
    """Groups a transition's paths; decoding gives them the destination set's counts."""
    group = _model_group(paths_doc((keys[spec.id[0]], keys[spec.id[1]])), spec)
    return group._replace(inputs=(repr(spec.counts),))


def encode_groups(groups: Iterable[DocumentGroup]) -> dict[str, bytes]:
    """Encodes every document of some groups, without a cache.

    >>> from e8s.examples import two_set_show
    >>> sorted(encode_groups(document_groups(two_set_show())))[0]
    '/cast/cast.json'
    """
    return {
        path: raw for group in groups for path, raw in zip(group.paths, group.encode(), strict=True)
    }


class _Entry(NamedTuple):
    """Holds a group's encoded documents and the sources whose identities key them.

    `content` names the stored documents and inputs a seed checked them against, if any.
    """

    sources: tuple[object, ...]
    documents: tuple[bytes, ...]
    size: int
    content: Hashable = None


def _content(group: DocumentGroup, digests: Mapping[str, str]) -> Hashable:
    """Names a group's stored documents by digest, with its inputs; None when one is missing."""
    stored = tuple(digests.get(path) for path in group.paths)
    return None if None in stored else (group.paths, stored, group.inputs)


class EncodeCache:
    """Remembers encoded documents by the identity of their sources, bounded by encoded bytes.

    Attributes:
        max_bytes: Most encoded bytes kept; the least recently used group leaves first, and a
            group larger than this is never kept.
        misses: Documents encoded so far because their group was not cached.
        checks: Documents encoded so far to check a seed against the stored bytes.
    """

    def __init__(self, max_bytes: int = ENCODE_CACHE_BYTES) -> None:
        self.max_bytes = max_bytes
        self.misses = 0
        self.checks = 0
        self._entries: OrderedDict[Hashable, _Entry] = OrderedDict()
        self._checked: dict[Hashable, Hashable] = {}
        self._bytes = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def nbytes(self) -> int:
        """Returns the encoded bytes the cache holds."""
        with self._lock:
            return self._bytes

    def documents(self, show: Show) -> dict[str, bytes]:
        """Encodes every stored document of a show, reusing groups whose sources are cached.

        Two threads may encode the same miss; the bytes are equal, so the later store wins.

        Args:
            show: Show to encode; one that cannot be stored raises, cached or not.

        Returns:
            documents: Stored path to canonical bytes, equal to what encode_show() returns.

        >>> from e8s.examples import two_set_show
        >>> cache, show = EncodeCache(), two_set_show()
        >>> cache.documents(show) == cache.documents(show), cache.misses
        (True, 12)
        """
        documents: dict[str, bytes] = {}
        for group in document_groups(show):
            documents.update(zip(group.paths, self._encoded(group), strict=True))
        return documents

    def seed(self, show: Show, stored: Mapping[str, bytes], digests: Mapping[str, str]) -> None:
        """Stores a decoded show's stored documents under its sources, where they encode back.

        A group is stored only when its encoding equals the stored bytes: checked by encoding
        the first time its content is seen, and by content after that. A group with a document
        missing, or one that encodes otherwise, is left to encode on the next save.

        Args:
            show: The show decode_show() returned for the current-format documents `stored`.
            stored: Stored path to the bytes the show was decoded from.
            digests: Stored path to the blob digest of each of those documents.

        >>> from e8s.examples import two_set_show
        >>> from e8s.show.documents import decode_show, encode_show
        >>> from e8s.show.integrity import blob_digest
        >>> docs = encode_show(two_set_show())
        >>> cache, show = EncodeCache(), decode_show(docs)
        >>> cache.seed(show, docs, {p: blob_digest(raw) for p, raw in docs.items()})
        >>> cache.documents(show) == docs, cache.misses
        (True, 0)
        """
        for group in document_groups(show):
            content = _content(group, digests)
            if content is None or self._reused(group, content):
                continue
            documents = group.encode()
            matches = documents == tuple(stored[path] for path in group.paths)
            with self._lock:
                self.checks += len(documents)
                if matches:
                    self._store(group, documents, content)

    def _reused(self, group: DocumentGroup, content: Hashable) -> bool:
        """Tells whether a group is cached, storing it from a checked entry of the same content."""
        with self._lock:
            if group.key in self._entries:
                self._entries.move_to_end(group.key)
                return True
            checked = self._entries.get(self._checked.get(content))
            if checked is None:
                return False
            self._store(group, checked.documents, content)
            return True

    def _encoded(self, group: DocumentGroup) -> tuple[bytes, ...]:
        """Returns a group's cached documents, encoding and storing them on a miss."""
        with self._lock:
            entry = self._entries.get(group.key)
            if entry is not None:
                self._entries.move_to_end(group.key)
                return entry.documents
        documents = group.encode()
        with self._lock:
            self.misses += len(documents)
            self._store(group, documents)
        return documents

    def _store(
        self, group: DocumentGroup, documents: tuple[bytes, ...], content: Hashable = None
    ) -> None:
        """Stores a group's documents, then evicts least recently used groups over the cap."""
        size = sum(len(raw) for raw in documents)
        if size > self.max_bytes:
            return
        replaced = self._entries.pop(group.key, None)
        if replaced is not None:
            self._forget(group.key, replaced)
        self._entries[group.key] = _Entry(group.sources, documents, size, content)
        self._bytes += size
        if content is not None:
            self._checked[content] = group.key
        while self._bytes > self.max_bytes:
            key, evicted = self._entries.popitem(last=False)
            self._forget(key, evicted)

    def _forget(self, key: Hashable, entry: _Entry) -> None:
        """Takes a removed entry's bytes off the total and drops the check that points at it."""
        self._bytes -= entry.size
        if entry.content is not None and self._checked.get(entry.content) == key:
            del self._checked[entry.content]
