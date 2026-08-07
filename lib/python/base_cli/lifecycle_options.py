from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "LIFECYCLE_META_KEY",
    "LifecycleOption",
    "LifecycleOptions",
    "LifecycleValues",
    "get_lifecycle_values",
]


LIFECYCLE_META_KEY = "base_cli.lifecycle"


@dataclass(frozen=True, init=False)
class LifecycleOption:
    """Immutable public configuration for one lifecycle-owned Click option."""

    param_decls: tuple[str, ...]
    name: str | None
    help: str | None
    metavar: str | None
    envvar: str | tuple[str, ...] | None
    show_envvar: bool
    show_default: bool | str | None
    hidden: bool
    default: Any

    def __init__(
        self,
        *param_decls: str,
        name: str | None = None,
        help: str | None = None,  # pylint: disable=redefined-builtin
        metavar: str | None = None,
        envvar: str | tuple[str, ...] | list[str] | None = None,
        show_envvar: bool = False,
        show_default: bool | str | None = None,
        hidden: bool = False,
        default: Any = None,
    ) -> None:
        if not param_decls:
            raise ValueError("LifecycleOption requires at least one option declaration.")
        if not all(isinstance(declaration, str) and declaration for declaration in param_decls):
            raise TypeError("LifecycleOption declarations must be non-empty strings.")
        if not all(declaration.startswith(("-", "/")) for declaration in param_decls):
            raise ValueError(
                "LifecycleOption declarations must be visible Click option names "
                "starting with '-' or '/'; use name= for the Click destination."
            )
        if len(set(param_decls)) != len(param_decls):
            raise ValueError("LifecycleOption declarations must be unique.")
        if name is not None and (not isinstance(name, str) or not name or not name.isidentifier()):
            raise TypeError("LifecycleOption name must be a non-empty Python identifier or None.")
        if help is not None and not isinstance(help, str):
            raise TypeError("LifecycleOption help must be a string or None.")
        if metavar is not None and not isinstance(metavar, str):
            raise TypeError("LifecycleOption metavar must be a string or None.")
        if envvar is not None and not isinstance(envvar, str):
            try:
                envvar = tuple(envvar)
            except TypeError as exc:
                raise TypeError("LifecycleOption envvar must be a string, a sequence of strings, or None.") from exc
            if not all(isinstance(value, str) and value for value in envvar):
                raise TypeError("LifecycleOption envvar sequences must contain non-empty strings.")
        if isinstance(envvar, str) and not envvar:
            raise TypeError("LifecycleOption envvar must not be empty.")
        if not isinstance(show_envvar, bool):
            raise TypeError("LifecycleOption show_envvar must be a bool.")
        if not (show_default is None or isinstance(show_default, bool) or isinstance(show_default, str)):
            raise TypeError("LifecycleOption show_default must be a bool, string, or None.")
        if not isinstance(hidden, bool):
            raise TypeError("LifecycleOption hidden must be a bool.")

        object.__setattr__(self, "param_decls", tuple(param_decls))
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "help", help)
        object.__setattr__(self, "metavar", metavar)
        object.__setattr__(self, "envvar", envvar)
        object.__setattr__(self, "show_envvar", show_envvar)
        object.__setattr__(self, "show_default", show_default)
        object.__setattr__(self, "hidden", hidden)
        object.__setattr__(self, "default", default)


def _debug_option() -> LifecycleOption:
    return LifecycleOption(
        "--debug",
        help="Enable DEBUG logging on the user-facing stream.",
    )


def _quiet_option() -> LifecycleOption:
    return LifecycleOption(
        "--quiet",
        "-q",
        help="Suppress INFO logs on the user-facing stream.",
    )


def _environment_option() -> LifecycleOption:
    return LifecycleOption(
        "--environment",
        help="Set the CLI environment.",
    )


def _config_option() -> LifecycleOption:
    return LifecycleOption(
        "--config",
        help="Load an additional config file.",
    )


def _keep_temp_option() -> LifecycleOption:
    return LifecycleOption(
        "--keep-temp",
        help="Preserve this run's temp directory.",
    )


def _log_file_option() -> LifecycleOption:
    return LifecycleOption(
        "--log-file",
        help="Override the persistent log file.",
    )


def _version_option() -> LifecycleOption:
    return LifecycleOption("--version")


@dataclass(frozen=True)
class LifecycleOptions:
    """Composable option policy used by native and attached applications."""

    debug: LifecycleOption | None = field(default_factory=_debug_option)
    quiet: LifecycleOption | None = field(default_factory=_quiet_option)
    environment: LifecycleOption | None = field(default_factory=_environment_option)
    config: LifecycleOption | None = field(default_factory=_config_option)
    keep_temp: LifecycleOption | None = field(default_factory=_keep_temp_option)
    log_file: LifecycleOption | None = field(default_factory=_log_file_option)
    version: LifecycleOption | None = field(default_factory=_version_option)
    dry_run: LifecycleOption | None = None
    json: LifecycleOption | None = None

    def __post_init__(self) -> None:
        for key in (
            "debug",
            "quiet",
            "environment",
            "config",
            "keep_temp",
            "log_file",
            "version",
            "dry_run",
            "json",
        ):
            value = getattr(self, key)
            if value is not None and not isinstance(value, LifecycleOption):
                raise TypeError(f"LifecycleOptions.{key} must be a LifecycleOption or None.")


@dataclass(frozen=True)
class LifecycleValues:
    """Normalized lifecycle values resolved for the active Click context."""

    debug: bool = False
    quiet: bool = False
    environment: str | None = None
    config: Path | None = None
    keep_temp: bool = False
    log_file: Path | None = None
    dry_run: bool = False
    json: bool = False


def get_lifecycle_values(click_context: Any | None = None) -> LifecycleValues:
    """Return normalized lifecycle values stored on an active Click context."""

    if click_context is None:
        try:
            import click
        except ImportError as exc:
            raise RuntimeError("Click is required to inspect lifecycle option values.") from exc
        click_context = click.get_current_context(silent=True)
    if click_context is None:
        raise RuntimeError("Lifecycle option values are not available outside a Click invocation.")
    value = getattr(click_context, "meta", {}).get(LIFECYCLE_META_KEY)
    if not isinstance(value, LifecycleValues):
        raise RuntimeError("Lifecycle option values are not available for this Click context.")
    return value
