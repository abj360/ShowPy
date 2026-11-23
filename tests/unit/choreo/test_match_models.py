#!/usr/bin/env python3

"""
test_match_models.py --- tests MatchOptions validation, MatchResult alignment and FormFnInfo

Contains:
    test_regression_match_options_rejects_nan_weights: rejects a NaN penalty weight
    test_regression_match_options_rejects_negative_no_backward: rejects a negative no_backward
        penalty
    test_regression_match_options_rejects_bool_slot_index: rejects True as a pinned slot index
        instead of pinning to slot 1
    test_regression_match_result_rejects_a_negative_slot_index: rejects a slot index below zero
        in an assignment, a form slot or the unassigned slots
    test_match_weights_are_typed_and_non_negative: holds the four penalties as finite numbers
        at or above zero
    test_regression_match_options_rejects_bool_in_forbid_and_group_slots: rejects True as a slot
        index in forbid and group_slots
    test_match_options_accept_weights_as_a_mapping: coerces a dict of weights into MatchWeights
"""

import math

import pytest

from e8s.choreo.form_models import FormFnInfo
from e8s.choreo.match_models import MatchOptions, MatchResult, MatchWeights
from e8s.core.errors import SchemaError
from e8s.core.ids import FormId, SpotId


def test_match_options_defaults_follow_the_reference() -> None:
    options = MatchOptions()
    assert options.objective == "bottleneck_then_sum"
    assert options.keep_groups is True
    assert options.no_backward is False


def test_no_backward_keeps_bool_and_float_apart() -> None:
    assert MatchOptions(no_backward=True).no_backward is True
    assert MatchOptions(no_backward=1.5).no_backward == 1.5


@pytest.mark.parametrize(
    "bad",
    [{"power": 0.0}, {"max_speed": -1.0}, {"counts": 0.0}, {"weights": {"turn": -1.0}}],
)
def test_match_options_reject_non_positive_values(bad: dict[str, object]) -> None:
    with pytest.raises(SchemaError):
        MatchOptions.model_validate(bad)


def test_cost_fn_is_excluded_from_serialization() -> None:
    options = MatchOptions(cost_fn=lambda *_: 0.0)
    assert "cost_fn" not in options.model_dump()
    assert MatchOptions.model_validate_json(options.model_dump_json()).cost_fn is None


def test_match_result_round_trips_and_checks_alignment() -> None:
    result = MatchResult(
        entities=(SpotId("A"),), assignment={SpotId("A"): 0}, targets=((1.0, 2.0),)
    )
    assert MatchResult.model_validate_json(result.model_dump_json()) == result
    assert result.target_points().tolist() == [[1.0, 2.0]]
    with pytest.raises(SchemaError):
        MatchResult(entities=(SpotId("A"),), assignment={SpotId("B"): 0}, targets=((1.0, 2.0),))


def test_match_result_slots_must_cover_the_assigned_spots() -> None:
    kept = MatchResult(
        entities=(SpotId("A"),),
        assignment={SpotId("A"): 0},
        targets=((1.0, 2.0),),
        slots={SpotId("A"): (FormId("f"), 0)},
    )
    assert MatchResult.model_validate_json(kept.model_dump_json()) == kept
    with pytest.raises(SchemaError):
        MatchResult(
            entities=(SpotId("A"),),
            assignment={SpotId("A"): 0},
            targets=((1.0, 2.0),),
            slots={SpotId("B"): (FormId("f"), 0)},
        )


def test_form_fn_info_holds_name_and_summary() -> None:
    assert FormFnInfo(name="spiral", summary="Spaces spots").name == "spiral"


NEGATIVE_PENALTY_M = -1.0


def test_regression_match_options_rejects_nan_weights() -> None:
    """Rejects a NaN penalty weight (audit 9.6)."""
    with pytest.raises(SchemaError):
        MatchOptions.model_validate({"weights": {"turn": math.nan}})


def test_regression_match_options_rejects_negative_no_backward() -> None:
    """Rejects a negative no_backward penalty (audit 9.6)."""
    with pytest.raises(SchemaError):
        MatchOptions(no_backward=NEGATIVE_PENALTY_M)


def test_regression_match_options_rejects_bool_slot_index() -> None:
    """Rejects True as a pinned slot index instead of pinning to slot 1 (audit 9.6)."""
    with pytest.raises(SchemaError):
        MatchOptions(pinned={SpotId("A"): True})


NEGATIVE_SLOT = -1


@pytest.mark.parametrize(
    "bad",
    [
        {"assignment": {SpotId("A"): NEGATIVE_SLOT}},
        {"slots": {SpotId("A"): (FormId("f"), NEGATIVE_SLOT)}},
        {"unassigned_slots": (NEGATIVE_SLOT,)},
    ],
)
def test_regression_match_result_rejects_a_negative_slot_index(bad: dict[str, object]) -> None:
    """Rejects a hand-built result whose slot index is below zero with a SchemaError."""
    fields: dict[str, object] = {
        "entities": (SpotId("A"),),
        "assignment": {SpotId("A"): 0},
        "targets": ((1.0, 2.0),),
    }
    with pytest.raises(SchemaError):
        MatchResult.model_validate({**fields, **bad})


@pytest.mark.parametrize("name", ["backward", "crossing", "group_spread", "turn"])
@pytest.mark.parametrize("bad", [-1.0, math.inf, math.nan])
def test_match_weights_are_typed_and_non_negative(name: str, bad: float) -> None:
    assert MatchWeights().model_dump() == {
        "backward": 0.0,
        "crossing": 0.0,
        "group_spread": 0.0,
        "turn": 0.0,
    }
    with pytest.raises(SchemaError):
        MatchWeights.model_validate({name: bad})


def test_match_options_accept_weights_as_a_mapping() -> None:
    options = MatchOptions.model_validate({"weights": {"turn": 0.5}})
    assert options.weights == MatchWeights(turn=0.5)
    assert MatchOptions(weights=MatchWeights(crossing=2.0)).weights.crossing == 2.0


def test_regression_match_options_rejects_bool_in_forbid_and_group_slots() -> None:
    """Rejects True as a slot index in `forbid` and `group_slots`, as it does in `pinned`."""
    with pytest.raises(SchemaError):
        MatchOptions(forbid=((SpotId("A"), True),))
    with pytest.raises(SchemaError):
        MatchOptions(group_slots={"brass": (True,)})
