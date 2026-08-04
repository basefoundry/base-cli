"""Public runtime layout contract for consumer profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["RuntimeLayout"]


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
