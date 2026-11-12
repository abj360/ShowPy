#!/usr/bin/env python3

"""
test_match_models.py --- tests MatchOptions validation, MatchResult alignment and FormFnInfo
"""

import pytest

from stage.choreo.models import FormFnInfo, MatchOptions, MatchResult
from stage.core.errors import SchemaError


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
    result = MatchResult(entities=("A",), assignment={"A": 0}, targets=((1.0, 2.0),))
    assert MatchResult.model_validate_json(result.model_dump_json()) == result
    assert result.target_points().tolist() == [[1.0, 2.0]]
    with pytest.raises(SchemaError):
        MatchResult(entities=("A",), assignment={"B": 0}, targets=((1.0, 2.0),))


def test_form_fn_info_holds_name_and_summary() -> None:
    assert FormFnInfo(name="spiral", summary="Spaces spots").name == "spiral"
