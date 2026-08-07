from __future__ import annotations

import functools
import inspect
import io
import logging
import os
import stat
import sys
import time
import traceback
from collections.abc import Callable, Iterable
from contextlib import redirect_stdout
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, ParamSpec, TypeVar, cast

from ._click_compat import dialect_for_command
from ._lifecycle import (
    InvocationOutcome,
    RunRecorder,
    outcome_from_exception,
    outcome_from_exit_code,
    system_exit_code,
)
from ._private_files import write_private_json
from ._runtime import (
    RuntimeDirectoryError,
    create_owned_runtime_directory,
    create_runtime_directory,
    prune_log_files,
    prune_run_bundles,
)
from .attachment import AttachmentContract
from .config import ConfigSnapshot
from .context import Context, recover_current_context, reset_current_context, set_current_context
from .errors import ConfigurationError
from .exit_codes import ExitCode
from .history import compact_optional_path, utc_now
from .integrations import TelemetryOptions, TelemetrySession, finish_telemetry, start_telemetry
from .json_contracts import dumps_envelope, error_envelope, success_envelope
from .lifecycle_options import (
    LIFECYCLE_META_KEY,
    LifecycleOption,
    LifecycleOptions,
    LifecycleValues,
)
from .logging import configure_logger, log_invocation
from .paths import (
    current_working_dir,
    normalize_cli_name,
)
from .profile import CliProfile
from .redaction import (
    REDACTED,
    RedactionPlan,
    compile_redaction_plan,
    option_aliases_from_decls,
    parameter_name_from_decls,
    redact_argv,
)
from .runtime import RetentionPolicy

_STANDARD_OPTION_KEYS = ("debug", "quiet", "environment", "config", "keep_temp", "log_file", "json")
_FLAG_LIFECYCLE_OPTION_KEYS = frozenset({"debug", "quiet", "keep_temp", "dry_run", "json"})
_NATIVE_LIFECYCLE_OPTION_ORDER = (
    "quiet",
    "debug",
    "environment",
    "config",
    "keep_temp",
    "log_file",
    "dry_run",
    "json",
)
_ATTACHED_LIFECYCLE_OPTION_ORDER = (
    "log_file",
    "keep_temp",
    "config",
    "environment",
    "debug",
    "quiet",
    "dry_run",
    "json",
)
_LIFECYCLE_CAPTURE_META_KEY = object()
_LIFECYCLE_RESOLUTION_META_KEY = object()
DISPLAY_COMMAND_ENV = "BASE_CLI_DISPLAY_COMMAND"
_INVOCATION_ARGV: ContextVar[list[str] | None] = ContextVar("base_cli_invocation_argv", default=None)
_INVOCATION_MAIN_BYPASS: ContextVar[Any | None] = ContextVar(
    "base_cli_invocation_main_bypass",
    default=None,
)
_COMMAND_APP_ATTRIBUTE = "__base_cli_command_app__"
_COMMAND_APP_LOCK = RLock()
_CLICK_ATTACHMENT_ATTRIBUTE = "__base_cli_attachment__"
_CLICK_INSTRUMENTED_ATTRIBUTE = "__base_cli_lifecycle_instrumented__"
_CLICK_MAIN_INSTRUMENTED_ATTRIBUTE = "__base_cli_main_instrumented__"
_CLICK_ORIGINAL_INVOKE_ATTRIBUTE = "__base_cli_original_invoke__"
_CLICK_ORIGINAL_RESOLVE_ATTRIBUTE = "__base_cli_original_resolve__"
_CLICK_ORIGINAL_MAIN_ATTRIBUTE = "__base_cli_original_main__"
_CLICK_APP_OWNER_ATTRIBUTE = "__base_cli_app_owner__"
_CLICK_LIFECYCLE_BINDINGS_ATTRIBUTE = "__base_cli_lifecycle_bindings__"
_CLICK_INSTRUMENTED_SENTINEL = object()
_CLICK_MAIN_INSTRUMENTED_SENTINEL = object()
_CLICK_ATTACHMENT_LOCK = RLock()
_JSON_DEFAULT_MAX_LOG_FILES = 20
_REGISTRATION_OPEN = "open"
_REGISTRATION_MATERIALIZING = "materializing"
_REGISTRATION_FROZEN = "frozen"
_COMMAND_NAME_SUFFIXES = frozenset({"command", "cmd", "group", "grp"})
_P = ParamSpec("_P")
_R = TypeVar("_R")
_ClickCommandT = TypeVar("_ClickCommandT")
_ASYNC_CALLBACK_ERROR = (
    "Native async Click callbacks are not supported by base-cli. "
    "Use a synchronous callback or an adapter with an explicit async runner."
)


@dataclass
class _InvocationState:
    owner_app: Any = None
    run_id: str | None = None
    log_file: Path | None = None
    debug: bool = False
    quiet: bool = False
    debug_option: str | None = "--debug"
    options_parsed: bool = False
    attached_completion: bool = False
    json_output: bool = False


@dataclass(frozen=True)
class _SubcommandRegistration:
    func: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    name: str


@dataclass(frozen=True)
class _LifecycleBinding:
    key: str
    parameter_name: str
    adopted: bool


@dataclass(frozen=True)
class _RawLifecycleValue:
    value: Any
    source: Any
    depth: int


@dataclass(frozen=True)
class _LifecycleResolution:
    values: LifecycleValues
    raw: dict[str, _RawLifecycleValue]


_ClickAttachment = AttachmentContract


class _AttachedInvocation:
    """One attachment invocation whose schema is completed lazily."""

    def __init__(
        self,
        attachment: _ClickAttachment[Any],
        root_click_context: Any,
        context: Context[Any, Any, Any],
        recorder: RunRecorder,
    ) -> None:
        self.attachment = attachment
        self.root_click_context = root_click_context
        self.context = context
        self.recorder = recorder
        self.redaction_plan = RedactionPlan()
        self.invocation_argv: list[str] = []
        self.started = False
        self._resolved_children: dict[int, list[tuple[str, Any, Any]]] = {}
        self._resolution_parents: dict[int, Any] = {}
        self._has_chain = bool(getattr(attachment.command, "chain", False))
        self._selected_boundary_seen = False

    def note_resolution(
        self,
        parent_context: Any,
        command_name: str,
        child_command: Any,
    ) -> None:
        if getattr(getattr(parent_context, "command", None), "chain", False):
            self._has_chain = True
        self._resolution_parents[id(parent_context)] = parent_context
        self._resolved_children.setdefault(id(parent_context), []).append((command_name, child_command, None))

    def note_child_context(self, child_context: Any) -> None:
        parent = getattr(child_context, "parent", None)
        if parent is None:
            return
        resolutions = self._resolved_children.get(id(parent), [])
        for index in range(len(resolutions) - 1, -1, -1):
            name, command, recorded_context = resolutions[index]
            if recorded_context is None and command is getattr(child_context, "command", None):
                resolutions[index] = (name, command, child_context)
                break

    def start(
        self,
        selected_context: Any | None = None,
        *,
        force: bool = False,
    ) -> None:
        if self.started:
            return
        if selected_context is not None:
            self._selected_boundary_seen = True
        if self._has_chain and not force:
            # Click resolves all chain members before invoking the first one.
            # Wait until root teardown so every selected command can contribute
            # its sensitive option names to the conservative chain scan.
            return
        # Mark first so a schema failure cannot trigger a second logging
        # attempt during teardown and mask the original exception.
        self.started = True
        opaque_teardown = force and not self._selected_boundary_seen
        if opaque_teardown:
            self.redaction_plan = RedactionPlan()
        elif self._has_chain:
            self.redaction_plan = compile_redaction_plan(
                self.attachment.command,
                self.attachment.sensitive_parameters,
                selected_paths=_selected_click_paths(
                    self.root_click_context,
                    self._resolved_children,
                    self._resolution_parents,
                ),
            )
        else:
            selected_path = _selected_click_path(
                self.root_click_context,
                selected_context,
                self._resolved_children,
            )
            self.redaction_plan = compile_redaction_plan(
                self.attachment.command,
                self.attachment.sensitive_parameters,
                selected_path=selected_path,
            )
        raw_argv = _current_invocation_argv()
        self.invocation_argv = (
            [raw_argv[0], *([REDACTED] * (len(raw_argv) - 1))]
            if opaque_teardown and raw_argv
            else redact_argv(raw_argv, self.redaction_plan)
        )
        log_invocation(self.context.log, self.invocation_argv, None)


_INVOCATION_STATE: ContextVar[_InvocationState | None] = ContextVar("base_cli_invocation_state", default=None)
_ATTACHED_INVOCATION: ContextVar[_AttachedInvocation | None] = ContextVar(
    "base_cli_attached_invocation",
    default=None,
)


def _reset_context_var(variable: ContextVar[Any], token: Any) -> None:
    try:
        variable.reset(token)
    except BaseException:  # pylint: disable=broad-exception-caught
        try:
            previous = token.old_value
            variable.set(None if previous is Token.MISSING else previous)
        except BaseException:  # pylint: disable=broad-exception-caught
            pass


def _default_log_file(layout: Any, configured_log_file: Path | None) -> Path:
    return configured_log_file or layout.log_dir / "primary.log"


def _warn_lifecycle_failure(context: Context[Any, Any, Any], message: str, exc: BaseException) -> None:
    """Report a secondary lifecycle failure without breaking teardown."""
    try:
        detail = str(exc) or type(exc).__name__
        context.log.warning("%s: %s", message, detail)
    except BaseException:  # pylint: disable=broad-exception-caught
        pass


def _capture_invocation_context(context: Context[Any, Any, Any], owner_app: App) -> None:
    state = _INVOCATION_STATE.get()
    if state is None or state.owner_app is not owner_app:
        return
    state.run_id = context.run_id
    state.log_file = context.log_file
    state.debug = context.debug
    state.quiet = context.quiet


def _capture_standard_options(standard: dict[str, Any], owner_app: App) -> None:
    state = _INVOCATION_STATE.get()
    if state is None or state.owner_app is not owner_app:
        return
    state.debug = bool(standard.get("debug"))
    state.quiet = bool(standard.get("quiet"))
    state.json_output = bool(standard.get("json"))
    state.options_parsed = True


def _capture_effective_output_options(
    *,
    owner_app: App,
    debug: bool,
    quiet: bool,
    json_output: bool = False,
) -> None:
    state = _INVOCATION_STATE.get()
    if state is None or state.owner_app is not owner_app:
        return
    state.debug = debug
    state.quiet = quiet
    state.json_output = json_output


def _record_unexpected_traceback(context: Context[Any, Any, Any], outcome: InvocationOutcome) -> None:
    if outcome.kind != "unexpected_error":
        return
    try:
        context.log.debug("Unexpected command exception", exc_info=True)
    except BaseException:  # pylint: disable=broad-exception-caught
        pass


def _start_run_recorder(recorder: RunRecorder) -> None:
    try:
        recorder.start()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _warn_lifecycle_failure(recorder.context, "Run metadata start failed", exc)


def _finish_run_recorder(
    recorder: RunRecorder,
    outcome: InvocationOutcome,
    *,
    ended_at: datetime,
    ended_monotonic_ns: int,
) -> None:
    try:
        recorder.finish(
            outcome,
            ended_at=ended_at,
            ended_monotonic_ns=ended_monotonic_ns,
        )
    except BaseException as exc:  # pylint: disable=broad-exception-caught
        path = recorder.context._run_metadata_path
        _warn_lifecycle_failure(
            recorder.context,
            f"Run metadata finalization failed for '{path}'",
            exc,
        )
        _discard_owned_run_record(recorder)


def _discard_owned_run_record(recorder: RunRecorder) -> None:
    try:
        recorder.discard_owned_record()
    except BaseException as exc:  # pylint: disable=broad-exception-caught
        _warn_lifecycle_failure(
            recorder.context,
            f"Run metadata recovery failed for '{recorder.context._run_metadata_path}'",
            exc,
        )


def _reset_active_context(context: Context[Any, Any, Any], token: Any) -> None:
    try:
        reset_current_context(token)
    except BaseException as exc:  # pylint: disable=broad-exception-caught
        _warn_lifecycle_failure(context, "Active context reset failed", exc)
        try:
            recover_current_context(token)
        except BaseException:  # pylint: disable=broad-exception-caught
            pass


def _require_click() -> Any:
    try:
        import click
    except ImportError as exc:
        raise RuntimeError("Click is required for base_cli. Install it with 'pip install click'.") from exc
    return click


def _explicit_command_name(
    command_args: tuple[Any, ...],
    command_kwargs: dict[str, Any],
) -> str | None:
    if command_args and "name" in command_kwargs:
        raise TypeError("Command name cannot be provided both positionally and by keyword.")
    name = command_args[0] if command_args else command_kwargs.get("name")
    if name is None:
        return None
    if not isinstance(name, str):
        raise TypeError("Command name must be a string or None.")
    return name


def _inferred_command_name(func: Callable[..., Any]) -> str:
    name = func.__name__.lower().replace("_", "-")
    prefix, separator, suffix = name.rpartition("-")
    if separator and suffix in _COMMAND_NAME_SUFFIXES:
        return prefix
    return name


def _resolved_command_name(
    func: Callable[..., Any],
    command_args: tuple[Any, ...],
    command_kwargs: dict[str, Any],
) -> str:
    return _explicit_command_name(command_args, command_kwargs) or _inferred_command_name(func)


