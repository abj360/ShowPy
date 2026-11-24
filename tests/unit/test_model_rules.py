#!/usr/bin/env python3

"""
test_model_rules.py --- tests rules that every stored e8s model must follow

A stored model is one whose JSON a repository or an asset store keeps: the show and every model
it holds, the notes document, canvas comments and selection, revision records and fonts.

Contains:
    STORED_MODEL_ROOTS: lists the models whose JSON is stored, so every model they hold is too
    annotation_parts(): yields an annotation and every type nested in it, through aliases
    find_any_fields(): walks the models reachable from some roots and lists fields typed with Any
    test_no_any_in_stored_model_fields: fails on a stored model field whose annotation holds Any
    test_any_field_scan_flags_nested_aliased_and_annotated_any: proves the scan can fail
"""

from collections.abc import Iterable, Iterator
from typing import Annotated, Any, TypeAliasType, get_args, get_origin

from pydantic import BaseModel

from e8s.core.selection import Selection
from e8s.geo.models import Font
from e8s.show._notes_document import NotesDocument
from e8s.show.models import Comment, Revision, Show

STORED_MODEL_ROOTS: tuple[type[BaseModel], ...] = (
    Show,
    NotesDocument,
    Comment,
    Selection,
    Revision,
    Font,
)
MIN_STORED_MODELS = 40


def annotation_parts(annotation: object) -> Iterator[object]:
    """Yields an annotation and every type nested in it, looking through type aliases.

    `Annotated` metadata is skipped: it holds constraints, not the stored type.
    """
    pending = [annotation]
    seen: set[int] = set()
    while pending:
        part = pending.pop()
        if id(part) in seen:
            continue
        seen.add(id(part))
        yield part
        origin = get_origin(part)
        args = get_args(part)
        if isinstance(part, TypeAliasType):
            pending.append(part.__value__)
        elif isinstance(origin, TypeAliasType):
            pending.append(origin.__value__)
        pending.extend(args[:1] if origin is Annotated else args)


def find_any_fields(roots: Iterable[type[BaseModel]]) -> tuple[int, list[str]]:
    """Walks the models reachable from `roots` through their fields and lists Any-typed fields.

    Args:
        roots: Models to start from; every model a field of theirs names is walked too.

    Returns:
        walked: Number of models walked.
        offenders: `Model.field` for each field whose annotation holds Any, sorted.
    """
    pending = list(roots)
    walked: set[type[BaseModel]] = set()
    offenders: list[str] = []
    while pending:
        model = pending.pop()
        if model in walked:
            continue
        walked.add(model)
        for name, field in model.model_fields.items():
            for part in annotation_parts(field.annotation):
                if part is Any:
                    offenders.append(f"{model.__qualname__}.{name}")
                elif isinstance(part, type) and issubclass(part, BaseModel):
                    pending.append(part)
    return len(walked), sorted(set(offenders))


def test_no_any_in_stored_model_fields() -> None:
    """Requires every stored model field to name its type, so stored JSON is checked in full."""
    walked, offenders = find_any_fields(STORED_MODEL_ROOTS)
    assert walked >= MIN_STORED_MODELS, f"the walk reached only {walked} models"
    assert offenders == []


# Any: the negative control plants it in an alias, a container and an Annotated field.
type _LooseList = list[Any]


class _Leaf(BaseModel):
    payload: dict[str, Any]
    count: int = 0


class _Root(BaseModel):
    leaf: _Leaf | None = None
    loose: Annotated[_LooseList, "metadata is skipped"] = []
    name: str = ""


def test_any_field_scan_flags_nested_aliased_and_annotated_any() -> None:
    """Reports Any inside a nested model's dict and inside an annotated type alias."""
    assert find_any_fields([_Root]) == (2, ["_Leaf.payload", "_Root.loose"])
