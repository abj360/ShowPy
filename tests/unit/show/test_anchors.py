#!/usr/bin/env python3

"""
test_anchors.py --- tests the tagged Anchor

Contains:
    test_anchor_constructors_set_one_payload: builds each anchor kind with its one payload
    test_anchor_rejects_missing_or_extra_payload: refuses an anchor with no payload or two
    test_anchor_is_public: serves Anchor at the top level of e8s
"""

import pytest

import e8s
from e8s.core.errors import SchemaError
from e8s.core.ids import SetId
from e8s.show.anchors import Anchor


def test_anchor_constructors_set_one_payload() -> None:
    assert Anchor.on_set("12").set_id == "12"
    assert Anchor.on_transition(("12", "13")).tid == ("12", "13")
    assert Anchor.at_point((1.0, 2.0)).point == (1.0, 2.0)
    assert Anchor.on_spots(["T1", "T2"]).spots == ("T1", "T2")


def test_anchor_rejects_missing_or_extra_payload() -> None:
    with pytest.raises(SchemaError):
        Anchor(kind="set")
    with pytest.raises(SchemaError):
        Anchor(kind="set", set_id=SetId("1"), point=(0.0, 0.0))


def test_anchor_is_public() -> None:
    assert e8s.Anchor is Anchor
