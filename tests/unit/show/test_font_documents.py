#!/usr/bin/env python3

"""
test_font_documents.py --- tests that a show stores the fonts of its text forms in its own tree

Contains:
    test_reopened_user_font_text_form_rebuilds_without_a_registry: a fresh workspace re-lays text
    test_font_lands_in_the_same_revision_as_its_form: the font and the form share one revision
    test_save_without_the_font_raises_missing_font: a user font missing everywhere fails closed
    test_tampered_font_document_is_refused: a renamed or changed font file raises the hash code
    test_bundled_fonts_are_not_stored: a text form on a bundled font writes no font document
    test_reopened_show_saves_again_without_a_registry: stored fonts resolve on the next save
    test_unused_font_documents_are_deleted: a whole-show save drops fonts no form uses
    test_stored_font_bytes_are_the_source_bytes: the file holds the exact bytes
    test_bad_font_document_names_are_refused_on_load: a non-hash name raises SchemaError
    test_regression_one_spot_save_keeps_the_font_and_encodes_only_the_edit: B1 and M2 together
    test_load_fonts_without_documents_is_bundled: an empty tree gives the bundled registry
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from e8s.choreo.forms import slots, text_form
from e8s.choreo.models import PlacementPatch
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import FontError, MissingFontError, SchemaError
from e8s.core.ids import FormId
from e8s.core.util import asset_uri
from e8s.examples import two_set_show
from e8s.geo.fonts import bundled_fonts, register_font
from e8s.show.document_paths import role_docs, set_doc
from e8s.show.document_repository import encode_cache_of
from e8s.show.font_documents import FONTS_ROOT, load_fonts
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.models import Show
from e8s.show.set_keys import set_key
from e8s.show.show_io import load_show, save_show
from e8s.show.workspace import open_workspace
from tests.fixtures.fonts import tiny_bytes
from tests.fixtures.generators import seeded_show

AT = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
SET_ID = "1"
SHOW_ID = "show_1"
TINY_FORM = FormId("tiny")
MEMBERS = ["T1", "T2", "T3", "T4"]


def _text_show() -> tuple[Show, str, bytes]:
    data = tiny_bytes()
    registry, uri = register_font(bundled_fonts(), data)
    form = text_form(
        "tiny", MEMBERS, "I", fonts=[uri, registry.resolve("simplex")], height=2.0,
        at=(0, 5), n=4, set_id=SET_ID, font_registry=registry,
    )  # fmt: skip
    return two_set_show().replace(id=SHOW_ID, forms={"tiny": form}), uri, data


def _font_path(data: bytes) -> str:
    return f"{FONTS_ROOT}{asset_uri(data).removeprefix('asset:sha256:')}.json"


def _saved() -> tuple[InMemoryRepository, Show, str, bytes]:
    show, uri, data = _text_show()
    repo = InMemoryRepository()
    ws = open_workspace(repo, show.id)
    registry, _ = register_font(bundled_fonts(), data)
    save_show(ws, show, AT, font_registry=registry)
    return repo, show, uri, data


def test_reopened_user_font_text_form_rebuilds_without_a_registry() -> None:
    repo, show, _, data = _saved()
    original, _ = register_font(bundled_fonts(), data)
    expected = slots(show.forms[TINY_FORM], font_registry=original)
    ws = open_workspace(repo, show.id)
    loaded = load_show(ws)
    rebuilt = slots(loaded.forms[TINY_FORM], font_registry=load_fonts(ws))
    assert np.array_equal(rebuilt, expected)


def test_font_lands_in_the_same_revision_as_its_form() -> None:
    repo, show, _, data = _saved()
    ws = open_workspace(repo, show.id)
    revisions = repo.revisions(show.id)
    assert len(revisions) == 1
    tree = ws.tree_at(revisions[0].id)
    assert tree[_font_path(data)] == data
    assert any(path.startswith("/show/") for path in tree)


def test_stored_font_bytes_are_the_source_bytes() -> None:
    repo, show, _, data = _saved()
    ws = open_workspace(repo, show.id)
    assert ws.tree_at(ws.revision)[_font_path(data)] == data
    assert load_fonts(ws).source(asset_uri(data)) == data


def test_save_without_the_font_raises_missing_font() -> None:
    show, uri, _ = _text_show()
    ws = open_workspace(InMemoryRepository(), show.id)
    with pytest.raises(MissingFontError) as caught:
        save_show(ws, show, AT)
    assert uri in caught.value.message
    assert "tiny" in caught.value.message
    assert "font_registry" in (caught.value.hint or "")


def test_tampered_font_document_is_refused() -> None:
    repo, show, _, data = _saved()
    ws = open_workspace(repo, show.id)
    wrong = tiny_bytes(family="Other")
    ws._commit_stored({_font_path(data): wrong}, at=AT + timedelta(seconds=1))
    with pytest.raises(FontError) as caught:
        load_fonts(ws)
    assert caught.value.code == ErrorCode.FONT_HASH_MISMATCH
    assert _font_path(data) in caught.value.message


def test_bundled_fonts_are_not_stored() -> None:
    members = [f"T{i}" for i in range(1, 13)]
    form = text_form("hi", members, "I", fonts=["simplex"], height=2.0, at=(0, 5), n=12,
                     set_id=SET_ID, font_registry=bundled_fonts())  # fmt: skip
    show = two_set_show().replace(id=SHOW_ID, forms={"hi": form})
    ws = open_workspace(InMemoryRepository(), show.id)
    save_show(ws, show, AT)
    assert not [p for p in ws.tree_at(ws.revision) if p.startswith(FONTS_ROOT)]


def test_reopened_show_saves_again_without_a_registry() -> None:
    repo, show, _, data = _saved()
    ws = open_workspace(repo, show.id)
    loaded = load_show(ws)
    save_show(ws, loaded.replace(title="renamed"), AT + timedelta(seconds=1))
    assert ws.tree_at(ws.revision)[_font_path(data)] == data


def test_unused_font_documents_are_deleted() -> None:
    repo, show, _, data = _saved()
    ws = open_workspace(repo, show.id)
    loaded = load_show(ws)
    save_show(ws, loaded.replace(forms={}), AT + timedelta(seconds=1))
    assert _font_path(data) not in ws.tree_at(ws.revision)
    first = repo.revisions(show.id)[0]
    assert _font_path(data) in ws.tree_at(first.id)


def test_bad_font_document_names_are_refused_on_load() -> None:
    repo, show, _, _ = _saved()
    ws = open_workspace(repo, show.id)
    for name in ("notes.json", f"{'a' * 64}.txt", f"{'A' * 64}.json"):
        ws._commit_stored({f"{FONTS_ROOT}{name}": b"{}"}, at=AT + timedelta(seconds=1))
        with pytest.raises(SchemaError) as caught:
            load_fonts(ws)
        assert name in caught.value.message
        ws._commit_stored({f"{FONTS_ROOT}{name}": None}, at=AT + timedelta(seconds=2))


def test_load_fonts_without_documents_is_bundled() -> None:
    ws = open_workspace(InMemoryRepository(), "show_1")
    save_show(ws, two_set_show(), AT)
    assert load_fonts(ws) is bundled_fonts()


def test_regression_one_spot_save_keeps_the_font_and_encodes_only_the_edit() -> None:
    data = tiny_bytes()
    registry, uri = register_font(bundled_fonts(), data)
    base = seeded_show(5, 12, 4)
    members = [str(entity) for entity in base.cast.ids()[:12]]
    form = text_form(
        "tiny", members, "I", fonts=[uri], height=2.0, at=(0, 5), n=12,
        set_id=base.sets[0].id, font_registry=registry,
    )  # fmt: skip
    show = base.replace(forms={TINY_FORM: form})
    repo = InMemoryRepository()
    ws = open_workspace(repo, show.id)
    saved = save_show(ws, show, AT, font_registry=registry)
    cache = encode_cache_of(repo)
    assert cache is not None
    before = cache.misses
    entity = show.cast.ids()[0]
    edited_set = show.sets[2].with_placements({entity: PlacementPatch(x=-1.0)}, force=True)
    sets = (*show.sets[:2], edited_set, *show.sets[3:])
    revision = save_show(ws, show.replace(sets=sets, revision=saved.id), AT + timedelta(seconds=1))
    folder = set_key(show.sets[2])
    assert cache.misses - before == len({set_doc(folder), *role_docs(folder)})
    assert _font_path(data) not in revision.changed
    assert ws.tree_at(revision.id)[_font_path(data)] == data
