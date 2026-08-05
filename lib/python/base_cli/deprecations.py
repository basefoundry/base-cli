"""Small, consistent deprecation primitives for public base-cli APIs."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar


P = ParamSpec("P")
R = TypeVar("R")


class BaseCliDeprecationWarning(DeprecationWarning):
    """Warning emitted when a supported base-cli API is being retired."""


def deprecated(
    since: str,
    *,
    remove: str,
    alternative: str | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Mark a callable as deprecated while preserving its normal behavior.

    ``since`` and ``remove`` are release identifiers, not free-form dates. The
    decorator emits :class:`BaseCliDeprecationWarning` on every call so an
    application can choose whether to show, record, or fail on the warning.
    ``stacklevel=2`` points the warning at the caller rather than this helper.
    """

    if not since.strip():
        raise ValueError("since must not be empty")
    if not remove.strip():
        raise ValueError("remove must not be empty")

    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        message = f"{function.__qualname__} is deprecated since {since} and will be removed in {remove}."
        if alternative:
            message += f" Use {alternative} instead."

        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            warnings.warn(message, BaseCliDeprecationWarning, stacklevel=2)
            return function(*args, **kwargs)

        return wrapped

    return decorate


__all__ = ["BaseCliDeprecationWarning", "deprecated"]
