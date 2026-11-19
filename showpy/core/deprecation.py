"""
deprecation.py --- warns when a renamed or moved public name is used

Contains:
    DeprecatedWarning: warns that a deprecated API was used and names its replacement
    moved(): builds a module `__getattr__` that serves moved names and warns on each use
    renamed_field(): builds a read-only property that serves a renamed field and warns
"""

import importlib
import warnings
from collections.abc import Callable, Mapping
from typing import Final

WARNING_STACK_LEVEL: Final = 3


class DeprecatedWarning(DeprecationWarning):
    """Warns that a deprecated API was used and names its replacement."""


def _warn_moved(old: str, new: str, remove: str, extra_frames: int) -> None:
    """Emits the warning for a moved name, attributed to the code that used it."""
    message = f"`{old}` moved to `{new}`; the old path is removed in {remove}"
    warnings.warn(message, DeprecatedWarning, stacklevel=WARNING_STACK_LEVEL + extra_frames)


def moved(
    module_name: str, names: Mapping[str, str], *, since: str, remove: str, extra_frames: int = 0
) -> Callable[[str], object]:
    """Builds a module `__getattr__` that warns and returns the object a name moved to.

    Args:
        module_name: Dotted name of the module that used to hold the names.
        names: Old attribute name mapped to the dotted path `package.module.attribute` it moved to.
        since: Release that deprecated the names; recorded in the API table, not in the message.
        remove: Release that removes the old path.
        extra_frames: Frames between the user's attribute access and the call of the hook.

    Returns:
        getattr_hook: A function to assign to `__getattr__` in the old module; it raises
        `AttributeError` for a name outside `names`.

    Example:
        >>> import warnings
        >>> from showpy.core.deprecation import DeprecatedWarning, moved
        >>> hook = moved("old", {"Err": "showpy.core.errors.SchemaError"}, since="0.4.0",
        ...              remove="0.6.0")
        >>> with warnings.catch_warnings(record=True) as caught:
        ...     warnings.simplefilter("always")
        ...     hook("Err").__name__
        'SchemaError'
        >>> issubclass(caught[0].category, DeprecatedWarning)
        True
    """
    del since

    def getattr_hook(name: str) -> object:
        if name not in names:
            raise AttributeError(f"module {module_name!r} has no attribute {name!r}")
        target = names[name]
        _warn_moved(name, target, remove, extra_frames)
        target_module, _, attribute = target.rpartition(".")
        return getattr(importlib.import_module(target_module), attribute)

    return getattr_hook


class _RenamedField(property):
    """Property that warns and reads another attribute; learns its own name when assigned."""

    old_name: str = "field"

    def __set_name__(self, owner: type, name: str) -> None:
        self.old_name = name


def renamed_field(new: str, *, since: str, remove: str) -> property:
    """Builds a read-only property under an old field name that warns and reads the new one.

    Args:
        new: Attribute that replaced the old one.
        since: Release that deprecated the old name; recorded in the API table, not in the message.
        remove: Release that removes the old name.

    Returns:
        old_field: A property to assign to the old name in the class body.

    Example:
        >>> import warnings
        >>> from showpy.core.deprecation import renamed_field
        >>> class Box:
        ...     size = 3
        ...     width = renamed_field("size", since="0.4.0", remove="0.6.0")
        >>> with warnings.catch_warnings(record=True):
        ...     warnings.simplefilter("always")
        ...     Box().width
        3
    """
    del since

    def read(instance: object) -> object:
        message = f"`{field.old_name}` renamed to `{new}`; the old name is removed in {remove}"
        warnings.warn(message, DeprecatedWarning, stacklevel=WARNING_STACK_LEVEL - 1)
        return getattr(instance, new)

    field = _RenamedField(read)
    return field
