"""
deprecation.py --- warns when a renamed or moved public name is used

Contains:
    DeprecatedWarning: warns that a deprecated API was used and names its replacement
    moved(): builds a module `__getattr__` that serves moved names and warns on each use
    renamed_field(): builds a read-only property that serves a renamed field, optionally converted
    renamed_keyword(): resolves a keyword argument that was renamed, warning on the old name
    warn_deprecated(): warns, at the user's calling line, that one use of an API is deprecated
    deprecated_call(): decorates a superseded function so each call warns, then runs it
    warn_leaving(): warns that a name leaves the library, naming any planned replacements
    leaving_call(): decorates a function that leaves the library so each call warns, then runs it
    leave_field(): makes an outside read of a stored model field warn that the field leaves
"""

import functools
import importlib
import sys
import warnings
from collections.abc import Callable, Mapping
from typing import Final, Never

WARNING_STACK_LEVEL: Final = 3
LIBRARY_MODULE_PREFIXES: Final = ("e8s.", "pydantic.")
READER_FRAME_DEPTH: Final = 1


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
        >>> from e8s.core.deprecation import DeprecatedWarning, moved
        >>> hook = moved("old", {"Err": "e8s.core.errors.SchemaError"}, since="0.4.0",
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
        >>> from e8s.core.deprecation import renamed_field
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
        >>> from e8s.core.deprecation import renamed_keyword
        >>> renamed_keyword("tol", None, "abs_tol", 0.5, default=1e-6, remove="0.6.0")
        0.5
    """
    if old_value is not None and new_value is not None:
        from e8s.core.errors import SchemaError  # errors imports this module

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
        >>> from e8s.core.deprecation import warn_deprecated
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
        >>> from e8s.core.deprecation import deprecated_call
        >>> @deprecated_call("e8s.core.util.approx_equal", since="0.4.0", remove="0.6.0")
        ... def close(a: float, b: float) -> bool:
        ...     return abs(a - b) < 1e-6
        >>> with warnings.catch_warnings(record=True) as caught:
        ...     warnings.simplefilter("always")
        ...     close(1.0, 1.0)
        True
        >>> "replaced by `e8s.core.util.approx_equal`" in str(caught[0].message)
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


def _replacement_clause(replacement: str | tuple[str, ...] | None, remove: str) -> str:
    """Words the end of a leaving message: a full stop, or the replacements and their release."""
    names = (replacement,) if isinstance(replacement, str) else (replacement or ())
    quoted = [f"`e8s.{name}`" for name in names]
    if not quoted:
        return "."
    if len(quoted) == 1:
        return f"; its replacement {quoted[0]} arrives in {remove}."
    listed = f"{', '.join(quoted[:-1])} and {quoted[-1]}"
    return f"; its replacements {listed} arrive in {remove}."


def warn_leaving(
    old: str,
    *,
    since: str,
    remove: str,
    replacement: str | tuple[str, ...] | None = None,
    extra_frames: int = 0,
) -> None:
    """Warns DeprecatedWarning that `old` leaves the library, at the user's calling line.

    The message says when the name was deprecated and when it leaves. With replacements it lists
    them in the given order and says they arrive in `remove`, the release that removes the old
    name.

    Args:
        old: Dotted public name that leaves, such as `e8s.sheet.render.render_sheet`.
        since: Release that deprecated the name.
        remove: Release that removes the name; also the release that adds its replacements.
        replacement: Public name, or names, without the `e8s.` prefix, that replace it; None
            if none.
        extra_frames: Frames between the public name's use and this call, such as a helper.

    Example:
        >>> import warnings
        >>> from e8s.core.deprecation import warn_leaving
        >>> with warnings.catch_warnings(record=True) as caught:
        ...     warnings.simplefilter("always")
        ...     warn_leaving("e8s.old", since="0.4.0", remove="0.7.0")
        ...     warn_leaving("e8s.old", since="0.4.0", remove="0.7.0", replacement=("a", "b"))
        >>> str(caught[0].message)
        '`e8s.old` is deprecated since 0.4.0 and leaves the library in 0.7.0.'
        >>> str(caught[1].message).endswith("`e8s.a` and `e8s.b` arrive in 0.7.0.")
        True
    """
    message = f"`{old}` is deprecated since {since} and leaves the library in {remove}"
    message += _replacement_clause(replacement, remove)
    warnings.warn(message, DeprecatedWarning, stacklevel=WARNING_STACK_LEVEL + extra_frames)


def leaving_call[**P, R](
    *, since: str, remove: str, replacement: str | tuple[str, ...] | None = None
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorates a function that leaves the library so each call warns, then runs it.

    Args:
        since: Release that deprecated the function.
        remove: Release that removes the function; also the release that adds its replacements.
        replacement: Public name, or names, without the `e8s.` prefix, that replace it; None
            if none.

    Returns:
        decorator: Wraps a function so every call warns DeprecatedWarning at the caller.

    Example:
        >>> import warnings
        >>> from e8s.core.deprecation import leaving_call
        >>> @leaving_call(since="0.4.0", remove="99.0.0")
        ... def old() -> int:
        ...     return 1
        >>> with warnings.catch_warnings(record=True) as caught:
        ...     warnings.simplefilter("always")
        ...     old()
        1
        >>> str(caught[0].message).endswith("leaves the library in 99.0.0.")
        True
    """

    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        old = _dotted_name(function)

        @functools.wraps(function)
        def warn_and_run(*args: P.args, **kwargs: P.kwargs) -> R:
            warn_leaving(old, since=since, remove=remove, replacement=replacement)
            return function(*args, **kwargs)

        return warn_and_run

    return decorate


class _LeavingField:
    """Data descriptor that warns when code outside the library reads a stored field."""

    def __init__(
        self,
        old: str,
        field: str,
        *,
        since: str,
        remove: str,
        replacement: str | tuple[str, ...] | None,
    ) -> None:
        self.old = old
        self.field = field
        self.since = since
        self.remove = remove
        self.replacement = replacement

    def __get__(self, instance: object, owner: type) -> object:
        if instance is None:
            return self
        module = str(sys._getframe(READER_FRAME_DEPTH).f_globals.get("__name__", ""))
        if module != "e8s" and not module.startswith(LIBRARY_MODULE_PREFIXES):
            warn_leaving(
                self.old, since=self.since, remove=self.remove, replacement=self.replacement
            )
        return vars(instance)[self.field]

    def __set__(self, instance: object, value: object) -> Never:
        raise AttributeError(f"`{self.old}` is read-only")


def leave_field(
    model: type,
    field: str,
    *,
    since: str,
    remove: str,
    replacement: str | tuple[str, ...] | None = None,
) -> None:
    """Makes an outside read of a stored model field warn that the field leaves the library.

    Decoding, dumping, copying and comparing a model read the stored value directly, and reads
    from modules inside `e8s` or `pydantic` stay silent. Call it once, right after the class
    body.

    Args:
        model: Pydantic model class that holds the field.
        field: Name of the field that leaves.
        since: Release that deprecated the field.
        remove: Release that removes the field; also the release that adds its replacements.
        replacement: Public name, or names, without the `e8s.` prefix, that replace it; None
            if none.

    Example:
        >>> import warnings
        >>> from pydantic import BaseModel
        >>> from e8s.core.deprecation import leave_field
        >>> class Box(BaseModel):
        ...     size: int = 3
        >>> leave_field(Box, "size", since="0.4.0", remove="0.7.0")
        >>> with warnings.catch_warnings(record=True) as caught:
        ...     warnings.simplefilter("always")
        ...     eval("Box().size", {"__name__": "app", "Box": Box})
        3
        >>> str(caught[0].message)
        '`e8s.Box.size` is deprecated since 0.4.0 and leaves the library in 0.7.0.'
    """
    old = f"e8s.{model.__name__}.{field}"
    descriptor = _LeavingField(old, field, since=since, remove=remove, replacement=replacement)
    type.__setattr__(model, field, descriptor)
