"""
deprecation.py --- warns when a public name that leaves the library is used

Contains:
    DeprecatedWarning: warns that a deprecated API was used and names its replacement
    warn_leaving(): warns that a name leaves the library, naming any planned replacements
    leaving_call(): decorates a function that leaves the library so each call warns, then runs it
"""

import functools
import warnings
from collections.abc import Callable
from typing import Final

WARNING_STACK_LEVEL: Final = 3


class DeprecatedWarning(DeprecationWarning):
    """Warns that a deprecated API was used and names its replacement."""


def _dotted_name(target: str | Callable[..., object]) -> str:
    if isinstance(target, str):
        return target
    return f"{target.__module__}.{target.__qualname__}"


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
        old: Dotted public name that leaves, such as `e8s.sheet.templates.get_sheet`.
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
