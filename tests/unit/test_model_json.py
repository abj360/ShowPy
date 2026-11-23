#!/usr/bin/env python3

"""
test_model_json.py --- tests that every public model reads back what to_json writes

Contains:
    public_models(): lists the public FrozenModel subclasses e8s exports, by name
    models_without_examples(): lists the public models that have no example instance
    test_every_public_model_round_trips_json: requires from_json(to_json(m)) == m for each model
    test_every_public_model_has_an_example: fails when a public model has no example instance
    test_missing_example_scan_flags_a_model_without_one: proves the coverage scan can fail
    test_not_json_models_are_exactly_those_that_refuse: keeps the listed exclusions in step with
        the models that refuse JSON, and checks each refuses with NOT_SERIALIZABLE
"""

import warnings

import pytest

import e8s
from e8s._api_table import PUBLIC_API
from e8s.core.deprecation import DeprecatedWarning
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError
from e8s.core.types import FrozenModel
from tests.fixtures.model_examples import ABSTRACT_MODELS, NOT_JSON_MODELS, model_examples


def public_models() -> dict[str, type[FrozenModel]]:
    """Lists the public FrozenModel subclasses e8s exports, by public name.

    Returns:
        models: Public name mapped to the model class.
    """
    models: dict[str, type[FrozenModel]] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecatedWarning)
        for name, _, _ in PUBLIC_API:
            value = getattr(e8s, name)
            if isinstance(value, type) and issubclass(value, FrozenModel):
                models[name] = value
    return models


def models_without_examples(
    models: dict[str, type[FrozenModel]], examples: dict[type[FrozenModel], FrozenModel]
) -> list[str]:
    """Lists the models, bar the abstract bases, that have no example instance.

    Args:
        models: Public name mapped to the model class.
        examples: Model class mapped to an example instance.

    Returns:
        names: Sorted public names of the models with no example.
    """
    return sorted(
        name
        for name, model in models.items()
        if model not in examples and name not in ABSTRACT_MODELS
    )


def test_every_public_model_round_trips_json() -> None:
    examples = model_examples()
    failed = [
        name
        for name, model in public_models().items()
        if name not in NOT_JSON_MODELS
        and model in examples
        and model.from_json(examples[model].to_json()) != examples[model]
    ]
    assert not failed, f"from_json(to_json(m)) != m for: {', '.join(failed)}"


def test_every_public_model_has_an_example() -> None:
    missing = models_without_examples(public_models(), model_examples())
    assert not missing, "add an example to tests/fixtures/model_examples.py for: " + ", ".join(
        missing
    )


def test_missing_example_scan_flags_a_model_without_one() -> None:
    class Unseen(FrozenModel):
        size: int = 1

    models: dict[str, type[FrozenModel]] = {"Unseen": Unseen, "FrozenModel": FrozenModel}
    assert models_without_examples(models, {}) == ["Unseen"]
    assert models_without_examples(models, {Unseen: Unseen()}) == []


def test_not_json_models_are_exactly_those_that_refuse() -> None:
    models = public_models()
    refusing = {name for name, model in models.items() if model._json_refusal is not None}
    assert refusing == NOT_JSON_MODELS
    examples = model_examples()
    for name in sorted(NOT_JSON_MODELS):
        with pytest.raises(SchemaError) as caught:
            examples[models[name]].to_json()
        assert caught.value.code is ErrorCode.NOT_SERIALIZABLE