def _click_command_decorator(
    click: Any,
    name: str,
    command_args: tuple[Any, ...],
    command_kwargs: dict[str, Any],
) -> Callable[[Callable[..., Any]], Any]:
    # ``name`` is resolved by base-cli so naming and duplicate behavior do not
    # drift across supported Click versions. Preserve the optional positional
    # command class and all non-name attributes.
    args_after_name = command_args[1:] if command_args else ()
    attrs = dict(command_kwargs)
    attrs.pop("name", None)
    return cast(Callable[[Callable[..., Any]], Any], click.command(name, *args_after_name, **attrs))


def _require_materialized_command_name(
    command: Any,
    expected_name: str,
    app_name: str,
) -> None:
    actual_name = getattr(command, "name", None)
    if actual_name != expected_name:
        raise RuntimeError(
            f"App '{app_name}' expected Click command name '{expected_name}', "
            f"but the configured command class produced {actual_name!r}."
        )


# pylint: disable=too-many-statements
class App:
    """Define a Click-backed command with a shared runtime lifecycle."""

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(
        self,
        name: str | None = None,
        version: str | None = None,
        help: str | None = None,  # pylint: disable=redefined-builtin
        log_to_file: bool = True,
        max_log_files: int | None = None,
        profile: CliProfile | None = None,
        lifecycle_options: LifecycleOptions | None = None,
        retention: RetentionPolicy | None = None,
        max_run_bundles: int | None = None,
        max_run_age_seconds: float | None = None,
        max_run_total_bytes: int | None = None,
        rich: bool = False,
        telemetry: TelemetryOptions | None = None,
    ) -> None:
        if max_log_files is not None and max_log_files < 1:
            raise ValueError("max_log_files must be greater than 0 when set.")
        if retention is not None and not isinstance(retention, RetentionPolicy):
            raise TypeError("retention must be a RetentionPolicy instance or None.")
        if not isinstance(rich, bool):
            raise TypeError("rich must be a bool.")
        if telemetry is not None and not isinstance(telemetry, TelemetryOptions):
            raise TypeError("telemetry must be a TelemetryOptions instance or None.")
        if retention is not None and any(
            value is not None for value in (max_run_bundles, max_run_age_seconds, max_run_total_bytes)
        ):
            raise ValueError("pass either retention or individual run retention bounds, not both.")
        if retention is not None:
            self.retention: RetentionPolicy | None = retention
        elif any(value is not None for value in (max_run_bundles, max_run_age_seconds, max_run_total_bytes)):
            self.retention = RetentionPolicy(
                max_bundles=max_run_bundles,
                max_age_seconds=max_run_age_seconds,
                max_total_bytes=max_run_total_bytes,
            )
        elif max_log_files is None:
            self.retention = RetentionPolicy.safe_defaults()
        else:
            # Keep the original per-file option's behavior for explicitly
            # opted-in legacy consumers; modern bundles are still handled by
            # the compatibility path below.
            self.retention = None
        self._registration_lock = RLock()
        self._registration_state = _REGISTRATION_OPEN
        self._name = normalize_cli_name(name or sys.argv[0])
        self.version = version
        self.help = help
        self.log_to_file = log_to_file
        self.max_log_files = max_log_files
        self.rich = rich
        self.telemetry = telemetry
        # Standalone applications must not inherit a consumer's product
        # conventions. Consumers with product-specific policies should pass an
        # explicit profile.
        self.profile = profile or CliProfile.generic()
        if lifecycle_options is not None and not isinstance(
            lifecycle_options,
            LifecycleOptions,
        ):
            raise TypeError("lifecycle_options must be a LifecycleOptions instance or None.")
        self._lifecycle_options = lifecycle_options or LifecycleOptions()
        self._click_command = None
        self._redaction_plan: RedactionPlan | None = None
        self._command_func: Callable[..., Any] | None = None
        self._command_args: tuple[Any, ...] = ()
        self._command_kwargs: dict[str, Any] = {}
        self._subcommands: list[_SubcommandRegistration] = []
        self._subcommand_names: set[str] = set()
        self._attached_command: Any | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def lifecycle_options(self) -> LifecycleOptions:
        return self._lifecycle_options

    @lifecycle_options.setter
    def lifecycle_options(self, value: LifecycleOptions) -> None:
        if not isinstance(value, LifecycleOptions):
            raise TypeError("lifecycle_options must be a LifecycleOptions instance.")
        with self._registration_lock:
            self._ensure_registration_open()
            self._lifecycle_options = value

    def _set_name(self, value: str) -> None:
        normalized = normalize_cli_name(value)
        with self._registration_lock:
            self._ensure_registration_open()
            explicit_name = _explicit_command_name(
                self._command_args,
                self._command_kwargs,
            )
            if (
                self._command_func is not None
                and explicit_name is not None
                and normalize_cli_name(explicit_name) != normalized
            ):
                raise RuntimeError(
                    f"App '{self.name}' cannot be renamed to '{normalized}' because "
                    f"its registered command explicitly uses '{explicit_name}'."
                )
            self._name = normalized

    name = name.setter(_set_name)  # type: ignore[attr-defined]

    def _ensure_registration_open(self) -> None:
        if self._registration_state == _REGISTRATION_MATERIALIZING:
            raise RuntimeError(
                f"App '{self.name}' registration is unavailable while its Click command is being materialized."
            )
        if self._registration_state == _REGISTRATION_FROZEN:
            raise RuntimeError(
                f"App '{self.name}' registration is frozen because its Click command has already been materialized."
            )

    def _validate_single_command_name(
        self,
        command_args: tuple[Any, ...],
        command_kwargs: dict[str, Any],
    ) -> None:
        explicit_name = _explicit_command_name(command_args, command_kwargs)
        if explicit_name is not None and normalize_cli_name(explicit_name) != self.name:
            raise RuntimeError(
                f"App '{self.name}' is the authoritative command name; "
                f"the registered command cannot use '{explicit_name}'."
            )

    def command(
        self,
        *command_args: Any,
        **command_kwargs: Any,
    ) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
        with self._registration_lock:
            self._ensure_registration_open()
            self._validate_single_command_name(command_args, command_kwargs)

        def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
            _reject_async_callback(func)
            with self._registration_lock:
                self._ensure_registration_open()
                self._validate_single_command_name(command_args, command_kwargs)
                if self._subcommands:
                    raise RuntimeError(
                        f"App '{self.name}' already has registered subcommands. "
                        "Use @app.subcommand() for additional entry points."
                    )
                if self._command_func is not None:
                    raise RuntimeError(
                        f"App '{self.name}' already has a registered command. "
                        "Use subcommands for multiple entry points."
                    )
                self._command_func = func
                self._command_args = tuple(command_args)
                self._command_kwargs = dict(command_kwargs)
            return func

        return decorator

    def subcommand(
        self,
        *command_args: Any,
        **command_kwargs: Any,
    ) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
        with self._registration_lock:
            self._ensure_registration_open()
            _explicit_command_name(command_args, command_kwargs)

        def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
            _reject_async_callback(func)
            with self._registration_lock:
                self._ensure_registration_open()
                if self._command_func is not None:
                    raise RuntimeError(
                        f"App '{self.name}' already has a registered command. "
                        "Use either @app.command() or @app.subcommand(), not both."
                    )
                name = _resolved_command_name(func, command_args, command_kwargs)
                if name in self._subcommand_names:
                    raise RuntimeError(f"App '{self.name}' already has a registered subcommand named '{name}'.")
                self._subcommands.append(
                    _SubcommandRegistration(
                        func=func,
                        args=tuple(command_args),
                        kwargs=dict(command_kwargs),
                        name=name,
                    )
                )
                self._subcommand_names.add(name)
            return func

        return decorator

    def attach(
        self,
        command: _ClickCommandT,
        *,
        context_factory: Callable[[Context[Any, Any, Any]], Any] | None = None,
        service_factory: Callable[[Context[Any, Any, Any]], Any] | None = None,
        sensitive_parameters: Iterable[str] = (),
    ) -> _ClickCommandT:
        """Attach this app's lifecycle to an existing Click command tree.

        The same command object is returned rather than copied. Click continues
        to own callbacks, contexts, aliases, and lazy command resolution while
        base-cli extends its root parameters and adds one lifecycle boundary.
        """

        click = dialect_for_command(command)
        if not isinstance(command, click.Command):
            raise TypeError("App.attach() requires a click.Command instance.")
        _reject_async_callback(getattr(command, "callback", None))
        if context_factory is not None and not callable(context_factory):
            raise TypeError("context_factory must be callable or None.")
        if service_factory is not None and not callable(service_factory):
            raise TypeError("service_factory must be callable or None.")
        normalized_sensitive_parameters = _normalize_sensitive_parameters(sensitive_parameters)

        with _CLICK_ATTACHMENT_LOCK, self._registration_lock:
            existing = getattr(command, _CLICK_ATTACHMENT_ATTRIBUTE, None)
            if isinstance(existing, _ClickAttachment):
                if (
                    existing.app is self
                    and existing.command is command
                    and existing.context_factory is context_factory
                    and existing.service_factory is service_factory
                    and existing.sensitive_parameters == normalized_sensitive_parameters
                    and existing.lifecycle_options == self.lifecycle_options
                    and self._attached_command is command
                    and self._click_command is command
                    and self._registration_state == _REGISTRATION_FROZEN
                ):
                    return command
                raise RuntimeError(
                    f"Click command '{getattr(command, 'name', None) or '<unnamed>'}' "
                    "is already attached to a base_cli.App."
                )
            native_owner = getattr(command, _CLICK_APP_OWNER_ATTRIBUTE, None)
            if isinstance(native_owner, App):
                raise RuntimeError(
                    f"Click command '{getattr(command, 'name', None) or '<unnamed>'}' "
                    "already belongs to a native base_cli.App and cannot be attached."
                )
            self._ensure_registration_open()
            if self._command_func is not None or self._subcommands:
                raise RuntimeError(
                    f"App '{self.name}' already has registered commands and cannot attach an existing Click tree."
                )
            if self._attached_command is not None:
                raise RuntimeError(f"App '{self.name}' is already attached to a Click command.")
            command_name = getattr(command, "name", None)
            if not isinstance(command_name, str) or not command_name:
                raise RuntimeError("App.attach() requires a named Click command.")
            if command_name != self.name:
                raise RuntimeError(
                    f"App '{self.name}' is the authoritative command name; "
                    f"the attached Click command cannot use '{command_name}'."
                )

            added_parameters: list[Any] = []
            missing_marker = object()
            previous_marker = getattr(
                command,
                _CLICK_ATTACHMENT_ATTRIBUTE,
                missing_marker,
            )
            if previous_marker is not missing_marker and not isinstance(
                previous_marker,
                _ClickAttachment,
            ):
                raise RuntimeError(
                    f"Click command '{command_name}' uses base-cli's reserved "
                    "attachment marker. Remove that attribute before attaching."
                )
            for marker_name, sentinel, description in (
                (
                    _CLICK_INSTRUMENTED_ATTRIBUTE,
                    _CLICK_INSTRUMENTED_SENTINEL,
                    "command instrumentation",
                ),
                (
                    _CLICK_MAIN_INSTRUMENTED_ATTRIBUTE,
                    _CLICK_MAIN_INSTRUMENTED_SENTINEL,
                    "main instrumentation",
                ),
            ):
                marker = getattr(command, marker_name, missing_marker)
                if marker is not missing_marker and marker is not sentinel:
                    raise RuntimeError(
                        f"Click command '{command_name}' uses base-cli's reserved "
                        f"{description} marker. Remove that attribute before attaching."
                    )
            command_was_instrumented = (
                getattr(command, _CLICK_INSTRUMENTED_ATTRIBUTE, None) is _CLICK_INSTRUMENTED_SENTINEL
            )
            main_was_instrumented = (
                getattr(command, _CLICK_MAIN_INSTRUMENTED_ATTRIBUTE, None) is _CLICK_MAIN_INSTRUMENTED_SENTINEL
            )
            previous_redaction_plan = self._redaction_plan
            previous_attached_command = self._attached_command
            previous_click_command = self._click_command
            previous_registration_state = self._registration_state
            try:
                self._registration_state = _REGISTRATION_MATERIALIZING
                standard_bindings = _add_attached_standard_options(
                    click,
                    command,
                    lifecycle_options=self.lifecycle_options,
                    version=self.version,
                    added_parameters=added_parameters,
                )
                redaction_plan = compile_redaction_plan(
                    command,
                    normalized_sensitive_parameters,
                    selected_path=(),
                )
                attachment = _ClickAttachment(
                    app=self,
                    command=command,
                    context_factory=context_factory,
                    service_factory=service_factory,
                    sensitive_parameters=normalized_sensitive_parameters,
                    lifecycle_options=self.lifecycle_options,
                    standard_bindings=standard_bindings,
                )
                _instrument_attached_click_command(click, command)
                _instrument_attached_click_main(command)
                self._redaction_plan = redaction_plan
                self._attached_command = command
                self._click_command = command
                self._registration_state = _REGISTRATION_FROZEN
                # Publish ownership last. Invoke wrappers synchronize on this
                # lock, so neither the marker nor partial App state can become
                # observable before every attachment invariant is established.
                setattr(command, _CLICK_ATTACHMENT_ATTRIBUTE, attachment)
            except BaseException:
                if previous_marker is missing_marker:
                    try:
                        delattr(command, _CLICK_ATTACHMENT_ATTRIBUTE)
                    except (AttributeError, TypeError):
                        pass
                else:
                    try:
                        setattr(command, _CLICK_ATTACHMENT_ATTRIBUTE, previous_marker)
                    except (AttributeError, TypeError):
                        pass
                if not main_was_instrumented:
                    _restore_attached_click_main(command)
                if not command_was_instrumented:
                    _restore_attached_click_command(command)
                for parameter in added_parameters:
                    try:
                        command.params.remove(parameter)
                    except (AttributeError, ValueError):
                        pass
                object.__setattr__(self, "_redaction_plan", previous_redaction_plan)
                object.__setattr__(self, "_attached_command", previous_attached_command)
                object.__setattr__(self, "_click_command", previous_click_command)
                object.__setattr__(
                    self,
                    "_registration_state",
                    previous_registration_state,
                )
                raise
            return command

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if len(args) < 2 and "prog_name" not in kwargs:
            kwargs["prog_name"] = self.profile.display_command() or self.name
        return self.click_command(*args, **kwargs)

    @property
    def click_command(self) -> Any:
        with self._registration_lock:
            command = self._click_command
            if command is not None:
                return command
            if self._registration_state == _REGISTRATION_MATERIALIZING:
                raise RuntimeError(f"App '{self.name}' Click command materialization is already in progress.")

            self._registration_state = _REGISTRATION_MATERIALIZING
            try:
                command = self._build_click_command()
                redaction_plan = compile_redaction_plan(command)
            except BaseException:
                # A missing dependency, invalid custom Click class, or plan
                # compilation failure must not strand an otherwise repairable
                # application in a half-materialized state.
                self._registration_state = _REGISTRATION_OPEN
                raise
            else:
                # Publish the command last so another thread can never invoke
                # its wrapper before the corresponding plan is available.
                self._redaction_plan = redaction_plan
                self._registration_state = _REGISTRATION_FROZEN
                self._click_command = command
            return command

    def _build_click_command(self) -> Any:
        if self._command_func is None and not self._subcommands:
            raise RuntimeError("No command has been registered on this base_cli.App.")

        click = _require_click()
        if self._command_func is not None:
            wrapper = self._build_command_wrapper(click, self._command_func)
            command_kwargs = dict(self._command_kwargs)
            if self.help is not None:
                command_kwargs.setdefault("help", self.help)
            command = _click_command_decorator(
                click,
                self.name,
                self._command_args,
                command_kwargs,
            )(wrapper)
            _require_materialized_command_name(command, self.name, self.name)
            _install_native_lifecycle_options(
                click,
                command,
                self.lifecycle_options,
                version=self.version,
            )
            setattr(command, _CLICK_APP_OWNER_ATTRIBUTE, self)
            return command

        group_wrapper = _build_group_wrapper(click)
        group = click.group(name=self.name, help=self.help)(group_wrapper)
        _install_native_lifecycle_options(
            click,
            group,
            self.lifecycle_options,
            version=self.version,
        )
        setattr(group, _CLICK_APP_OWNER_ATTRIBUTE, self)
        for registration in self._subcommands:
            wrapper = self._build_command_wrapper(click, registration.func)
            command = _click_command_decorator(
                click,
                registration.name,
                registration.args,
                registration.kwargs,
            )(wrapper)
            _require_materialized_command_name(command, registration.name, self.name)
            _install_native_lifecycle_options(
                click,
                command,
                self.lifecycle_options,
                version=None,
            )
            setattr(command, _CLICK_APP_OWNER_ATTRIBUTE, self)
            # Supplying the canonical name explicitly also prevents a custom
            # Command implementation from changing the group key between the
            # validation above and Click's registration step.
            group.add_command(
                command,
                name=registration.name,
            )
        return group

    def _build_command_wrapper(
        self,
        click: Any,
        func: Callable[..., Any],
    ) -> Callable[..., Any]:
        explicit_dry_run_parameter = getattr(
            func,
            "__base_cli_dry_run_parameter__",
            None,
        )
        conventional_dry_run_parameter = any(
            parameter_name_from_decls(param_decls) == "dry_run"
            for _kind, param_decls, _attrs, *_metadata in getattr(
                func,
                "__base_cli_param_specs__",
                (),
            )
        )
        if self.lifecycle_options.dry_run is not None and (
            explicit_dry_run_parameter is not None or conventional_dry_run_parameter
        ):
            conflicting_parameter = explicit_dry_run_parameter or "dry_run"
            raise RuntimeError(
                f"{func.__name__} designates '{conflicting_parameter}' as dry-run, "
                "but LifecycleOptions.dry_run is also enabled. Use only one dry-run source."
            )
        dry_run_parameter = explicit_dry_run_parameter or "dry_run"

        @functools.wraps(func)
        def wrapper(**kwargs: Any) -> Any:
            if _ATTACHED_INVOCATION.get() is not None:
                raise RuntimeError(
                    f"base_cli command '{self.name}' cannot run inside an attached "
                    "Click tree because that would create a second lifecycle."
                )
            click_context = click.get_current_context()
            bindings = getattr(
                click_context.command,
                _CLICK_LIFECYCLE_BINDINGS_ATTRIBUTE,
                {},
            )
            extra_values: dict[str, _RawLifecycleValue] = {}
            if self.lifecycle_options.dry_run is None and dry_run_parameter in kwargs:
                extra_values["dry_run"] = _RawLifecycleValue(
                    value=kwargs.get(dry_run_parameter),
                    source=click_context.get_parameter_source(dry_run_parameter),
                    depth=_context_depth(click_context),
                )
            resolution = _resolve_lifecycle_values(
                click,
                click_context,
                bindings,
                extra_values=extra_values,
            )
            standard = _standard_options_from_values(resolution.values)
            _validate_standard_options(click, standard, self.lifecycle_options)
            _capture_standard_options(standard, self)
            started_at = utc_now()
            started_monotonic_ns = time.monotonic_ns()
            context: Context[Any, Any, Any] | None = None
            recorder: RunRecorder | None = None
            telemetry_session: TelemetrySession | None = None
            outcome = outcome_from_exit_code(ExitCode.SUCCESS)
            invocation_argv: list[str] = []
            redaction_plan = self._redaction_plan
            if redaction_plan is None:
                raise RuntimeError("Command redaction plan was not initialized.")
            token = None
            try:
                try:
                    context = self._create_context(
                        standard,
                        dry_run=resolution.values.dry_run,
                    )
                except ConfigurationError as exc:
                    raise click.UsageError(str(exc)) from exc
                except RuntimeDirectoryError as exc:
                    raise click.ClickException(str(exc)) from exc

                recorder = RunRecorder(context, started_at, started_monotonic_ns)
                token = set_current_context(context)
                _capture_invocation_context(context, self)
                invocation_argv = redact_argv(_current_invocation_argv(), redaction_plan)
                _start_run_recorder(recorder)
                telemetry_session = start_telemetry(self.telemetry, context)
                log_invocation(context.log, invocation_argv, None)
                if context.project_root is not None:
                    context.log.debug("project_root=%s", context.project_root)
                if context.manifest_path is not None:
                    context.log.debug("manifest_path=%s", context.manifest_path)
                result = _reject_async_result(func(context, **kwargs))
                try:
                    exit_code = _normalize_command_result(result)
                except TypeError as exc:
                    raise click.ClickException(str(exc)) from exc
                outcome = outcome_from_exit_code(exit_code)
                return result
            except BaseException as exc:
                if context is not None:
                    outcome = outcome_from_exception(click, exc)
                    _record_unexpected_traceback(context, outcome)
                raise
            finally:
                if context is not None:
                    try:
                        ended_at = utc_now()
                        ended_monotonic_ns = time.monotonic_ns()
                    except BaseException as exc:  # pylint: disable=broad-exception-caught
                        ended_at = started_at
                        ended_monotonic_ns = started_monotonic_ns
                        _warn_lifecycle_failure(context, "Terminal clock capture failed", exc)

                    try:
                        if self.profile.history_writer is not None:
                            self.profile.history_writer(
                                context,
                                invocation_argv,
                                set(redaction_plan),
                                started_at,
                                outcome.exit_code,
                            )
                    except BaseException as exc:  # pylint: disable=broad-exception-caught
                        _warn_lifecycle_failure(context, "History finalization failed", exc)

                    if recorder is None:
                        try:
                            recorder = RunRecorder(context, started_at, started_monotonic_ns)
                        except BaseException as exc:  # pylint: disable=broad-exception-caught
                            _warn_lifecycle_failure(context, "Run recorder construction failed", exc)
                    if recorder is not None:
                        finish_telemetry(
                            telemetry_session,
                            context,
                            outcome,
                            ended_monotonic_ns=ended_monotonic_ns,
                        )
                        _finish_run_recorder(
                            recorder,
                            outcome,
                            ended_at=ended_at,
                            ended_monotonic_ns=ended_monotonic_ns,
                        )

                    try:
                        context.cleanup()
                    except BaseException as exc:  # pylint: disable=broad-exception-caught
                        _warn_lifecycle_failure(context, "Lifecycle cleanup failed", exc)
                    finally:
                        if token is not None:
                            _reset_active_context(context, token)

        for spec in getattr(func, "__base_cli_param_specs__", []):
            kind, param_decls, attrs, *metadata = spec
            sensitive = bool(metadata[0]) if metadata else False
            if kind == "option":
                wrapper = click.option(*param_decls, **attrs)(wrapper)
            elif kind == "argument":
                wrapper = click.argument(*param_decls, **attrs)(wrapper)
            if sensitive:
                click_parameters = getattr(wrapper, "__click_params__", ())
                if click_parameters:
                    click_parameters[-1]._base_cli_sensitive = True
        return wrapper

    def _create_context(
        self,
        standard: dict[str, Any],
        dry_run: bool = False,
    ) -> Context[dict[str, Any], Any, Any]:
        project = self.profile.discover_project(current_working_dir())
        manifest_path = project.manifest if project is not None else None
        explicit_config = Path(standard["config"]).expanduser() if standard.get("config") else None
        user_config = self.profile.load_user_config()
        workspace_root = self.profile.resolve_workspace_root(user_config)
        requested_environment = standard.get("environment")
        if self.profile.load_config_for_environment is not None and requested_environment is not None:
            loaded_config = self.profile.load_config_for_environment(
                project,
                explicit_config,
                str(requested_environment),
            )
        else:
            loaded_config = self.profile.load_config(project, explicit_config)

        if isinstance(loaded_config, ConfigSnapshot):
            config = loaded_config.config
            framework_config = loaded_config.framework
            config_provenance = loaded_config.provenance
        else:
            config = loaded_config
            framework_config = None
            config_provenance = {}

        environment = (
            standard.get("environment")
            or (framework_config.environment if framework_config is not None else None)
            or config.get("environment")
            or "dev"
        )
        log_level = (
            framework_config.log_level if framework_config is not None else str(config.get("log_level", "")).lower()
        )
        debug = bool(standard.get("debug") or log_level == "debug")
        quiet = bool(standard.get("quiet"))
        keep_temp = bool(
            standard.get("keep_temp")
            or (framework_config.keep_temp if framework_config is not None else None)
            or config.get("keep_temp")
        )
        _capture_effective_output_options(
            owner_app=self,
            debug=debug,
            quiet=quiet,
            json_output=bool(standard.get("json")),
        )

        runtime = self.profile.resolve_runtime(self.name, project)
        cache_root = runtime.cache_root
        runtime_owner = runtime.runtime_owner
        selected_project_root = runtime.project_root
        selected_project_name = runtime.project_name
        inherited_path = runtime.inherited_path
        run_id = runtime.run_id
        layout = runtime.layout

        log_file = Path(standard["log_file"]).expanduser() if standard.get("log_file") else None
        uses_default_log_file = log_file is None
        if not dry_run and self.log_to_file and log_file is None:
            log_file = _default_log_file(layout, runtime.primary_log_file)

        owns_run_metadata = inherited_path is None and not dry_run and self.log_to_file
        run_metadata_path = layout.run_root / "run.json" if owns_run_metadata else None
        temp_dir_was_new = not layout.temp_dir.exists()
        logger = logging.getLogger(f"base_cli.{self.name}")
        context: Context[dict[str, Any], Any, Any] = Context(
            cli_name=self.name,
            run_id=run_id,
            runtime_owner=runtime_owner,
            owner_root=layout.owner_root,
            run_root=layout.run_root,
            application_home=runtime.application_home,
            project_root=selected_project_root,
            workspace_root=workspace_root,
            manifest_path=manifest_path,
            project_name=selected_project_name,
            state_dir=layout.state_dir,
            log_dir=layout.log_dir,
            cache_dir=layout.cache_dir,
            temp_dir=layout.temp_dir,
            log_file=log_file,
            config=config,
            framework_config=framework_config,
            config_provenance=config_provenance,
            environment=environment,
            debug=debug,
            quiet=quiet,
            keep_temp=keep_temp,
            log=logger,
            user_config=user_config,
            history_display_command=self.profile.history_display_command,
            dry_run=dry_run,
            history_scope=runtime.history_scope,
            history_parent_run_id=runtime.history_parent_run_id,
            json_output=bool(standard.get("json")),
            rich=self.rich,
        )
        context._run_metadata_path = run_metadata_path

        logger_activation_started = False
        try:
            if owns_run_metadata:
                create_runtime_directory(layout.run_root, cache_root)
            if dry_run or not self.log_to_file:
                if log_file is not None:
                    create_runtime_directory(log_file.parent, cache_root)
            else:
                for directory in (layout.log_dir, layout.cache_dir):
                    create_runtime_directory(directory, cache_root)
                if temp_dir_was_new:
                    owned_identity, owned_descriptor = create_owned_runtime_directory(layout.temp_dir, cache_root)
                    context._owned_temp_descriptor = owned_descriptor
                    context._owned_temp_identity = owned_identity
                    context._owns_temp_dir = True
                else:
                    create_runtime_directory(layout.temp_dir, cache_root)
                if log_file is not None:
                    create_runtime_directory(log_file.parent, cache_root)

            logger_activation_started = True
            try:
                context.log = configure_logger(
                    self.name,
                    log_file,
                    debug,
                    quiet=quiet,
                    json_logs=context.json_output,
                    run_id=context.run_id,
                )
            except OSError as exc:
                target = f"persistent log file '{log_file}'" if log_file is not None else "stderr logging"
                raise RuntimeDirectoryError(f"Unable to configure {target}: {exc}") from exc
            context.log.debug("cli=%s run_id=%s environment=%s", self.name, run_id, environment)
            if uses_default_log_file and log_file is not None:
                if self.retention is not None:
                    prune_run_bundles(
                        layout.owner_root / "runs",
                        layout.run_root,
                        policy=self.retention,
                        logger=context.log,
                    )
                elif self.max_log_files is not None:
                    # Compatibility for the original public option.  The
                    # legacy pass handles pre-metadata flat log directories;
                    # metadata-backed runs are routed to bundle retention by
                    # the helper itself.
                    prune_log_files(
                        layout.owner_root / "runs",
                        log_file,
                        self.max_log_files,
                        context.log,
                    )
                    prune_run_bundles(
                        layout.owner_root / "runs",
                        layout.run_root,
                        policy=RetentionPolicy(max_bundles=self.max_log_files),
                        logger=context.log,
                    )
                elif context.json_output:
                    prune_run_bundles(
                        layout.owner_root / "runs",
                        layout.run_root,
                        policy=RetentionPolicy(max_bundles=_JSON_DEFAULT_MAX_LOG_FILES),
                        logger=context.log,
                    )

            if runtime.write_identity and selected_project_root is not None and not dry_run and self.log_to_file:
                try:
                    create_runtime_directory(layout.owner_root, cache_root)
                    identity_path = layout.owner_root / "identity.json"
                    if not identity_path.exists():
                        write_private_json(
                            identity_path,
                            {
                                "schema_version": 1,
                                "project": selected_project_name,
                                "project_root": compact_optional_path(selected_project_root),
                                "manifest": compact_optional_path(manifest_path),
                                "checkout_id": layout.owner_root.name,
                            },
                        )
                except OSError:
                    pass
            return context
        except BaseException:
            _rollback_context_creation(
                context,
                logger_activation_started=logger_activation_started,
            )
            raise


