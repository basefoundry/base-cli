"""Public runtime layout contract for consumer profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["RetentionPolicy", "RuntimeLayout"]


DEFAULT_MAX_BUNDLES = 20
DEFAULT_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
DEFAULT_MAX_TOTAL_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class RetentionPolicy:
    """Bounds for complete invocation bundles.

    ``None`` disables an individual bound.  Protected bundles (the active
    invocation, inherited parent bundles, and bundles marked ``preserve`` in
    ``run.json``) are always retained, even when that means a bound cannot be
    met exactly.
    """

    max_bundles: int | None = None
    max_age_seconds: float | None = None
    max_total_bytes: int | None = None

    @classmethod
    def safe_defaults(cls) -> RetentionPolicy:
        """Return the bounded policy used by a durable :class:`App` by default."""

        return cls(
            max_bundles=DEFAULT_MAX_BUNDLES,
            max_age_seconds=DEFAULT_MAX_AGE_SECONDS,
            max_total_bytes=DEFAULT_MAX_TOTAL_BYTES,
        )

    def __post_init__(self) -> None:
        if self.max_bundles is not None and self.max_bundles < 1:
            raise ValueError("max_bundles must be greater than 0 when set.")
        if self.max_age_seconds is not None and self.max_age_seconds < 0:
            raise ValueError("max_age_seconds must be non-negative when set.")
        if self.max_total_bytes is not None and self.max_total_bytes < 1:
            raise ValueError("max_total_bytes must be greater than 0 when set.")


@dataclass(frozen=True)
class RuntimeLayout:
    """Filesystem locations owned by one base-cli runtime binding.

    Profiles may construct this value themselves or return it from a custom
    runtime resolver. The layout deliberately contains paths only; ownership,
    retention, and persistence policy remain profile decisions.
    """

    owner_root: Path
    run_root: Path
    state_dir: Path
    log_dir: Path
    cache_dir: Path
    temp_dir: Path
