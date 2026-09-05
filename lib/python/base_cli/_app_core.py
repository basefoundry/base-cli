from __future__ import annotations

import functools
import inspect
import logging
import stat
import sys
import time
from collections.abc import Awaitable, Callable, Iterable
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
)
from ._private_files import write_private_json
from ._runtime import (
    RuntimeDirectoryError,
    acquire_run_lease,
    create_owned_runtime_directory,
    create_runtime_directory,
    prune_log_files,
    prune_run_bundles,
)
from .asyncio_adapter import run_async
from .attachment import AttachmentContract
from .config import ConfigSnapshot
from .context import Context, recover_current_context, reset_current_context, set_current_context
from .errors import ConfigurationError
from .exit_codes import ExitCode
from .history import compact_optional_path, utc_now
from .integrations import TelemetryOptions, TelemetrySession, finish_telemetry, start_telemetry
from .lifecycle_options import (
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


def _record_lifecycle_diagnostic(context: Context[Any, Any, Any], outcome: InvocationOutcome) -> None:
    try:
        if outcome.kind == "interrupted":
            context.log.warning("Interrupted.")
        elif outcome.kind == "unexpected_error":
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
        self._retention_explicit = retention is not None or any(
            value is not None for value in (max_run_bundles, max_run_age_seconds, max_run_total_bytes)
        )
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

    def async_command(
        self,
        *command_args: Any,
        **command_kwargs: Any,
    ) -> Callable[[Callable[_P, Awaitable[_R]]], Callable[_P, _R]]:
        """Register an async callback through the explicit asyncio adapter.

        The callback remains an ordinary Click command from the lifecycle's
        perspective: ``run_async`` owns one event loop for the invocation,
        waits for the callback, and returns its normal synchronous result for
        exit-code normalization. Native ``@app.command`` callbacks remain
        synchronous and continue to reject unadapted coroutines.
        """

        def decorator(func: Callable[_P, Awaitable[_R]]) -> Callable[_P, _R]:
            if not inspect.iscoroutinefunction(func):
                raise TypeError("@app.async_command() requires an async def callback.")

            @functools.wraps(func)
            def synchronous_callback(*args: _P.args, **kwargs: _P.kwargs) -> _R:
                return run_async(func(*args, **kwargs))

            return self.command(*command_args, **command_kwargs)(synchronous_callback)

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
                    _record_lifecycle_diagnostic(context, outcome)
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
            or "dev"
        )
        log_level = framework_config.log_level if framework_config is not None else None
        debug = bool(standard.get("debug") or log_level == "debug")
        quiet = bool(standard.get("quiet"))
        keep_temp = bool(
            standard.get("keep_temp") or (framework_config.keep_temp if framework_config is not None else None)
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
                context._run_lease = acquire_run_lease(layout.run_root)
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
                if self.retention is not None and (self._retention_explicit or not context.json_output):
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
    context._close_run_lease()


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


# Internal seams are imported after the core definitions so they can share the
# existing state objects without introducing an import cycle.  Public imports
# continue to resolve through ``base_cli.app`` unchanged.
from ._attach import (  # noqa: E402, F401  (late binding avoids a cycle)
    _AttachedLifecycleResource,
    _click_command_has_pending_children,
    _instrument_attached_click_command,
    _instrument_attached_click_main,
    _normalize_attached_option_declaration,
    _normalize_sensitive_parameters,
    _restore_attached_click_command,
    _restore_attached_click_main,
    _selected_click_path,
    _selected_click_paths,
    _with_attached_lifecycle_resource,
)
from ._lifecycle_install import (  # noqa: E402, F401  (late binding avoids a cycle)
    _add_attached_standard_options,
    _capture_lifecycle_option,
    _context_depth,
    _implicit_help_declarations,
    _install_native_lifecycle_options,
    _lifecycle_collision_details,
    _lifecycle_option_attrs,
    _lifecycle_param_decls,
    _make_lifecycle_value_option,
    _make_lifecycle_version_option,
    _missing_adopted_declarations,
    _native_lifecycle_collision_error,
    _normalize_lifecycle_values,
    _normalized_parameter_declaration_sets,
    _normalized_parameter_declarations,
    _parameter_source_rank,
    _prefer_lifecycle_value,
    _reject_duplicate_lifecycle_declarations,
    _reject_implicit_help_collision,
    _resolve_lifecycle_values,
    _standard_options_from_values,
)
from ._run import (  # noqa: E402, F401  (late binding avoids a cycle)
    _current_invocation_argv,
    _effective_invocation_argv,
    _json_requested,
    _leading_output_flags,
    _normalize_command_result,
    _primary_lifecycle_declaration,
    _reject_async_callback,
    _reject_async_result,
    delegated_display_command,
    run_app,
)