def _rollback_context_creation(
    context: Context[Any, Any, Any],
    *,
    logger_activation_started: bool,
) -> None:
    if logger_activation_started:
        keep_temp = context.keep_temp
        context.keep_temp = True
        try:
            try:
                context._cleanup_preserving_temp_ownership()
            except BaseException:  # pylint: disable=broad-exception-caught
                pass
        finally:
            context.keep_temp = keep_temp

    try:
        context._cleanup_owned_temp_dir()
    except BaseException:  # pylint: disable=broad-exception-caught
        pass


class _AttachedLifecycleResource:
    """Lifecycle resource retained by Click's root Context exit stack."""

    def __init__(
        self,
        click: Any,
        attachment: _ClickAttachment[Any],
        click_context: Any,
        lifecycle_values: LifecycleValues,
    ) -> None:
        self.click = click
        self.attachment = attachment
        self.click_context = click_context
        self.lifecycle_values = lifecycle_values
        self.standard = _standard_options_from_values(lifecycle_values)
        self.started_at = utc_now()
        self.started_monotonic_ns = time.monotonic_ns()
        self.context: Context[Any, Any, Any] | None = None
        self.invocation: _AttachedInvocation | None = None
        self.telemetry_session: TelemetrySession | None = None
        self.context_token: Any = None
        self.invocation_token: Any = None
        self.original_click_exit: Callable[..., Any] | None = None
        self.click_exit_wrapper: Callable[..., Any] | None = None
        self.outcome = outcome_from_exit_code(ExitCode.SUCCESS)
        self._closed = False

    def __enter__(self) -> _AttachedLifecycleResource:
        try:
            try:
                context = self.attachment.app._create_context(  # pylint: disable=protected-access
                    self.standard,
                    dry_run=self.lifecycle_values.dry_run,
                )
            except ConfigurationError as exc:
                raise self.click.UsageError(str(exc)) from exc
            except RuntimeDirectoryError as exc:
                raise self.click.ClickException(str(exc)) from exc

            self.context = context
            self.context_token = set_current_context(context)
            _capture_invocation_context(context, self.attachment.app)
            recorder = RunRecorder(context, self.started_at, self.started_monotonic_ns)
            self.invocation = _AttachedInvocation(
                self.attachment,
                self.click_context,
                context,
                recorder,
            )
            self.invocation_token = _ATTACHED_INVOCATION.set(self.invocation)
            _start_run_recorder(recorder)
            self.telemetry_session = start_telemetry(self.attachment.app.telemetry, context)

            original_click_exit = self.click_context.exit

            @functools.wraps(original_click_exit)
            def lifecycle_aware_exit(code: int = 0) -> Any:
                # Context.exit() closes the Context before it raises. When it
                # is called from a close hook, that recursive close can unwind
                # this resource with no exception information, so capture the
                # terminal outcome before delegating.
                self.record_exception(self.click.exceptions.Exit(code))
                return original_click_exit(code)

            self.original_click_exit = original_click_exit
            self.click_exit_wrapper = lifecycle_aware_exit
            self.click_context.exit = lifecycle_aware_exit
            return self
        except BaseException as exc:
            if self.context is not None:
                self.outcome = outcome_from_exception(self.click, exc)
                _record_unexpected_traceback(self.context, self.outcome)
                self._finalize()
            raise

    def initialize_factories(self) -> None:
        """Run extension factories after Click retains this resource.

        A factory may use Click's ``with_resource`` or ``call_on_close`` APIs.
        Running it only after our own ``__exit__`` is on Click's stack keeps
        those resources inside the base-cli lifecycle boundary.
        """

        context = self.context
        if context is None:
            raise RuntimeError("The attached lifecycle has not been entered.")
        try:
            if self.attachment.context_factory is not None:
                context.application_context = self.attachment.context_factory(context)
            if self.attachment.service_factory is not None:
                context.services = self.attachment.service_factory(context)
        except ConfigurationError as exc:
            error = self.click.UsageError(str(exc))
            self.record_exception(error)
            raise error from exc
        except RuntimeDirectoryError as exc:
            error = self.click.ClickException(str(exc))
            self.record_exception(error)
            raise error from exc
        except BaseException as exc:
            self.record_exception(exc)
            raise

    def record_result(self, _result: Any) -> None:
        # Arbitrary Click return values are application data, not process exit
        # codes. A normally completed attached tree is always successful.
        self.outcome = outcome_from_exit_code(ExitCode.SUCCESS)
        state = _INVOCATION_STATE.get()
        if state is not None and state.owner_app is self.attachment.app:
            state.attached_completion = True

    def record_exception(self, exc: BaseException) -> None:
        state = _INVOCATION_STATE.get()
        if state is not None and state.owner_app is self.attachment.app:
            state.attached_completion = False
        if self.context is not None:
            self.outcome = outcome_from_exception(self.click, exc)
            _record_unexpected_traceback(self.context, self.outcome)

    def __exit__(
        self,
        _exc_type: Any,
        exc_value: Any,
        _traceback: Any,
    ) -> None:
        # Click 8.1 closes Context resources without forwarding exception
        # details, so the invoke wrapper records the outcome explicitly.
        if exc_value is not None and self.outcome.kind == "success":
            self.record_exception(exc_value)
        self._finalize()

    def _finalize(self) -> None:
        if self._closed:
            return
        self._closed = True
        context = self.context
        invocation = self.invocation
        if context is None:
            return

        if invocation is None:
            try:
                context.cleanup()
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                _warn_lifecycle_failure(context, "Lifecycle cleanup failed", exc)
            finally:
                if self.context_token is not None:
                    _reset_active_context(context, self.context_token)
            return

        try:
            if not invocation.started:
                invocation.start(force=True)
        except BaseException as exc:  # pylint: disable=broad-exception-caught
            _warn_lifecycle_failure(context, "Invocation redaction or logging failed", exc)

        try:
            ended_at = utc_now()
            ended_monotonic_ns = time.monotonic_ns()
        except BaseException as exc:  # pylint: disable=broad-exception-caught
            ended_at = self.started_at
            ended_monotonic_ns = self.started_monotonic_ns
            _warn_lifecycle_failure(context, "Terminal clock capture failed", exc)

        try:
            if self.attachment.app.profile.history_writer is not None:
                self.attachment.app.profile.history_writer(
                    context,
                    invocation.invocation_argv,
                    set(invocation.redaction_plan),
                    self.started_at,
                    self.outcome.exit_code,
                )
        except BaseException as exc:  # pylint: disable=broad-exception-caught
            _warn_lifecycle_failure(context, "History finalization failed", exc)

        _finish_run_recorder(
            invocation.recorder,
            self.outcome,
            ended_at=ended_at,
            ended_monotonic_ns=ended_monotonic_ns,
        )
        finish_telemetry(
            self.telemetry_session,
            context,
            self.outcome,
            ended_monotonic_ns=ended_monotonic_ns,
        )
        try:
            context.cleanup()
        except BaseException as exc:  # pylint: disable=broad-exception-caught
            _warn_lifecycle_failure(context, "Lifecycle cleanup failed", exc)
        finally:
            if (
                self.original_click_exit is not None
                and getattr(self.click_context, "exit", None) is self.click_exit_wrapper
            ):
                try:
                    self.click_context.exit = self.original_click_exit
                except (AttributeError, TypeError):
                    pass
            if self.invocation_token is not None:
                _reset_context_var(_ATTACHED_INVOCATION, self.invocation_token)
            if self.context_token is not None:
                _reset_active_context(context, self.context_token)


