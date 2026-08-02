from __future__ import annotations

import contextvars
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


_current_context: contextvars.ContextVar[Context | None] = contextvars.ContextVar(
    "base_cli_current_context",
    default=None,
)


def _default_history_display_command(cli_name: str, _argv: list[str]) -> str:
    return cli_name.replace("_", "-")


@dataclass
class Context:
    """Runtime state and cleanup hooks available to an active CLI command."""

    cli_name: str
    run_id: str
    state_dir: Path
    log_dir: Path
    cache_dir: Path
    temp_dir: Path
    log_file: Path | None
    config: dict[str, Any]
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
    _run_metadata_path: Path | None = field(default=None, init=False, repr=False, compare=False)

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

    def cleanup(self) -> None:
        for hook in self.cleanup_hooks:
            try:
                hook()
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                self._warn_cleanup_failure("Cleanup hook failed: %s", exc)
        if not self.keep_temp and self.temp_dir.exists():
            try:
                shutil.rmtree(self.temp_dir)
                for parent in (self.temp_dir.parent, self.temp_dir.parent.parent):
                    try:
                        parent.rmdir()
                    except OSError:
                        break
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                self._warn_cleanup_failure("Temp directory cleanup failed for '%s': %s", self.temp_dir, exc)
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


def set_current_context(context: Context | None) -> contextvars.Token[Context | None]:
    return _current_context.set(context)


def reset_current_context(token: contextvars.Token[Context | None]) -> None:
    try:
        _current_context.reset(token)
    except BaseException:  # pylint: disable=broad-exception-caught
        recover_current_context(token)


def recover_current_context(token: contextvars.Token[Context | None]) -> None:
    previous = token.old_value
    _current_context.set(None if previous is contextvars.Token.MISSING else previous)


def get_current_context() -> Context:
    context = _current_context.get()
    if context is None:
        raise RuntimeError("base_cli context is not active. Run inside a base_cli.App command.")
    return context
