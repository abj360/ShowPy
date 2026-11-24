#!/usr/bin/env python3

"""
test_pinning.py --- tests which forms a set pin locks

Contains:
    test_regression_set_pin_locks_the_forms_the_set_lists: locks a listed form and leaves a stale
        form of the same set unlocked
    test_unpinning_a_set_unlocks_its_listed_forms: clears the lock on listed forms when the set pin
        goes
    test_form_pin_still_locks_a_stale_form: locks a stale form pinned on its own
    test_regression_unpinning_a_set_unlocks_a_form_that_went_stale_under_the_pin: clears the lock
        on a form that left the set's list while the set was pinned
    test_regression_repinning_a_set_locks_a_form_relisted_under_the_pin: locks a form the set
        lists again once the set pin is refreshed
    test_form_pin_on_a_stale_form_survives_a_set_unpin: keeps a stale form locked by its own pin
    test_set_pin_locks_every_form_when_the_set_lists_none: locks every stamped form of an unlisted
        set
    test_regression_legacy_transition_pin_with_reserved_set_id_still_resolves: resolves, splits
        and lock-checks a stored transition pin into a set `con` that migration renames `con_`
"""

from e8s.core.ids import LEGACY_IDS_KEY, FormId
from e8s.examples import two_set_show
from e8s.show.locks import require_unlocked
from e8s.show.manage import add_set
from e8s.show.migrate import FIRST_VERSION, ReportBuilder
from e8s.show.migrations.v1_to_v2_ids import rename_ids
from e8s.show.models import Show
from e8s.show.pinning import pin, unpin
from e8s.show.sections import split_section
from tests.helpers import json_doc, replaced_form_show

LEGACY_SET = "con"
LEGACY_TRANSITION = "con→3"
MIGRATED_SET = "con_"
MIGRATED_TRANSITION = "con_→3"


def test_regression_set_pin_locks_the_forms_the_set_lists() -> None:
    """Locks a listed form and leaves a stale form of the same set unlocked (ruling L-G3)."""
    pinned = pin(replaced_form_show(), "set", "2")
    assert pinned.forms[FormId("b")].locked
    assert not pinned.forms[FormId("a")].locked


def test_unpinning_a_set_unlocks_its_listed_forms() -> None:
    """Clears the lock on listed forms when the set pin goes."""
    show = unpin(pin(replaced_form_show(), "set", "2"), "set", "2")
    assert not show.forms[FormId("b")].locked


def test_form_pin_still_locks_a_stale_form() -> None:
    """Locks a stale form pinned on its own."""
    pinned = pin(replaced_form_show(), "form", "a")
    assert pinned.forms[FormId("a")].locked


def test_regression_unpinning_a_set_unlocks_a_form_that_went_stale_under_the_pin() -> None:
    """Clears the lock on a form that left the set's list while the set was pinned."""
    base = replaced_form_show()
    listed_a = base.replace(sets=(base.sets[0], base.sets[1].replace(forms=("a",))))
    pinned = pin(listed_a, "set", "2")
    assert pinned.forms[FormId("a")].locked
    restaled = pinned.replace(sets=(pinned.sets[0], pinned.sets[1].replace(forms=("b",))))
    assert not unpin(restaled, "set", "2").forms[FormId("a")].locked


def test_regression_repinning_a_set_locks_a_form_relisted_under_the_pin() -> None:
    """Locks a form the set lists again once the set pin is refreshed."""
    pinned = pin(replaced_form_show(), "set", "2")
    relisted = pinned.replace(sets=(pinned.sets[0], pinned.sets[1].replace(forms=("a", "b"))))
    assert pin(relisted, "set", "2").forms[FormId("a")].locked


def test_form_pin_on_a_stale_form_survives_a_set_unpin() -> None:
    """Keeps a stale form locked by its own pin when the set pin is removed."""
    show = pin(pin(replaced_form_show(), "form", "a"), "set", "2")
    assert unpin(show, "set", "2").forms[FormId("a")].locked


def test_set_pin_locks_every_form_when_the_set_lists_none() -> None:
    """Locks every stamped form when the set's own list is empty."""
    base = replaced_form_show()
    unlisted = base.replace(sets=(base.sets[0], base.sets[1].replace(forms=())))
    pinned = pin(unlisted, "set", "2")
    assert pinned.forms[FormId("a")].locked
    assert pinned.forms[FormId("b")].locked


def test_regression_legacy_transition_pin_with_reserved_set_id_still_resolves() -> None:
    """Resolves a stored transition pin into a set `con` once migration renames it (review of
    L0.5); the set ID predates the ID rule, so the v1 show is built with legacy IDs accepted."""
    dump = json_doc(pin(add_set(two_set_show(), "3", 4.0), "transition", "2→3"))
    dump["sets"][1]["id"] = LEGACY_SET
    dump["pins"][0]["id"] = LEGACY_TRANSITION
    legacy = Show._validated({**dump, "transitions": []}, context={LEGACY_IDS_KEY: True})
    pinned = rename_ids(legacy, ReportBuilder(FIRST_VERSION))
    assert [p.id for p in pinned.pins] == [MIGRATED_TRANSITION]
    require_unlocked(pinned, pinned)
    first, second = split_section(pinned, MIGRATED_SET)
    assert [p.id for p in second.pins] == [MIGRATED_TRANSITION]
    assert first.pins == ()
