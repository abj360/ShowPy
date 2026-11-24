#!/usr/bin/env python3

"""
test_model_json.py --- tests that every public model reads back what to_json writes

Contains:
    DEFAULT_ONLY_FIELDS: optional fields no example moves off its default, each with the reason
    public_models(): lists the public FrozenModel subclasses e8s exports, by name
    models_without_examples(): lists the public models that have no example instance
    round_trip_failures(): lists the models whose examples do not survive to_json and from_json
    fields_left_at_default(): lists the optional fields every example leaves at their default
    test_every_public_model_round_trips_json: requires from_json(to_json(m)) == m for each example
    test_round_trip_check_catches_a_field_to_json_drops: proves the round-trip check can fail
    test_every_public_model_has_an_example: fails when a public model has no example instance
    test_missing_example_scan_flags_a_model_without_one: proves the coverage scan can fail
    test_every_optional_field_leaves_its_default_in_some_example: fails when the examples never
        set an optional field, so a dropped field could pass the round trip unseen
    test_default_field_scan_flags_a_field_left_at_default: proves the field scan can fail
    test_not_json_models_are_exactly_those_that_refuse: keeps the listed exclusions in step with
        the models that refuse JSON, and checks each refuses with NOT_SERIALIZABLE
"""

import typing
from collections.abc import Mapping

import pytest
from pydantic import Field
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

import e8s
from e8s._api_table import PUBLIC_API
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError
from e8s.core.types import FrozenModel
from tests.fixtures.model_examples import ABSTRACT_MODELS, NOT_JSON_MODELS, model_examples

type Examples = Mapping[type[FrozenModel], tuple[FrozenModel, ...]]

DEFAULT_ONLY_FIELDS = {
    "MatchOptions.cost_fn": "a function has no JSON form; to_json refuses a set cost_fn",
}


def public_models() -> dict[str, type[FrozenModel]]:
    """Lists the public FrozenModel subclasses e8s exports, by public name.

    Returns:
        models: Public name mapped to the model class.
    """
    models: dict[str, type[FrozenModel]] = {}
    for name, _, _ in PUBLIC_API:
        value = getattr(e8s, name)
        if isinstance(value, type) and issubclass(value, FrozenModel):
            models[name] = value
    return models


def models_without_examples(models: dict[str, type[FrozenModel]], examples: Examples) -> list[str]:
    """Lists the models, bar the abstract bases, that have no example instance.

    Args:
        models: Public name mapped to the model class.
        examples: Model class mapped to its example instances.

    Returns:
        names: Sorted public names of the models with no example.
    """
    return sorted(
        name
        for name, model in models.items()
        if not examples.get(model) and name not in ABSTRACT_MODELS
    )


def round_trip_failures(models: dict[str, type[FrozenModel]], examples: Examples) -> list[str]:
    """Lists the models with an example that from_json(to_json(example)) does not reproduce.

    Args:
        models: Public name mapped to the model class.
        examples: Model class mapped to its example instances.

    Returns:
        names: Public names of the failing models, in the order of `models`.
    """
    return [
        name
        for name, model in models.items()
        if name not in NOT_JSON_MODELS
        and any(
            model.from_json(example.to_json()) != example for example in examples.get(model, ())
        )
    ]


def _default(field: FieldInfo) -> object:
    if field.default is PydanticUndefined:
        return field.get_default(call_default_factory=True, validated_data={})
    return field.default


def _only_one_value(field: FieldInfo) -> bool:
    """Tells whether the field's type allows only its default, such as a one-value Literal tag."""
    annotation = field.annotation
    return typing.get_origin(annotation) is typing.Literal and len(typing.get_args(annotation)) == 1


def fields_left_at_default(
    models: dict[str, type[FrozenModel]], examples: Examples, exempt: Mapping[str, str]
) -> list[str]:
    """Lists `Model.field` for each optional field that every example leaves at its default.

    Args:
        models: Public name mapped to the model class.
        examples: Model class mapped to its example instances.
        exempt: `Model.field` names that may stay at default, mapped to the reason.

    Returns:
        fields: Sorted `Model.field` names that no example sets.
    """
    left: list[str] = []
    for name, model in models.items():
        if name in ABSTRACT_MODELS:
            continue
        for field_name, field in model.__pydantic_fields__.items():
            qualified = f"{name}.{field_name}"
            if field.is_required() or _only_one_value(field) or qualified in exempt:
                continue
            default = _default(field)
            if all(vars(m).get(field_name, default) == default for m in examples.get(model, ())):
                left.append(qualified)
    return sorted(left)


def test_every_public_model_round_trips_json() -> None:
    """Requires from_json(to_json(example)) == example for every example of every model."""
    failed = round_trip_failures(public_models(), model_examples())
    assert not failed, f"from_json(to_json(m)) != m for: {', '.join(failed)}"


def test_round_trip_check_catches_a_field_to_json_drops() -> None:
    """Reports a model whose excluded field holds a value that from_json cannot restore."""

    class Dropped(FrozenModel):
        kept: int = 0
        hidden: int = Field(default=0, exclude=True)

    models: dict[str, type[FrozenModel]] = {"Dropped": Dropped}
    assert round_trip_failures(models, {Dropped: (Dropped(hidden=5),)}) == ["Dropped"]
    assert round_trip_failures(models, {Dropped: (Dropped(kept=5),)}) == []


def test_every_public_model_has_an_example() -> None:
    """Requires an example for every public model, so a new model must bring one."""
    missing = models_without_examples(public_models(), model_examples())
    assert not missing, "add an example to tests/fixtures/model_examples.py for: " + ", ".join(
        missing
    )


def test_missing_example_scan_flags_a_model_without_one() -> None:
    """Reports a model with no example and skips the abstract bases."""

    class Unseen(FrozenModel):
        size: int = 1

    models: dict[str, type[FrozenModel]] = {"Unseen": Unseen, "FrozenModel": FrozenModel}
    assert models_without_examples(models, {}) == ["Unseen"]
    assert models_without_examples(models, {Unseen: (Unseen(),)}) == []


def test_every_optional_field_leaves_its_default_in_some_example() -> None:
    """Requires each optional field off its default in some example, bar the reasoned exemptions."""
    left = fields_left_at_default(public_models(), model_examples(), DEFAULT_ONLY_FIELDS)
    assert not left, (
        "give an example in tests/fixtures/model_variants.py a non-default value for: "
        + ", ".join(left)
    )


def test_default_field_scan_flags_a_field_left_at_default() -> None:
    """Reports a field no example sets, and skips one-value tags and exempt fields."""

    class Tagged(FrozenModel):
        kind: typing.Literal["tagged"] = "tagged"
        size: int = 1
        note: str = ""

    models: dict[str, type[FrozenModel]] = {"Tagged": Tagged}
    examples: Examples = {Tagged: (Tagged(), Tagged(size=2))}
    assert fields_left_at_default(models, examples, {}) == ["Tagged.note"]
    assert fields_left_at_default(models, examples, {"Tagged.note": "free text"}) == []


def test_not_json_models_are_exactly_those_that_refuse() -> None:
    """Checks the listed exclusions match the refusing models, and each refuses its example."""
    models = public_models()
    refusing = {name for name, model in models.items() if model._json_refusal is not None}
    assert refusing == NOT_JSON_MODELS
    examples = model_examples()
    for name in sorted(NOT_JSON_MODELS):
        with pytest.raises(SchemaError) as caught:
            examples[models[name]][0].to_json()
        assert caught.value.code is ErrorCode.NOT_SERIALIZABLE