def _normalize_sensitive_parameters(values: Iterable[str]) -> frozenset[str]:
    if isinstance(values, str):
        values = (values,)
    try:
        normalized = frozenset(values)
    except TypeError as exc:
        raise TypeError("sensitive_parameters must be an iterable of strings.") from exc
    if not all(isinstance(value, str) and value for value in normalized):
        raise TypeError("sensitive_parameters must contain only non-empty strings.")
    return normalized


def _lifecycle_option_attrs(
    click: Any,
    key: str,
    option: LifecycleOption,
) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if key in _FLAG_LIFECYCLE_OPTION_KEYS:
        attrs.update(is_flag=True, default=option.default)
    elif key == "config":
        attrs.update(type=_explicit_config_path_type(click), default=option.default)
    elif key == "log_file":
        attrs.update(
            type=click.Path(dir_okay=False, path_type=Path),
            default=option.default,
        )
    else:
        attrs["default"] = option.default
    if option.help is not None:
        attrs["help"] = option.help
    if option.metavar is not None:
        attrs["metavar"] = option.metavar
    if option.envvar is not None:
        attrs["envvar"] = option.envvar
    if option.show_envvar:
        attrs["show_envvar"] = True
    if option.show_default is not None:
        attrs["show_default"] = option.show_default
    if option.hidden:
        attrs["hidden"] = True
    return attrs


def _lifecycle_param_decls(option: LifecycleOption) -> list[str]:
    declarations = list(option.param_decls)
    if option.name is not None:
        declarations.append(option.name)
    return declarations


def _context_depth(click_context: Any) -> int:
    depth = 0
    current = getattr(click_context, "parent", None)
    while current is not None:
        depth += 1
        current = getattr(current, "parent", None)
    return depth


def _capture_lifecycle_option(
    click_context: Any,
    parameter: Any,
    value: Any,
    *,
    key: str,
) -> Any:
    source = click_context.get_parameter_source(parameter.name)
    captures = click_context.meta.setdefault(_LIFECYCLE_CAPTURE_META_KEY, {})
    context_values = captures.setdefault(id(click_context), {})
    context_values[key] = _RawLifecycleValue(
        value=value,
        source=source,
        depth=_context_depth(click_context),
    )
    return value


