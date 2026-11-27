"""
validate_issue.py --- builds the validation issues every whole-show check reports

Contains:
    Level: names the two issue levels
    ERROR: marks an issue that blocks
    WARNING: marks an issue that only informs
    make_issue(): builds a validation issue, with optional context
    item_context(): builds an issue context naming the stored item the issue is about
"""

from typing import Literal

from e8s.core.error_codes import ErrorCode
from e8s.core.json_types import JsonValue
from e8s.core.validation import ValidationIssue
from e8s.show.change_models import ItemKey

type Level = Literal["error", "warning"]
ERROR: Level = "error"
WARNING: Level = "warning"


def make_issue(
    level: Level,
    code: ErrorCode,
    where: str,
    message: str,
    hint: str,
    context: dict[str, JsonValue] | None = None,
) -> ValidationIssue:
    """Builds a validation issue from its parts.

    Args:
        level: Whether the issue blocks or only informs.
        code: Stable issue code.
        where: Location of the problem.
        message: What is wrong.
        hint: What would make it valid.
        context: JSON values that help debug the issue; empty when None.

    Returns:
        issue: The validation issue.
    """
    return ValidationIssue(
        level=level,
        code=code,
        where=where,
        message=message,
        hint=hint,
        context=context or {},
    )


def item_context(key: ItemKey, **extra: JsonValue) -> dict[str, JsonValue]:
    """Builds an issue context that names the item in `item`, beside any other values.

    >>> from e8s.show.change_models import ItemKey
    >>> item_context(ItemKey(kind="form", path=("row",)), spot="T1")
    {'item': {'kind': 'form', 'path': ['row'], 'role': None}, 'spot': 'T1'}

    Args:
        key: The stored item the issue is about.
        extra: Other JSON values that help debug the issue.

    Returns:
        context: The item's JSON key under `item`, then the other values.
    """
    return {"item": key.to_json(), **extra}
