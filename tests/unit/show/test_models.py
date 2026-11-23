#!/usr/bin/env python3

"""
test_models.py --- tests the Show aggregate: set and transition lookup, JSON round-trip
"""

import pytest

from e8s.choreo.forms import line_form
from e8s.choreo.path_models import PathRow, TransitionSpec
from e8s.core.caps import MAX_SETS
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, UnknownRefError
from e8s.core.ids import LEGACY_IDS_KEY, SetId, SpotId, make_tid
from e8s.show.models import Show
from tests.fixtures.shows import two_set_show


def test_set_lookup_and_unknown_suggests() -> None:
    show = two_set_show()
    assert show.set("2").counts == 8.0
    with pytest.raises(UnknownRefError) as info:
        show.set("3")
    assert "1" in info.value.hint or "2" in info.value.hint


def test_transition_ids_are_consecutive_pairs() -> None:
    assert two_set_show().transition_ids() == [("1", "2")]


def test_transition_id_resolves_a_pair_to_the_show_transition() -> None:
    show = two_set_show()
    tid = show.transition_id(("1", "2"))
    assert tid == show.transition_ids()[0]
    assert (tid.from_set, tid.to_set) == ("1", "2")
    with pytest.raises(UnknownRefError):
        show.transition_id(("2", "1"))


def test_missing_transition_defaults_to_destination_counts() -> None:
    spec = two_set_show().transition(("1", "2"))
    assert spec.counts == 8.0
    assert spec.rows == {}


def test_stored_transition_is_returned() -> None:
    stored = TransitionSpec(
        id=make_tid("1", "2"),
        counts=8.0,
        rows={SpotId("T1"): PathRow(entity=SpotId("T1"), start=2)},
    )
    show = two_set_show().model_copy(update={"transitions": (stored,)})
    assert show.transition(("1", "2")) == stored


def test_non_consecutive_transition_raises() -> None:
    with pytest.raises(UnknownRefError):
        two_set_show().transition(("2", "1"))


def test_duplicate_set_ids_rejected() -> None:
    show = two_set_show()
    with pytest.raises(SchemaError):
        Show.model_validate({**show.model_dump(), "sets": [show.sets[0].model_dump()] * 2})


def test_set_index() -> None:
    assert two_set_show().set_index("2") == 1


def test_show_round_trips_json() -> None:
    show = two_set_show()
    assert Show.model_validate_json(show.model_dump_json()) == show


def test_regression_show_index_is_fresh_after_replace() -> None:
    """Finds a set added by `replace` or `model_copy`, never a stale cached position."""
    base = two_set_show()
    assert base.set_index("2") == 1
    third = base.sets[1].model_copy(update={"id": "3", "key": "3"})
    for edited in (
        base.replace(sets=(*base.sets, third)),
        base.model_copy(update={"sets": (*base.sets, third)}),
    ):
        assert edited.set("3").id == "3"
        assert edited.set_index("3") == 2
        assert edited.transition_ids() == [("1", "2"), ("2", "3")]
        assert edited.transition(("2", "3")).counts == third.counts
    with pytest.raises(UnknownRefError):
        base.set("3")


def test_show_containers_hash() -> None:
    show = two_set_show()
    assert isinstance(hash(show.forms), int)
    assert isinstance(hash(show.dependencies), int)
    with pytest.raises(TypeError):
        show.dependencies["x"] = "1"  # type: ignore[index]


def test_regression_show_form_keys_pass_the_id_check() -> None:
    form = line_form("f-1", ["T1", "T2"], (0.0, 0.0), (4.0, 0.0), set_id="2")
    show = two_set_show()
    with pytest.raises(SchemaError):
        show.replace(forms={"f-1": form})
    dumped = show.model_dump(mode="json") | {"forms": {"f-1": form.model_dump(mode="json")}}
    assert list(Show.model_validate(dumped, context={LEGACY_IDS_KEY: True}).forms) == ["f-1"]


def _show_of(count: int) -> Show:
    show = two_set_show()
    first, second = show.sets
    more = (second.replace(id=SetId(str(n)), key=None) for n in range(3, count + 1))
    return show.replace(sets=(first, second, *more))


def test_show_accepts_max_sets() -> None:
    """Accepts a show of exactly MAX_SETS sets."""
    assert len(_show_of(MAX_SETS).sets) == MAX_SETS


def test_regression_show_refuses_more_than_max_sets() -> None:
    """Refuses a show of MAX_SETS + 1 sets with TOO_MANY_SETS, built, edited or decoded."""
    with pytest.raises(SchemaError) as caught:
        _show_of(MAX_SETS + 1)
    assert caught.value.code == ErrorCode.TOO_MANY_SETS
    assert caught.value.context == {"sets": MAX_SETS + 1, "max_sets": MAX_SETS}
    assert f"{MAX_SETS:,}" in caught.value.hint
    full = _show_of(MAX_SETS)
    extra = full.sets[-1].replace(id=SetId(str(MAX_SETS + 1)), key=None)
    with pytest.raises(SchemaError) as edited:
        full.replace(sets=(*full.sets, extra))
    assert edited.value.code == ErrorCode.TOO_MANY_SETS
    dumped = full.model_dump(mode="json")
    dumped["sets"].append(extra.model_dump(mode="json"))
    with pytest.raises(SchemaError) as decoded:
        Show.model_validate(dumped)
    assert decoded.value.code == ErrorCode.TOO_MANY_SETS