def _make_lifecycle_value_option(
    click: Any,
    key: str,
    option: LifecycleOption,
) -> Any:
    def capture(click_context: Any, parameter: Any, value: Any) -> Any:
        return _capture_lifecycle_option(
            click_context,
            parameter,
            value,
            key=key,
        )

    expected_flag = key in _FLAG_LIFECYCLE_OPTION_KEYS
    has_secondary_declaration = any(
        (";" if declaration.startswith("/") else "/") in declaration for declaration in option.param_decls
    )
    if not expected_flag and has_secondary_declaration:
        raise RuntimeError(
            f"LifecycleOptions.{key} must accept one scalar value; its configured "
            "declarations change the lifecycle-owned Click option shape."
        )
    attrs = _lifecycle_option_attrs(click, key, option)
    attrs.update(callback=capture, expose_value=False)
    parameter = click.Option(_lifecycle_param_decls(option), **attrs)
    if not isinstance(getattr(parameter, "name", None), str) or not parameter.name:
        raise RuntimeError(f"LifecycleOptions.{key} does not produce a stable Click destination.")
    if bool(getattr(parameter, "is_flag", False)) != expected_flag:
        expected_shape = "a scalar flag" if expected_flag else "one scalar value"
        raise RuntimeError(
            f"LifecycleOptions.{key} must accept {expected_shape}; its configured "
            "declarations change the lifecycle-owned Click option shape."
        )
    return parameter


def _make_lifecycle_version_option(
    click: Any,
    option: LifecycleOption,
    version: str,
) -> Any:
    def version_parameter_source() -> None:
        return None

    attrs: dict[str, Any] = {}
    if option.help is not None:
        attrs["help"] = option.help
    if option.metavar is not None:
        attrs["metavar"] = option.metavar
    if option.envvar is not None:
        attrs["envvar"] = option.envvar
    if option.show_envvar:
        attrs["show_envvar"] = True
    if option.show_default is not None:
        attrs["show_default"] = option.show_default
    if option.hidden:
        attrs["hidden"] = True
    if option.default is not None:
        attrs["default"] = option.default
    decorated = click.version_option(
        version,
        *_lifecycle_param_decls(option),
        **attrs,
    )(version_parameter_source)
    parameters = list(getattr(decorated, "__click_params__", ()))
    if not parameters:
        raise RuntimeError("Click did not create the requested version option.")
    parameter = parameters[-1]
    if not isinstance(getattr(parameter, "name", None), str) or not parameter.name:
        raise RuntimeError("LifecycleOptions.version does not produce a stable Click destination.")
    return parameter


def _normalized_parameter_declarations(
    parameter: Any,
    normalize: Callable[[str], str] | None,
) -> set[str]:
    return {
        _normalize_attached_option_declaration(str(declaration), normalize)
        for declaration in (
            *tuple(getattr(parameter, "opts", ())),
            *tuple(getattr(parameter, "secondary_opts", ())),
        )
    }


def _normalized_parameter_declaration_sets(
    parameter: Any,
    normalize: Callable[[str], str] | None,
) -> tuple[set[str], set[str]]:
    return (
        {
            _normalize_attached_option_declaration(str(declaration), normalize)
            for declaration in tuple(getattr(parameter, "opts", ()))
        },
        {
            _normalize_attached_option_declaration(str(declaration), normalize)
            for declaration in tuple(getattr(parameter, "secondary_opts", ()))
        },
    )


def _reject_duplicate_lifecycle_declarations(
    key: str,
    parameter: Any,
    normalize: Callable[[str], str] | None,
) -> None:
    declarations = [
        _normalize_attached_option_declaration(str(declaration), normalize)
        for declaration in (
            *tuple(getattr(parameter, "opts", ())),
            *tuple(getattr(parameter, "secondary_opts", ())),
        )
    ]
    seen: set[str] = set()
    duplicates: set[str] = set()
    for declaration in declarations:
        if declaration in seen:
            duplicates.add(declaration)
        seen.add(declaration)
    if duplicates:
        aliases = ", ".join(sorted(duplicates))
        raise RuntimeError(
            f"Lifecycle option '{key}' repeats normalized declaration(s) {aliases}. "
            f"Give LifecycleOptions.{key} unique aliases."
        )


def _lifecycle_collision_details(
    parameter: Any,
    existing_parameters: list[Any],
    normalize: Callable[[str], str] | None,
) -> tuple[list[tuple[Any, set[str]]], list[Any]]:
    declarations = _normalized_parameter_declarations(parameter, normalize)
    alias_collisions: list[tuple[Any, set[str]]] = []
    destination_collisions: list[Any] = []
    for existing in existing_parameters:
        overlapping = declarations & _normalized_parameter_declarations(
            existing,
            normalize,
        )
        if overlapping:
            alias_collisions.append((existing, overlapping))
        if getattr(parameter, "name", None) and getattr(existing, "name", None) == parameter.name:
            destination_collisions.append(existing)
    return alias_collisions, destination_collisions


def _implicit_help_declarations(
    command: Any,
    normalize: Callable[[str], str] | None,
) -> set[str]:
    if not bool(getattr(command, "add_help_option", True)):
        return set()
    context_settings = dict(getattr(command, "context_settings", None) or {})
    declarations = context_settings.get("help_option_names", ("--help",))
    if declarations is None:
        declarations = ("--help",)
    return {_normalize_attached_option_declaration(str(declaration), normalize) for declaration in declarations}


def _missing_adopted_declarations(
    requested: Any,
    existing: Any,
    normalize: Callable[[str], str] | None,
) -> set[str]:
    """Return configured aliases absent from the requested vendor flag polarity."""

    requested_positive, requested_negative = _normalized_parameter_declaration_sets(
        requested,
        normalize,
    )
    existing_positive, existing_negative = _normalized_parameter_declaration_sets(
        existing,
        normalize,
    )
    return (requested_positive - existing_positive) | (requested_negative - existing_negative)


def _reject_implicit_help_collision(
    key: str,
    parameter: Any,
    command: Any,
    normalize: Callable[[str], str] | None,
) -> None:
    collisions = _normalized_parameter_declarations(
        parameter,
        normalize,
    ) & _implicit_help_declarations(command, normalize)
    if collisions:
        aliases = ", ".join(sorted(collisions))
        raise RuntimeError(
            f"Lifecycle option '{key}' conflicts with Click's implicit help "
            f"declaration(s) {aliases}. Disable or rename LifecycleOptions.{key}."
        )


def _native_lifecycle_collision_error(
    key: str,
    parameter: Any,
    alias_collisions: list[tuple[Any, set[str]]],
    destination_collisions: list[Any],
    lifecycle_parameter_keys: dict[int, str] | None = None,
) -> RuntimeError:
    lifecycle_parameter_keys = lifecycle_parameter_keys or {}
    conflicting_keys = {
        lifecycle_parameter_keys[id(existing)]
        for existing in (
            *(existing for existing, _declarations in alias_collisions),
            *destination_collisions,
        )
        if id(existing) in lifecycle_parameter_keys
    }
    if conflicting_keys:
        conflicting = ", ".join(f"'{other_key}'" for other_key in sorted(conflicting_keys))
        return RuntimeError(
            f"Lifecycle option '{key}' conflicts with lifecycle option(s) "
            f"{conflicting}. Give LifecycleOptions.{key} a distinct declaration "
            "and Click destination."
        )
    if alias_collisions:
        aliases = sorted(declaration for _existing, declarations in alias_collisions for declaration in declarations)
        detail = f"option declaration(s) {', '.join(aliases)}"
    else:
        detail = f"Click destination '{getattr(parameter, 'name', None)}'"
    return RuntimeError(
        f"Lifecycle option '{key}' conflicts with an application parameter at {detail}. "
        f"Disable or rename LifecycleOptions.{key}."
    )


def _install_native_lifecycle_options(
    click: Any,
    command: Any,
    lifecycle_options: LifecycleOptions,
    *,
    version: str | None,
) -> dict[str, _LifecycleBinding]:
    parameters = getattr(command, "params", None)
    if not isinstance(parameters, list):
        raise TypeError("Click commands must expose a mutable params list.")
    existing_parameters = list(parameters)
    context_settings = dict(getattr(command, "context_settings", None) or {})
    normalize = context_settings.get("token_normalize_func")
    bindings: dict[str, _LifecycleBinding] = {}
    lifecycle_parameter_keys: dict[int, str] = {}

    lifecycle_parameters: dict[str, Any] = {}
    version_parameter: Any | None = None

    for key in _NATIVE_LIFECYCLE_OPTION_ORDER:
        option = getattr(lifecycle_options, key)
        if option is None:
            continue
        parameter = _make_lifecycle_value_option(click, key, option)
        _reject_duplicate_lifecycle_declarations(key, parameter, normalize)
        _reject_implicit_help_collision(key, parameter, command, normalize)
        alias_collisions, destination_collisions = _lifecycle_collision_details(
            parameter,
            existing_parameters,
            normalize,
        )
        if alias_collisions or destination_collisions:
            raise _native_lifecycle_collision_error(
                key,
                parameter,
                alias_collisions,
                destination_collisions,
                lifecycle_parameter_keys,
            )
        parameters.append(parameter)
        existing_parameters.append(parameter)
        lifecycle_parameter_keys[id(parameter)] = key
        lifecycle_parameters[key] = parameter
        bindings[key] = _LifecycleBinding(
            key=key,
            parameter_name=str(parameter.name),
            adopted=False,
        )

    version_option = lifecycle_options.version
    if version is not None and version_option is not None:
        parameter = _make_lifecycle_version_option(click, version_option, version)
        _reject_duplicate_lifecycle_declarations("version", parameter, normalize)
        _reject_implicit_help_collision("version", parameter, command, normalize)
        alias_collisions, destination_collisions = _lifecycle_collision_details(
            parameter,
            existing_parameters,
            normalize,
        )
        if alias_collisions or destination_collisions:
            raise _native_lifecycle_collision_error(
                "version",
                parameter,
                alias_collisions,
                destination_collisions,
                lifecycle_parameter_keys,
            )
        parameters.append(parameter)
        version_parameter = parameter

    parameters[:] = [
        *([version_parameter] if version_parameter is not None else []),
        *(lifecycle_parameters[key] for key in _NATIVE_LIFECYCLE_OPTION_ORDER if key in lifecycle_parameters),
        *(parameter for parameter in existing_parameters if id(parameter) not in lifecycle_parameter_keys),
    ]

    setattr(command, _CLICK_LIFECYCLE_BINDINGS_ATTRIBUTE, bindings)
    return bindings


def _parameter_source_rank(source: Any) -> int:
    name = getattr(source, "name", None)
    if not isinstance(name, str):
        return 0
    return {
        "COMMANDLINE": 4,
        "PROMPT": 4,
        "ENVIRONMENT": 3,
        "DEFAULT_MAP": 2,
        "DEFAULT": 1,
    }.get(name, 0)


def _prefer_lifecycle_value(
    current: _RawLifecycleValue | None,
    candidate: _RawLifecycleValue | None,
) -> _RawLifecycleValue | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    current_rank = _parameter_source_rank(current.source)
    candidate_rank = _parameter_source_rank(candidate.source)
    if candidate_rank > current_rank:
        return candidate
    if candidate_rank == current_rank and candidate.depth >= current.depth:
        return candidate
    return current


def _normalize_lifecycle_values(
    click: Any,
    raw: dict[str, _RawLifecycleValue],
) -> LifecycleValues:
    def raw_value(key: str) -> Any:
        selected = raw.get(key)
        return None if selected is None else selected.value

    environment = raw_value("environment")
    if environment is not None and not isinstance(environment, str):
        raise click.UsageError("The configured lifecycle environment option must produce a string.")

    paths: dict[str, Path | None] = {}
    for key in ("config", "log_file"):
        value = raw_value(key)
        if value is None:
            paths[key] = None
            continue
        try:
            raw_path = os.fspath(value)
        except TypeError:
            raw_path = None
        if not isinstance(raw_path, str):
            raise click.UsageError(
                f"The configured lifecycle {key.replace('_', '-')} option must produce a string or path-like object."
            )
        paths[key] = Path(raw_path)

    return LifecycleValues(
        debug=bool(raw_value("debug")),
        quiet=bool(raw_value("quiet")),
        environment=environment,
        config=paths["config"],
        keep_temp=bool(raw_value("keep_temp")),
        log_file=paths["log_file"],
        dry_run=bool(raw_value("dry_run")),
        json=bool(raw_value("json")),
    )


def _resolve_lifecycle_values(
    click: Any,
    click_context: Any,
    bindings: dict[str, _LifecycleBinding],
    *,
    extra_values: dict[str, _RawLifecycleValue] | None = None,
) -> _LifecycleResolution:
    existing_resolution_map = click_context.meta.get(
        _LIFECYCLE_RESOLUTION_META_KEY,
    )
    if LIFECYCLE_META_KEY in click_context.meta:
        existing_public_value = click_context.meta[LIFECYCLE_META_KEY]
        framework_values = (
            tuple(
                resolution.values
                for resolution in existing_resolution_map.values()
                if isinstance(resolution, _LifecycleResolution)
            )
            if isinstance(existing_resolution_map, dict)
            else ()
        )
        if not any(existing_public_value is value for value in framework_values):
            raise click.UsageError(
                f"Click context metadata key {LIFECYCLE_META_KEY!r} is reserved for "
                "base-cli LifecycleValues. Rename the application metadata key."
            )
    resolution_map = click_context.meta.setdefault(
        _LIFECYCLE_RESOLUTION_META_KEY,
        {},
    )
    parent = getattr(click_context, "parent", None)
    parent_resolution = resolution_map.get(id(parent)) if parent is not None else None
    raw = dict(parent_resolution.raw) if isinstance(parent_resolution, _LifecycleResolution) else {}
    captures = click_context.meta.get(_LIFECYCLE_CAPTURE_META_KEY, {})
    context_captures = captures.get(id(click_context), {})
    depth = _context_depth(click_context)

    for key, binding in bindings.items():
        if binding.adopted:
            candidate = _RawLifecycleValue(
                value=getattr(click_context, "params", {}).get(binding.parameter_name),
                source=click_context.get_parameter_source(binding.parameter_name),
                depth=depth,
            )
        else:
            candidate = context_captures.get(key)
        selected = _prefer_lifecycle_value(raw.get(key), candidate)
        if selected is not None:
            raw[key] = selected

    for key, candidate in (extra_values or {}).items():
        selected = _prefer_lifecycle_value(raw.get(key), candidate)
        if selected is not None:
            raw[key] = selected

    resolution = _LifecycleResolution(
        values=_normalize_lifecycle_values(click, raw),
        raw=raw,
    )
    resolution_map[id(click_context)] = resolution
    click_context.meta[LIFECYCLE_META_KEY] = resolution.values
    return resolution


