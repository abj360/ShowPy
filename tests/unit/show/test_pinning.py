#!/usr/bin/env python3

"""
test_pinning.py --- tests which forms a set pin locks

Contains:
    test_regression_set_pin_locks_the_forms_the_set_lists: locks a listed form and leaves a stale
        form of the same set unlocked
    test_unpinning_a_set_unlocks_its_listed_forms: clears the lock on listed forms when the set pin
        goes
    test_form_pin_still_locks_a_stale_form: locks a stale form pinned on its own
"""

from showpy.show.pinning import pin, unpin
from tests.helpers import replaced_form_show


def test_regression_set_pin_locks_the_forms_the_set_lists() -> None:
    """Locks a listed form and leaves a stale form of the same set unlocked (ruling L-G3)."""
    pinned = pin(replaced_form_show(), "set", "2")
    assert pinned.forms["b"].locked
    assert not pinned.forms["a"].locked


def test_unpinning_a_set_unlocks_its_listed_forms() -> None:
    """Clears the lock on listed forms when the set pin goes."""
    show = unpin(pin(replaced_form_show(), "set", "2"), "set", "2")
    assert not show.forms["b"].locked


def test_form_pin_still_locks_a_stale_form() -> None:
    """Locks a stale form pinned on its own."""
    pinned = pin(replaced_form_show(), "form", "a")
    assert pinned.forms["a"].locked
