#!/usr/bin/env python3

"""
test_selection.py --- tests the Selection set algebra
"""

from stage.core.selection import Selection


def test_selection_dedupes_and_keeps_order() -> None:
    assert Selection(ids=("T2", "T1", "T2")).ids == ("T2", "T1")


def test_union_intersection_difference() -> None:
    a = Selection(ids=("T1", "T2", "T3"))
    b = Selection(ids=("T3", "T4"))
    assert (a | b).ids == ("T1", "T2", "T3", "T4")
    assert (a & b).ids == ("T3",)
    assert (a - b).ids == ("T1", "T2")


def test_len_iter_contains() -> None:
    sel = Selection(ids=("T1", "T2"))
    assert len(sel) == 2
    assert list(sel) == ["T1", "T2"]
    assert "T1" in sel
    assert "T9" not in sel