def _standard_options_from_values(values: LifecycleValues) -> dict[str, Any]:
    return {key: getattr(values, key) for key in _STANDARD_OPTION_KEYS}


def _add_attached_standard_options(
    click: Any,
    command: Any,
    *,
    lifecycle_options: LifecycleOptions,
    version: str | None,
    added_parameters: list[Any],
) -> dict[str, _LifecycleBinding]:
    parameters = getattr(command, "params", None)
    if not isinstance(parameters, list):
        raise TypeError("Attached Click commands must expose a mutable params list.")
    existing_parameters = list(parameters)
    existing_options = [
        parameter for parameter in existing_parameters if getattr(parameter, "param_type_name", None) == "option"
    ]
    context_settings = dict(getattr(command, "context_settings", None) or {})
    token_normalize_func = context_settings.get("token_normalize_func")
    bindings: dict[str, _LifecycleBinding] = {}
    bound_existing_parameters: dict[int, str] = {}

    for key in _ATTACHED_LIFECYCLE_OPTION_ORDER:
        option = getattr(lifecycle_options, key)
        if option is None:
            continue
        parameter = _make_lifecycle_value_option(click, key, option)
        _reject_duplicate_lifecycle_declarations(
            key,
            parameter,
            token_normalize_func,
        )
        _reject_implicit_help_collision(
            key,
            parameter,
            command,
            token_normalize_func,
        )
        normalized_primary = _normalize_attached_option_declaration(
            str(parameter.opts[0]),
            token_normalize_func,
        )
        primary_matches = [
            existing
            for existing in existing_options
            if normalized_primary
            in _normalized_parameter_declarations(
                existing,
                token_normalize_func,
            )
        ]
        if len(primary_matches) > 1:
            raise RuntimeError(
                f"Lifecycle option '{key}' has ambiguous attached declaration "
                f"'{parameter.opts[0]}'; multiple Click options already use it."
            )
        existing = primary_matches[0] if primary_matches else None
        if existing is not None:
            if option.name is not None and existing.name != option.name:
                raise RuntimeError(
                    f"Existing '{parameter.opts[0]}' option uses Click destination "
                    f"{existing.name!r}, but LifecycleOptions.{key} requires "
                    f"{option.name!r}. Remove name= to adopt the vendor destination, "
                    "or rename/disable the lifecycle option."
                )
            previous_key = bound_existing_parameters.get(id(existing))
            if previous_key is not None:
                raise RuntimeError(
                    f"Existing Click option combines lifecycle aliases "
                    f"'{previous_key}' and '{key}' in one parameter; each "
                    "base-cli lifecycle option must use a distinct parameter."
                )
            alias_collisions, _destination_collisions = _lifecycle_collision_details(
                parameter,
                existing_parameters,
                token_normalize_func,
            )
            foreign_aliases = [
                (candidate, declarations) for candidate, declarations in alias_collisions if candidate is not existing
            ]
            if foreign_aliases:
                aliases = sorted(
                    declaration for _candidate, declarations in foreign_aliases for declaration in declarations
                )
                raise RuntimeError(
                    f"Lifecycle option '{key}' cannot adopt '{parameter.opts[0]}' "
                    f"because its other declaration(s) collide: {', '.join(aliases)}."
                )
            missing_declarations = _missing_adopted_declarations(
                parameter,
                existing,
                token_normalize_func,
            )
            if missing_declarations:
                alias_text = ", ".join(sorted(missing_declarations))
                raise RuntimeError(
                    f"Existing '{parameter.opts[0]}' option is incompatible with "
                    f"LifecycleOptions.{key}; it does not expose configured "
                    f"declaration(s) {alias_text} with the required flag polarity. "
                    "Add compatible aliases to the vendor option, or rename/disable "
                    "the lifecycle option."
                )
            foreign_destinations = [
                candidate
                for candidate in existing_parameters
                if candidate is not existing and getattr(candidate, "name", None) == getattr(existing, "name", None)
            ]
            if foreign_destinations:
                raise RuntimeError(
                    f"Lifecycle option '{key}' cannot adopt '{parameter.opts[0]}' "
                    f"because Click destination {existing.name!r} is also used by "
                    "another application parameter. Rename that destination or "
                    f"disable LifecycleOptions.{key}."
                )
            expected_flag = key in _FLAG_LIFECYCLE_OPTION_KEYS
            is_flag = bool(getattr(existing, "is_flag", False) or getattr(existing, "count", False))
            positive_declarations = {
                _normalize_attached_option_declaration(
                    str(declaration),
                    token_normalize_func,
                )
                for declaration in tuple(getattr(existing, "opts", ()))
            }
            secondary_declarations = {
                _normalize_attached_option_declaration(
                    str(declaration),
                    token_normalize_func,
                )
                for declaration in tuple(getattr(existing, "secondary_opts", ()))
            }
            incompatible = (
                is_flag != expected_flag
                or bool(getattr(existing, "count", False))
                or not getattr(existing, "expose_value", True)
                or getattr(existing, "prompt", None) is not None
                or bool(getattr(existing, "multiple", False))
                or getattr(existing, "nargs", 1) != 1
                or normalized_primary in secondary_declarations
                or (
                    expected_flag
                    and (
                        normalized_primary not in positive_declarations
                        or not bool(getattr(existing, "flag_value", False))
                    )
                )
            )
            if incompatible:
                raise RuntimeError(
                    f"Existing '{parameter.opts[0]}' option is incompatible with "
                    f"LifecycleOptions.{key}; rename or disable that lifecycle option."
                )
            bound_existing_parameters[id(existing)] = key
            parameter_name = getattr(existing, "name", None)
            if not parameter_name:
                raise RuntimeError(f"Existing '{parameter.opts[0]}' option has no Click destination.")
            bindings[key] = _LifecycleBinding(
                key=key,
                parameter_name=str(parameter_name),
                adopted=True,
            )
            continue

        alias_collisions, destination_collisions = _lifecycle_collision_details(
            parameter,
            existing_parameters,
            token_normalize_func,
        )
        if alias_collisions or destination_collisions:
            raise _native_lifecycle_collision_error(
                key,
                parameter,
                alias_collisions,
                destination_collisions,
                bound_existing_parameters,
            )
        parameters.append(parameter)
        added_parameters.append(parameter)
        existing_parameters.append(parameter)
        existing_options.append(parameter)
        bound_existing_parameters[id(parameter)] = key
        bindings[key] = _LifecycleBinding(
            key=key,
            parameter_name=str(parameter.name),
            adopted=False,
        )

    version_option = lifecycle_options.version
    if version is not None and version_option is not None:
        parameter = _make_lifecycle_version_option(click, version_option, version)
        _reject_duplicate_lifecycle_declarations(
            "version",
            parameter,
            token_normalize_func,
        )
        _reject_implicit_help_collision(
            "version",
            parameter,
            command,
            token_normalize_func,
        )
        normalized_primary = _normalize_attached_option_declaration(
            str(parameter.opts[0]),
            token_normalize_func,
        )
        primary_matches = [
            existing
            for existing in existing_options
            if normalized_primary
            in _normalized_parameter_declarations(
                existing,
                token_normalize_func,
            )
        ]
        if len(primary_matches) > 1:
            raise RuntimeError(f"Lifecycle version declaration '{parameter.opts[0]}' is ambiguous.")
        if primary_matches:
            existing = primary_matches[0]
            if version_option.name is not None and existing.name != version_option.name:
                raise RuntimeError(
                    f"Existing '{parameter.opts[0]}' option uses Click destination "
                    f"{existing.name!r}, but LifecycleOptions.version requires "
                    f"{version_option.name!r}. Remove name= to adopt the vendor "
                    "destination, or rename/disable the lifecycle version option."
                )
            compatible = bool(getattr(existing, "is_flag", False) and getattr(existing, "is_eager", False))
            if not compatible:
                raise RuntimeError(
                    f"Existing '{parameter.opts[0]}' option is incompatible with "
                    "LifecycleOptions.version; rename or disable the lifecycle version option."
                )
            alias_collisions, _destination_collisions = _lifecycle_collision_details(
                parameter,
                existing_parameters,
                token_normalize_func,
            )
            if any(candidate is not existing for candidate, _aliases in alias_collisions):
                raise RuntimeError("LifecycleOptions.version has an alias used by another Click option.")
            missing_declarations = _missing_adopted_declarations(
                parameter,
                existing,
                token_normalize_func,
            )
            if missing_declarations:
                alias_text = ", ".join(sorted(missing_declarations))
                raise RuntimeError(
                    "Existing lifecycle version option does not expose configured "
                    f"declaration(s) {alias_text} with the required flag polarity. "
                    "Add compatible aliases to the vendor option, or rename/disable "
                    "the lifecycle version option."
                )
            if any(
                candidate is not existing and getattr(candidate, "name", None) == getattr(existing, "name", None)
                for candidate in existing_parameters
            ):
                raise RuntimeError(
                    "LifecycleOptions.version adopts a Click destination used by "
                    "another application parameter. Rename that destination or "
                    "disable the lifecycle version option."
                )
            return bindings

        alias_collisions, destination_collisions = _lifecycle_collision_details(
            parameter,
            existing_parameters,
            token_normalize_func,
        )
        if alias_collisions or destination_collisions:
            raise _native_lifecycle_collision_error(
                "version",
                parameter,
                alias_collisions,
                destination_collisions,
                bound_existing_parameters,
            )
        parameters.append(parameter)
        added_parameters.append(parameter)

    return bindings


def _normalize_attached_option_declaration(
    declaration: str,
    normalize: Callable[[str], str] | None,
) -> str:
    if normalize is None:
        return declaration
    first = declaration[:1]
    if not first or first.isalnum() or first == "_":
        return declaration
    prefix = declaration[:2] if declaration[1:2] == first else first
    return f"{prefix}{normalize(declaration[len(prefix) :])}"


def _selected_click_path(
    root_context: Any,
    selected_context: Any | None,
    resolved_children: dict[int, list[tuple[str, Any, Any]]],
) -> tuple[tuple[str, Any], ...]:
    if selected_context is not None:
        contexts: list[Any] = []
        current = selected_context
        while current is not None:
            contexts.append(current)
            if current is root_context:
                contexts.reverse()
                selected: list[tuple[str, Any]] = []
                for parent, child in zip(contexts, contexts[1:], strict=False):
                    resolutions = resolved_children.get(id(parent), [])
                    recorded = next(
                        (resolution for resolution in reversed(resolutions) if resolution[2] is child),
                        None,
                    )
                    invoked_name = (
                        recorded[0]
                        if recorded is not None
                        else getattr(child, "info_name", None) or getattr(child.command, "name", "")
                    )
                    selected.append((str(invoked_name), child.command))
                return tuple(selected)
            current = getattr(current, "parent", None)

    path: list[tuple[str, Any]] = []
    parent = root_context
    seen: set[int] = set()
    while id(parent) not in seen:
        seen.add(id(parent))
        resolutions = resolved_children.get(id(parent), [])
        if not resolutions:
            break
        name, command, child_context = resolutions[-1]
        path.append((str(name), command))
        if child_context is None:
            break
        parent = child_context
    return tuple(path)


def _selected_click_paths(
    root_context: Any,
    resolved_children: dict[int, list[tuple[str, Any, Any]]],
    resolution_parents: dict[int, Any],
) -> tuple[tuple[tuple[str, Any], ...], ...]:
    paths: list[tuple[tuple[str, Any], ...]] = []
    seen: set[tuple[tuple[str, int], ...]] = set()
    for parent_identity, resolutions in resolved_children.items():
        for name, command, child_context in resolutions:
            if child_context is not None:
                path = _selected_click_path(
                    root_context,
                    child_context,
                    resolved_children,
                )
            else:
                parent_context = resolution_parents.get(parent_identity)
                parent_path = (
                    _selected_click_path(
                        root_context,
                        parent_context,
                        resolved_children,
                    )
                    if parent_context is not None
                    else ()
                )
                path = (*parent_path, (name, command))
            identity = tuple((name, id(command)) for name, command in path)
            if path and identity not in seen:
                paths.append(path)
                seen.add(identity)
    if not paths:
        fallback = _selected_click_path(root_context, None, resolved_children)
        if fallback:
            paths.append(fallback)
    return tuple(paths)


