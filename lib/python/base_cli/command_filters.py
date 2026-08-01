"""Shared command-name filter normalization for CLI reports."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias


__all__ = [
    "CommandFilterNormalizer",
    "command_matches",
    "normalize_command_filter",
    "normalize_command_filters",
]


CommandFilterNormalizer: TypeAlias = Callable[[str], str]


def _normalize_command_name(value: str) -> str:
    return value.replace("_", "-")


def normalize_command_filter(
    value: str,
    *,
    normalizer: CommandFilterNormalizer | None = None,
) -> str:
    """Normalize one command name for matching.

    ``normalizer`` lets a consumer apply its own compatibility policy before
    the generic underscore-to-hyphen conversion. The callback receives a
    stripped, lower-case command name and must return its canonical name.
    """

    normalized = value.strip().lower()
    if normalizer is not None:
        normalized = normalizer(normalized)
    return _normalize_command_name(normalized)


def normalize_command_filters(
    value: str | None,
    *,
    normalizer: CommandFilterNormalizer | None = None,
) -> tuple[str, ...]:
    """Normalize a comma-separated command filter and reject empty entries."""

    if value is None:
        return ()
    parts = value.split(",")
    if any(not part.strip() for part in parts):
        raise ValueError("Option '--command' expects comma-separated command names without empty entries.")
    normalized = tuple(
        dict.fromkeys(normalize_command_filter(part, normalizer=normalizer) for part in parts)
    )
    if not normalized or any(not command for command in normalized):
        raise ValueError("Option '--command' expects at least one command name.")
    return normalized


def command_matches(
    value: str,
    command_filters: tuple[str, ...],
    *,
    normalizer: CommandFilterNormalizer | None = None,
) -> bool:
    """Return whether a command value matches one of the normalized filters."""

    return normalize_command_filter(value, normalizer=normalizer) in command_filters
