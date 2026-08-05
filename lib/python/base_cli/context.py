from __future__ import annotations

import contextvars
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

from ._cleanup import remove_owned_temp_directory
from .config import FrameworkConfig


_current_context: contextvars.ContextVar[Context[Any, Any, Any] | None] = contextvars.ContextVar(
    "base_cli_current_context",
    default=None,
)

ConfigT = TypeVar("ConfigT")
ApplicationStateT = TypeVar("ApplicationStateT")
ServicesT = TypeVar("ServicesT")

__all__ = [
    "ApplicationStateT",
    "ConfigT",
    "Context",
    "ServicesT",
    "get_current_context",
    "recover_current_context",
    "reset_current_context",
    "set_current_context",
]


def _default_history_display_command(cli_name: str, _argv: list[str]) -> str:
    return cli_name.replace("_", "-")


@dataclass
class Context(Generic[ConfigT, ApplicationStateT, ServicesT]):
    """Runtime state and cleanup hooks available to an active CLI command."""

    cli_name: str
    run_id: str
    state_dir: Path
    log_dir: Path
    cache_dir: Path
    temp_dir: Path
    log_file: Path | None
    config: ConfigT
    environment: str
    debug: bool
    keep_temp: bool
    log: logging.Logger
    dry_run: bool = False
    application_home: Path | None = None
    project_root: Path | None = None
    manifest_path: Path | None = None
    project_name: str | None = None
    history_scope: str = "primary"
    history_parent_run_id: str | None = None
    user_config: object | None = None
    history_display_command: Callable[[str, list[str]], str] = _default_history_display_command
    cleanup_hooks: list[Callable[[], None]] = field(default_factory=list)
    workspace_root: Path | None = None
    quiet: bool = False
    runtime_owner: str = "default"
    owner_root: Path | None = None
    run_root: Path | None = None
    application_context: ApplicationStateT | None = field(default=None, repr=False, compare=False)
    services: ServicesT | None = field(default=None, repr=False, compare=False)
    framework_config: FrameworkConfig | None = field(default=None, repr=False, compare=False)
    config_provenance: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)
    json_output: bool = False
    rich: bool = False
    _run_metadata_path: Path | None = field(default=None, init=False, repr=False, compare=False)
    _owns_temp_dir: bool = field(default=False, init=False, repr=False, compare=False)
    _owned_temp_identity: tuple[int, int] | None = field(default=None, init=False, repr=False, compare=False)
    _owned_temp_descriptor: int | None = field(default=None, init=False, repr=False, compare=False)

    def on_cleanup(self, hook: Callable[[], None]) -> None:
        self.cleanup_hooks.append(hook)

    def bind_project(self, project_name: str | None, project_root: Path, manifest_path: Path | None = None) -> None:
        """Bind the selected project to this invocation's history context."""
        self.project_name = project_name
        self.project_root = project_root.resolve()
        self.manifest_path = manifest_path.resolve() if manifest_path is not None else None

    def _warn_cleanup_failure(self, message: str, *args: object) -> None:
        try:
            self.log.warning(message, *args)
        except BaseException:  # pylint: disable=broad-exception-caught
            pass

    def _cleanup_owned_temp_dir(self) -> None:
        if not self._owns_temp_dir:
            self._close_owned_temp_descriptor()
            return
        expected_identity = self._owned_temp_identity
        owned_descriptor = self._owned_temp_descriptor
        self._owns_temp_dir = False
        self._owned_temp_identity = None
        self._owned_temp_descriptor = None
        try:
            if expected_identity is None:
                raise RuntimeError("temp directory ownership identity is unavailable")
            remove_owned_temp_directory(
                self.temp_dir,
                self.run_root,
                self.run_id,
                expected_identity=expected_identity,
                owned_descriptor=owned_descriptor,
            )
        except BaseException as exc:  # pylint: disable=broad-exception-caught
            self._warn_cleanup_failure("Temp directory cleanup failed for '%s': %s", self.temp_dir, exc)
        finally:
            if owned_descriptor is not None:
                try:
                    os.close(owned_descriptor)
                except OSError as exc:
                    self._warn_cleanup_failure("Temp directory handle close failed: %s", exc)

    def _close_owned_temp_descriptor(self) -> None:
        owned_descriptor = self._owned_temp_descriptor
        self._owned_temp_descriptor = None
        self._owned_temp_identity = None
        self._owns_temp_dir = False
        if owned_descriptor is not None:
            try:
                os.close(owned_descriptor)
            except OSError as exc:
                self._warn_cleanup_failure("Temp directory handle close failed: %s", exc)

    def cleanup(self) -> None:
        self._cleanup_resources(preserve_temp_ownership=False)

    def _cleanup_preserving_temp_ownership(self) -> None:
        self._cleanup_resources(preserve_temp_ownership=True)

    def _cleanup_resources(self, *, preserve_temp_ownership: bool) -> None:
        for hook in self.cleanup_hooks:
            try:
                hook()
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                self._warn_cleanup_failure("Cleanup hook failed: %s", exc)
        if not self.keep_temp:
            self._cleanup_owned_temp_dir()
        elif not preserve_temp_ownership:
            self._close_owned_temp_descriptor()
        for handler in list(self.log.handlers):
            try:
                handler.flush()
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                self._warn_cleanup_failure("Log handler flush failed: %s", exc)
            try:
                handler.close()
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                self._warn_cleanup_failure("Log handler close failed: %s", exc)
            try:
                self.log.removeHandler(handler)
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                self._warn_cleanup_failure("Log handler removal failed: %s", exc)
                try:
                    self.log.handlers.remove(handler)
                except BaseException:  # pylint: disable=broad-exception-caught
                    pass


def set_current_context(
    context: Context[Any, Any, Any] | None,
) -> contextvars.Token[Context[Any, Any, Any] | None]:
    return _current_context.set(context)


def reset_current_context(token: contextvars.Token[Context[Any, Any, Any] | None]) -> None:
    try:
        _current_context.reset(token)
    except BaseException:  # pylint: disable=broad-exception-caught
        recover_current_context(token)


def recover_current_context(token: contextvars.Token[Context[Any, Any, Any] | None]) -> None:
    previous = token.old_value
    _current_context.set(None if previous is contextvars.Token.MISSING else previous)


def get_current_context() -> Context[Any, Any, Any]:
    context = _current_context.get()
    if context is None:
        raise RuntimeError("base_cli context is not active. Run inside a base_cli.App command.")
    return context