def _click_command_has_pending_children(click_context: Any, command: Any) -> bool:
    if not callable(getattr(command, "resolve_command", None)):
        return False
    protected = getattr(click_context, "_protected_args", None)
    if protected is None:
        protected = getattr(click_context, "protected_args", ())
    return bool(protected or getattr(click_context, "args", ()))


def _with_attached_lifecycle_resource(
    click_context: Any,
    resource: _AttachedLifecycleResource,
) -> None:
    # Parameter callbacks can register close hooks while Click parses the root
    # context, before Command.invoke gives us a lifecycle boundary. Move those
    # already-entered resources into a nested ExitStack so they unwind while
    # the base-cli Context is still active and can influence the final outcome.
    exit_stack = getattr(click_context, "_exit_stack", None)
    pop_all = getattr(exit_stack, "pop_all", None)
    if not callable(pop_all):
        click_context.with_resource(resource)
        resource.initialize_factories()
        return
    earlier_resources = pop_all()
    try:
        click_context.with_resource(resource)
    finally:
        click_context.with_resource(earlier_resources)
    resource.initialize_factories()


def _instrument_attached_click_command(click: Any, command: Any) -> None:
    with _CLICK_ATTACHMENT_LOCK:
        marker = getattr(command, _CLICK_INSTRUMENTED_ATTRIBUTE, None)
        if marker is _CLICK_INSTRUMENTED_SENTINEL:
            return
        if marker is not None:
            raise RuntimeError("Click command uses base-cli's reserved command instrumentation marker.")
        _reject_async_callback(getattr(command, "callback", None))
        original_invoke = command.invoke
        original_resolve = getattr(command, "resolve_command", None)

        @functools.wraps(original_invoke)
        def invoke(click_context: Any) -> Any:
            active = _ATTACHED_INVOCATION.get()
            with _CLICK_ATTACHMENT_LOCK:
                attachment = getattr(command, _CLICK_ATTACHMENT_ATTRIBUTE, None)
            if active is not None and isinstance(attachment, _ClickAttachment) and attachment is not active.attachment:
                raise RuntimeError(
                    f"Click command '{getattr(command, 'name', None) or '<unnamed>'}' "
                    "is attached to a different base_cli.App and cannot be nested "
                    "inside another attached tree."
                )
            if active is None and isinstance(attachment, _ClickAttachment):
                resolution = _resolve_lifecycle_values(
                    click,
                    click_context,
                    attachment.standard_bindings,
                )
                standard = _standard_options_from_values(resolution.values)
                _validate_standard_options(
                    click,
                    standard,
                    attachment.lifecycle_options,
                )
                _capture_standard_options(standard, attachment.app)
                resource = _AttachedLifecycleResource(
                    click,
                    attachment,
                    click_context,
                    resolution.values,
                )
                _with_attached_lifecycle_resource(click_context, resource)
                if not _click_command_has_pending_children(click_context, command):
                    if resource.invocation is not None:
                        resource.invocation.start(click_context)
                try:
                    result = _reject_async_result(original_invoke(click_context))
                except BaseException as exc:
                    resource.record_exception(exc)
                    raise
                resource.record_result(result)
                return result

            if active is not None:
                active.note_child_context(click_context)
                if not _click_command_has_pending_children(click_context, command):
                    active.start(click_context)
            return _reject_async_result(original_invoke(click_context))

        try:
            setattr(command, _CLICK_ORIGINAL_INVOKE_ATTRIBUTE, original_invoke)
            setattr(command, _CLICK_ORIGINAL_RESOLVE_ATTRIBUTE, original_resolve)
            command.invoke = invoke

            if callable(original_resolve):

                @functools.wraps(original_resolve)
                def resolve_command(click_context: Any, args: list[str]) -> Any:
                    invoked_name = str(args[0]) if args else ""
                    command_name, child, remaining = original_resolve(click_context, args)
                    if child is not None:
                        active = _ATTACHED_INVOCATION.get()
                        with _CLICK_ATTACHMENT_LOCK:
                            child_attachment = getattr(
                                child,
                                _CLICK_ATTACHMENT_ATTRIBUTE,
                                None,
                            )
                        child_owner = getattr(child, _CLICK_APP_OWNER_ATTRIBUTE, None)
                        if (
                            active is not None
                            and isinstance(child_attachment, _ClickAttachment)
                            and child_attachment is not active.attachment
                        ):
                            raise RuntimeError(
                                f"Click command '{getattr(child, 'name', None) or '<unnamed>'}' "
                                "is attached to a different base_cli.App and cannot be nested "
                                "inside another attached tree."
                            )
                        if active is not None and isinstance(child_owner, App):
                            raise RuntimeError(
                                f"Click command '{getattr(child, 'name', None) or '<unnamed>'}' "
                                "already belongs to a native base_cli.App and cannot be nested "
                                "inside an attached tree because that would create a second lifecycle."
                            )
                        _instrument_attached_click_command(click, child)
                        if active is not None:
                            active.note_resolution(
                                click_context,
                                invoked_name or str(command_name),
                                child,
                            )
                    return command_name, child, remaining

                command.resolve_command = resolve_command

            setattr(command, _CLICK_INSTRUMENTED_ATTRIBUTE, _CLICK_INSTRUMENTED_SENTINEL)
        except BaseException:
            _restore_attached_click_command(command)
            raise


def _restore_attached_click_command(command: Any) -> None:
    original_invoke = getattr(command, _CLICK_ORIGINAL_INVOKE_ATTRIBUTE, None)
    original_resolve = getattr(command, _CLICK_ORIGINAL_RESOLVE_ATTRIBUTE, None)
    if original_invoke is not None:
        try:
            command.invoke = original_invoke
        except (AttributeError, TypeError):
            pass
    if callable(original_resolve):
        try:
            command.resolve_command = original_resolve
        except (AttributeError, TypeError):
            pass
    for attribute in (
        _CLICK_ORIGINAL_INVOKE_ATTRIBUTE,
        _CLICK_ORIGINAL_RESOLVE_ATTRIBUTE,
    ):
        try:
            delattr(command, attribute)
        except (AttributeError, TypeError):
            pass
    if getattr(command, _CLICK_INSTRUMENTED_ATTRIBUTE, None) is _CLICK_INSTRUMENTED_SENTINEL:
        try:
            delattr(command, _CLICK_INSTRUMENTED_ATTRIBUTE)
        except (AttributeError, TypeError):
            pass


def _instrument_attached_click_main(command: Any) -> None:
    marker = getattr(command, _CLICK_MAIN_INSTRUMENTED_ATTRIBUTE, None)
    if marker is _CLICK_MAIN_INSTRUMENTED_SENTINEL:
        return
    if marker is not None:
        raise RuntimeError("Click command uses base-cli's reserved main instrumentation marker.")
    original_main = command.main

    @functools.wraps(original_main)
    def main(*args: Any, **kwargs: Any) -> Any:
        if _INVOCATION_MAIN_BYPASS.get() is command:
            bypass_token = _INVOCATION_MAIN_BYPASS.set(None)
            try:
                return original_main(*args, **kwargs)
            finally:
                _reset_context_var(_INVOCATION_MAIN_BYPASS, bypass_token)
        explicit_args = kwargs.get("args", args[0] if args else None)
        prog_name = kwargs.get("prog_name", args[1] if len(args) > 1 else None)
        if explicit_args is None:
            invocation_argv = list(sys.argv)
        else:
            materialized_args = list(explicit_args)
            invocation_argv = [
                prog_name or getattr(command, "name", None) or "cli",
                *materialized_args,
            ]
            if "args" in kwargs or not args:
                kwargs = {**kwargs, "args": materialized_args}
            else:
                args = (materialized_args, *args[1:])
        token = _INVOCATION_ARGV.set(invocation_argv)
        try:
            return original_main(*args, **kwargs)
        finally:
            _reset_context_var(_INVOCATION_ARGV, token)

    try:
        setattr(command, _CLICK_ORIGINAL_MAIN_ATTRIBUTE, original_main)
        command.main = main
        setattr(command, _CLICK_MAIN_INSTRUMENTED_ATTRIBUTE, _CLICK_MAIN_INSTRUMENTED_SENTINEL)
    except BaseException:
        _restore_attached_click_main(command)
        raise


def _restore_attached_click_main(command: Any) -> None:
    original_main = getattr(command, _CLICK_ORIGINAL_MAIN_ATTRIBUTE, None)
    if original_main is not None:
        try:
            command.main = original_main
        except (AttributeError, TypeError):
            pass
    if getattr(command, _CLICK_MAIN_INSTRUMENTED_ATTRIBUTE, None) is _CLICK_MAIN_INSTRUMENTED_SENTINEL:
        try:
            delattr(command, _CLICK_MAIN_INSTRUMENTED_ATTRIBUTE)
        except (AttributeError, TypeError):
            pass
    for attribute in (_CLICK_ORIGINAL_MAIN_ATTRIBUTE,):
        try:
            delattr(command, attribute)
        except (AttributeError, TypeError):
            pass


def get_command_app(command_func: Any) -> App:
    """Return the :class:`App` owning a registered function or attached tree."""

    with _CLICK_ATTACHMENT_LOCK:
        attachment = getattr(command_func, _CLICK_ATTACHMENT_ATTRIBUTE, None)
        if (
            isinstance(attachment, _ClickAttachment)
            and attachment.command is command_func
            and isinstance(attachment.app, App)
        ):
            owner = attachment.app
            with owner._registration_lock:  # pylint: disable=protected-access
                if (
                    owner._attached_command is command_func  # pylint: disable=protected-access
                    and owner._click_command is command_func  # pylint: disable=protected-access
                    and owner._registration_state == _REGISTRATION_FROZEN  # pylint: disable=protected-access
                ):
                    return owner
    with _COMMAND_APP_LOCK:
        registered_owner = getattr(command_func, _COMMAND_APP_ATTRIBUTE, None)
        if isinstance(registered_owner, App):
            with registered_owner._registration_lock:  # pylint: disable=protected-access
                if registered_owner._command_func is command_func:  # pylint: disable=protected-access
                    return registered_owner
    raise TypeError(
        "Expected a base_cli.App, an attached Click command, or a function registered with @base_cli.command()."
    )


def attach(
    command: _ClickCommandT,
    *,
    app: App | None = None,
    context_factory: Callable[[Context[Any, Any, Any]], Any] | None = None,
    service_factory: Callable[[Context[Any, Any, Any]], Any] | None = None,
    sensitive_parameters: Iterable[str] = (),
    **app_kwargs: Any,
) -> _ClickCommandT:
    """Attach lifecycle middleware and return the same Click command object.

    Attachment ownership, factories, and sensitivity policy are immutable;
    repeating the same attachment (or omitting its existing policy through
    this helper) is idempotent.
    """

    normalized_sensitive_parameters = _normalize_sensitive_parameters(sensitive_parameters)
    existing = getattr(command, _CLICK_ATTACHMENT_ATTRIBUTE, None)
    if app is not None and app_kwargs:
        unexpected = ", ".join(sorted(app_kwargs))
        raise TypeError(f"App constructor arguments cannot be used with app= ({unexpected}).")
    if isinstance(existing, _ClickAttachment) and (app is None or app is existing.app):
        existing_app = get_command_app(command)
        if app_kwargs:
            unexpected = ", ".join(sorted(app_kwargs))
            raise TypeError(f"Click command is already attached; app arguments cannot be changed ({unexpected}).")
        if not normalized_sensitive_parameters:
            normalized_sensitive_parameters = existing.sensitive_parameters
        if (
            context_factory is None
            and service_factory is None
            and normalized_sensitive_parameters == existing.sensitive_parameters
        ):
            return command
        app = existing_app
    if app is None:
        command_name = getattr(command, "name", None)
        if not isinstance(command_name, str) or not command_name:
            raise TypeError("attach() requires a named Click command.")
        name = app_kwargs.pop("name", None) or command_name
        app = App(name=name, **app_kwargs)
    if not isinstance(app, App):
        raise TypeError("app must be a base_cli.App instance or None.")
    return app.attach(
        command,
        context_factory=context_factory,
        service_factory=service_factory,
        sensitive_parameters=normalized_sensitive_parameters,
    )


