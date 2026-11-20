"""
deprecation.py --- warns when a renamed or moved public name is used

Contains:
    DeprecatedWarning: warns that a deprecated API was used and names its replacement
    moved(): builds a module `__getattr__` that serves moved names and warns on each use
    renamed_field(): builds a read-only property that serves a renamed field, optionally converted
    renamed_keyword(): resolves a keyword argument that was renamed, warning on the old name
    warn_deprecated(): warns, at the user's calling line, that one use of an API is deprecated
    deprecated_call(): decorates a superseded function so each call warns, then runs it
"""

import functools
import importlib
import warnings
from collections.abc import Callable, Mapping
from typing import Final, Never

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


def renamed_field(
    new: str, *, since: str, remove: str, read: Callable[[Never], object] | None = None
) -> property:
    """Builds a read-only property under an old field name that warns and reads the new one.

    Args:
        new: Attribute that replaced the old one.
        since: Release that deprecated the old name; recorded in the API table, not in the message.
        remove: Release that removes the old name.
        read: Converts the new attribute's value into what the old field held; the value itself
            when omitted.

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

    def read_old(instance: object) -> object:
        message = f"`{field.old_name}` renamed to `{new}`; the old name is removed in {remove}"
        warnings.warn(message, DeprecatedWarning, stacklevel=WARNING_STACK_LEVEL - 1)
        value = getattr(instance, new)
        return value if read is None else read(value)

    field = _RenamedField(read_old)
    return field


def renamed_keyword[T](
    old: str, old_value: T | None, new: str, new_value: T | None, *, default: T, remove: str
) -> T:
    """Returns the value of a renamed keyword argument, warning when the old name was used.

    Call it from the function that renamed the keyword; the warning points at that function's
    caller. Passing both names raises SchemaError.

    Args:
        old: Previous keyword name.
        old_value: Value passed under the old name, or None when it was not passed.
        new: Current keyword name.
        new_value: Value passed under the new name, or None when it was not passed.
        default: Value to use when neither name was passed.
        remove: Release that removes the old keyword.

    Returns:
        value: The value under the new name, else under the old name, else `default`.

    Example:
        >>> from showpy.core.deprecation import renamed_keyword
        >>> renamed_keyword("tol", None, "abs_tol", 0.5, default=1e-6, remove="0.6.0")
        0.5
    """
    if old_value is not None and new_value is not None:
        from showpy.core.errors import SchemaError  # errors imports this module

        raise SchemaError(
            f"`{old}` and `{new}` were both passed",
            hint=f"pass only `{new}`; `{old}` is its deprecated name",
            context={"old": old, "new": new},
        )
    if old_value is not None:
        message = f"`{old}` renamed to `{new}`; the old keyword is removed in {remove}"
        warnings.warn(message, DeprecatedWarning, stacklevel=WARNING_STACK_LEVEL)
        return old_value
    return default if new_value is None else new_value


def warn_deprecated(old: str, new: str, *, since: str, remove: str, extra_frames: int = 0) -> None:
    """Warns DeprecatedWarning that `old` is replaced by `new`, at the user's calling line.

    Call it from the public function whose use is deprecated, for instance only on the branch
    that takes an argument type it will stop accepting; the warning names the line that called
    that function, so default filters show it in user code.

    Args:
        old: The deprecated use, such as a function or a function given one argument type.
        new: The replacement to name.
        since: Release that deprecated the use; recorded in the API table, not in the message.
        remove: Release that removes the use.
        extra_frames: Frames between the public function and this call, such as a helper.

    Example:
        >>> import warnings
        >>> from showpy.core.deprecation import warn_deprecated
        >>> def turn(target: object) -> str:
        ...     if isinstance(target, frozenset):
        ...         warn_deprecated("turn(frozenset)", "turn_set", since="0.4.0", remove="0.6.0")
        ...     return "turned"
        >>> with warnings.catch_warnings(record=True) as caught:
        ...     warnings.simplefilter("always")
        ...     turn((1.0, 2.0)), turn(frozenset())
        ('turned', 'turned')
        >>> [str(w.message) for w in caught]
        ['`turn(frozenset)` is replaced by `turn_set`; the old use is removed in 0.6.0']
    """
    del since
    message = f"`{old}` is replaced by `{new}`; the old use is removed in {remove}"
    warnings.warn(message, DeprecatedWarning, stacklevel=WARNING_STACK_LEVEL + extra_frames)


def _dotted_name(target: str | Callable[..., object]) -> str:
    if isinstance(target, str):
        return target
    return f"{target.__module__}.{target.__qualname__}"


def deprecated_call[**P, R](
    replacement: str | Callable[..., object], *, since: str, remove: str
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorates a superseded function so each call warns, naming its replacement, then runs it.

    The decorated function keeps its name, docstring and signature for callers and type
    checkers; its body forwards to the replacement. A dotted path names a replacement that the
    old module must not import, such as one in a higher layer.

    Args:
        replacement: The function that supersedes the decorated one, or its dotted path.
        since: Release that deprecated the function; recorded in the API table, not the message.
        remove: Release that removes the decorated function.

    Returns:
        decorator: Wraps a function so every call warns DeprecatedWarning at the caller.

    Example:
        >>> import warnings
        >>> from showpy.core.deprecation import deprecated_call
        >>> @deprecated_call("showpy.core.util.approx_equal", since="0.4.0", remove="0.6.0")
        ... def close(a: float, b: float) -> bool:
        ...     return abs(a - b) < 1e-6
        >>> with warnings.catch_warnings(record=True) as caught:
        ...     warnings.simplefilter("always")
        ...     close(1.0, 1.0)
        True
        >>> "replaced by `showpy.core.util.approx_equal`" in str(caught[0].message)
        True
    """
    target = _dotted_name(replacement)

    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        old = _dotted_name(function)

        @functools.wraps(function)
        def warn_and_run(*args: P.args, **kwargs: P.kwargs) -> R:
            warn_deprecated(old, target, since=since, remove=remove)
            return function(*args, **kwargs)

        return warn_and_run

    return decorate