def run_app(
    app: App | Callable[..., Any],
    argv: list[str] | None = None,
    *,
    reraise_unexpected: bool = False,
) -> int:
    """Run an App, registered command, or attached Click tree and return its status."""

    if not isinstance(app, App):
        app = get_command_app(app)

    try:
        click = _require_click()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return ExitCode.FAILURE

    explicit_argv = argv is not None
    args = list(sys.argv[1:] if argv is None else argv)
    leading_debug, leading_quiet = _leading_output_flags(
        args,
        app.lifecycle_options,
    )
    state = _InvocationState(
        owner_app=app,
        debug=leading_debug,
        quiet=leading_quiet,
        debug_option=_primary_lifecycle_declaration(
            app.lifecycle_options.debug,
        ),
        json_output=_json_requested(args, app.lifecycle_options),
    )
    state_token = _INVOCATION_STATE.set(state)
    output_capture: io.StringIO | None = None
    try:
        try:
            display_command = app.profile.display_command()
            invocation_argv = _effective_invocation_argv(app, args, explicit_argv, display_command)
            command = app.click_command
            click = dialect_for_command(command)
            invocation_token = _INVOCATION_ARGV.set(invocation_argv)
            try:
                bypass_token = _INVOCATION_MAIN_BYPASS.set(command)
                # A configured JSON option may be enabled by any Click-supported
                # source (for example ``default_map`` or a combined short flag),
                # so raw argv cannot determine capture eligibility.  Buffer the
                # command whenever JSON mode exists and let the parsed lifecycle
                # value decide whether to emit an envelope or replay human text.
                output_capture = io.StringIO() if app.lifecycle_options.json is not None else None
                try:
                    if output_capture is None:
                        result = command.main(
                            args=args,
                            prog_name=display_command or app.name,
                            standalone_mode=False,
                        )
                    else:
                        with redirect_stdout(output_capture):
                            result = command.main(
                                args=args,
                                prog_name=display_command or app.name,
                                standalone_mode=False,
                            )
                finally:
                    _reset_context_var(_INVOCATION_MAIN_BYPASS, bypass_token)
            finally:
                _reset_context_var(_INVOCATION_ARGV, invocation_token)
        except click.Abort as exc:
            outcome = outcome_from_exception(click, exc)
            if state.json_output:
                _emit_json_error(state, outcome, str(exc), output_capture)
                return outcome.exit_code
            if outcome.kind == "interrupted":
                print("Interrupted.", file=sys.stderr)
            else:
                print("Aborted!", file=sys.stderr)
            return outcome.exit_code
        except click.ClickException as exc:
            outcome = outcome_from_exception(click, exc)
            if state.json_output:
                if reraise_unexpected:
                    raise
                _emit_json_error(state, outcome, exc.format_message(), output_capture)
                return outcome.exit_code
            if outcome.kind == "unexpected_error":
                if reraise_unexpected:
                    raise
                _show_unexpected_error(state, exc)
                return outcome.exit_code
            exc.show()
            return outcome.exit_code
        except KeyboardInterrupt:
            if state.json_output:
                outcome = outcome_from_exception(click, KeyboardInterrupt())
                _emit_json_error(state, outcome, "Interrupted.", output_capture)
                return outcome.exit_code
            print("Interrupted.", file=sys.stderr)
            return ExitCode.INTERRUPTED
        except SystemExit as exc:
            if state.json_output:
                outcome = outcome_from_exception(click, exc)
                detail = str(exc.code) if exc.code is not None and not isinstance(exc.code, int) else ""
                _emit_json_error(state, outcome, detail or "Command exited.", output_capture)
                return outcome.exit_code
            if exc.code is not None and not isinstance(exc.code, int):
                print(str(exc.code), file=sys.stderr)
            return system_exit_code(exc)
        except Exception as exc:
            if reraise_unexpected:
                raise
            if state.json_output:
                outcome = outcome_from_exception(click, exc)
                _emit_json_error(state, outcome, "Unexpected internal error.", output_capture)
                return outcome.exit_code
            _show_unexpected_error(state, exc)
            return ExitCode.FAILURE

        try:
            if state.attached_completion:
                if state.json_output:
                    _emit_json_success(state, ExitCode.SUCCESS, output_capture)
                return ExitCode.SUCCESS
            exit_code = _normalize_command_result(result)
            if state.json_output:
                if exit_code == ExitCode.SUCCESS:
                    _emit_json_success(state, exit_code, output_capture)
                else:
                    _emit_json_error(
                        state,
                        outcome_from_exit_code(exit_code),
                        "Command returned a non-zero exit code.",
                        output_capture,
                    )
            return exit_code
        except TypeError as exc:
            if state.json_output:
                outcome = outcome_from_exception(click, exc)
                _emit_json_error(state, outcome, str(exc), output_capture)
                return outcome.exit_code
            print(f"ERROR: {exc}", file=sys.stderr)
            return ExitCode.FAILURE
    finally:
        if output_capture is not None and not state.json_output:
            sys.stdout.write(output_capture.getvalue())
        _reset_context_var(_INVOCATION_STATE, state_token)


def _json_requested(args: list[str], lifecycle_options: LifecycleOptions) -> bool:
    option = lifecycle_options.json
    if option is None:
        return False
    if option.default is True:
        return True
    if option.envvar is not None:
        envvars = (option.envvar,) if isinstance(option.envvar, str) else option.envvar
        if any(os.environ.get(name, "").lower() in {"1", "true", "yes", "on"} for name in envvars):
            return True
    declarations = tuple(declaration for declaration in option.param_decls if declaration.startswith(("-", "/")))
    return any(
        argument == declaration or argument.startswith(f"{declaration}=")
        for argument in args
        for declaration in declarations
    )


def _captured_stdout(output_capture: io.StringIO | None) -> str:
    return "" if output_capture is None else output_capture.getvalue()


def _emit_json_success(
    state: _InvocationState,
    exit_code: int,
    output_capture: io.StringIO | None,
) -> None:
    details = {
        "exit_code": exit_code,
        "stdout": _captured_stdout(output_capture),
    }
    sys.stdout.write(
        dumps_envelope(
            success_envelope(
                run_id=state.run_id,
                details=details,
                message="Success" if exit_code == ExitCode.SUCCESS else "Command completed with a non-zero exit code.",
                code="ok" if exit_code == ExitCode.SUCCESS else "nonzero_return",
            )
        )
    )


def _emit_json_error(
    state: _InvocationState,
    outcome: InvocationOutcome,
    message: str,
    output_capture: io.StringIO | None,
) -> None:
    if outcome.exit_code == ExitCode.SUCCESS:
        _emit_json_success(state, outcome.exit_code, output_capture)
        return
    sys.stdout.write(
        dumps_envelope(
            error_envelope(
                run_id=state.run_id,
                code=outcome.kind,
                message=message,
                details={
                    "exit_code": outcome.exit_code,
                    "stdout": _captured_stdout(output_capture),
                },
            )
        )
    )


def _show_unexpected_error(state: _InvocationState, exc: Exception) -> None:
    print("Error: Unexpected internal error.", file=sys.stderr)
    if state.run_id is not None:
        print(f"Run ID: {state.run_id}", file=sys.stderr)
    if state.log_file is not None:
        print(f"Diagnostic log: {state.log_file}", file=sys.stderr)
    traceback_visible = state.debug and not state.quiet
    if traceback_visible and state.run_id is None:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
    elif not traceback_visible:
        if state.options_parsed:
            if state.debug_option is not None:
                print(
                    f"Re-run with {state.debug_option} for a traceback.",
                    file=sys.stderr,
                )
            else:
                print("Enable debug logging for a traceback.", file=sys.stderr)
        else:
            print("Diagnostic context was unavailable before option parsing completed.", file=sys.stderr)


def _normalize_command_result(result: Any) -> int:
    if result is None:
        return ExitCode.SUCCESS
    if isinstance(result, int):
        return result
    raise TypeError(f"Commands must return None or an int exit code; got {type(result).__name__}.")


def _reject_async_callback(callback: Any) -> None:
    if callback is not None and inspect.iscoroutinefunction(callback):
        raise RuntimeError(_ASYNC_CALLBACK_ERROR)


def _reject_async_result(result: Any) -> Any:
    if inspect.isawaitable(result):
        close = getattr(result, "close", None)
        if callable(close):
            close()
        raise RuntimeError(_ASYNC_CALLBACK_ERROR)
    return result


def _lifecycle_flag_declarations(
    option: LifecycleOption | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if option is None:
        return (), ()
    positive: list[str] = []
    negative: list[str] = []
    for declaration in option.param_decls:
        if declaration.isidentifier():
            continue
        split_char = ";" if declaration.startswith("/") else "/"
        first, separator, second = declaration.partition(split_char)
        positive.extend(option_aliases_from_decls((first.rstrip(),)))
        if separator:
            negative.extend(option_aliases_from_decls((second.lstrip(),)))
    return tuple(positive), tuple(negative)


def _primary_lifecycle_declaration(
    option: LifecycleOption | None,
) -> str | None:
    declarations, _negative_declarations = _lifecycle_flag_declarations(option)
    return next(
        (declaration for declaration in declarations if declaration.startswith("--")),
        declarations[0] if declarations else None,
    )


def _leading_output_flags(
    argv: list[str],
    lifecycle_options: LifecycleOptions,
) -> tuple[bool, bool]:
    debug_positive, debug_negative = (
        set(declarations) for declarations in _lifecycle_flag_declarations(lifecycle_options.debug)
    )
    quiet_positive, quiet_negative = (
        set(declarations) for declarations in _lifecycle_flag_declarations(lifecycle_options.quiet)
    )
    debug = False
    quiet = False
    for token in argv:
        if token in debug_positive:
            debug = True
        elif token in debug_negative:
            debug = False
        elif token in quiet_positive:
            quiet = True
        elif token in quiet_negative:
            quiet = False
        else:
            break
    return debug, quiet


def _effective_invocation_argv(
    app: App,
    args: list[str],
    explicit_argv: bool,
    display_command: str | None,
) -> list[str]:
    if not explicit_argv:
        return list(sys.argv)
    return [display_command or app.name, *args]


def _current_invocation_argv() -> list[str]:
    invocation_argv = _INVOCATION_ARGV.get()
    if invocation_argv is not None:
        return list(invocation_argv)
    return list(sys.argv)


def delegated_display_command(default: str | None = None) -> str | None:
    """Return the wrapper display label or ``default`` when none is set.

    ``BASE_CLI_DISPLAY_COMMAND`` is intended for launchers and delegated
    invocations that need a user-facing command label different from the
    consumer's internal module or entry-point name. Blank environment values
    are ignored, and the returned value can be supplied as a profile's
    ``display_command`` resolver.
    """
    display_command = os.environ.get(DISPLAY_COMMAND_ENV, "").strip()
    if display_command:
        return display_command
    return default


def command(
    *args: Any,
    **kwargs: Any,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    explicit_name = _explicit_command_name(args, kwargs)

    def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
        _reject_async_callback(func)
        with _COMMAND_APP_LOCK:
            if getattr(func, _COMMAND_APP_ATTRIBUTE, None) is not None:
                raise RuntimeError(f"Function '{func.__name__}' is already registered with @base_cli.command().")
            owner = App(name=explicit_name or _inferred_command_name(func))
            registered = owner.command(*args, **kwargs)(func)
            try:
                setattr(func, _COMMAND_APP_ATTRIBUTE, owner)
            except (AttributeError, TypeError) as exc:
                raise TypeError("@base_cli.command() requires a function that can retain its owning App.") from exc
            return registered

    return decorator


def option(
    *param_decls: str,
    sensitive: bool = False,
    dry_run: bool = False,
    **attrs: Any,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
        specs = list(getattr(func, "__base_cli_param_specs__", []))
        specs.append(("option", param_decls, attrs, sensitive))
        typed_func = cast(Any, func)
        typed_func.__base_cli_param_specs__ = specs
        if dry_run:
            dry_run_parameter = parameter_name_from_decls(param_decls)
            existing_dry_run_parameter = getattr(func, "__base_cli_dry_run_parameter__", None)
            if existing_dry_run_parameter is not None:
                raise RuntimeError(
                    f"{func.__name__} already designates '{existing_dry_run_parameter}' as dry-run. "
                    "only one option can be designated dry_run=True."
                )
            typed_func.__base_cli_dry_run_parameter__ = dry_run_parameter
        return func

    return decorator


def argument(
    *param_decls: str,
    sensitive: bool = False,
    **attrs: Any,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
        specs = list(getattr(func, "__base_cli_param_specs__", []))
        specs.append(("argument", param_decls, attrs, sensitive))
        cast(Any, func).__base_cli_param_specs__ = specs
        return func

    return decorator


def _explicit_config_path_type(click: Any) -> Any:
    class ExplicitConfigPath(click.Path):  # type: ignore[misc]
        def convert(self, value: Any, param: Any, ctx: Any) -> Path:
            try:
                expanded = Path(value).expanduser()
            except (RuntimeError, TypeError, ValueError) as exc:
                self.fail(f"Path {value!r} could not be expanded: {exc}", param, ctx)

            converted = super().convert(expanded, param, ctx)
            try:
                mode = converted.stat().st_mode
            except OSError:
                self.fail(f"Path {str(expanded)!r} does not exist.", param, ctx)
            if not stat.S_ISREG(mode):
                self.fail(f"Path {str(expanded)!r} is not a regular file.", param, ctx)
            return cast(Path, converted)

    return ExplicitConfigPath(
        exists=True,
        dir_okay=False,
        readable=True,
        path_type=Path,
    )


def _validate_standard_options(
    click: Any,
    standard: dict[str, Any],
    lifecycle_options: LifecycleOptions,
) -> None:
    if standard.get("debug") and standard.get("quiet"):
        debug = _primary_lifecycle_declaration(lifecycle_options.debug) or "debug"
        quiet = _primary_lifecycle_declaration(lifecycle_options.quiet) or "quiet"
        raise click.UsageError(f"{debug} and {quiet} cannot be used together.")


def _build_group_wrapper(click: Any) -> Callable[..., None]:
    @click.pass_context  # type: ignore[untyped-decorator]
    def group_wrapper(context: Any, **kwargs: Any) -> None:
        del kwargs
        bindings = getattr(
            context.command,
            _CLICK_LIFECYCLE_BINDINGS_ATTRIBUTE,
            {},
        )
        _resolve_lifecycle_values(click, context, bindings)

    return cast(Callable[..., None], group_wrapper)
