from __future__ import annotations

import functools
import logging
import os
import stat
import sys
import time
import traceback
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Callable

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
)
from .context import Context, recover_current_context, reset_current_context, set_current_context
from .errors import ConfigurationError
from .exit_codes import ExitCode
from .history import utc_now
from .logging import configure_logger, log_invocation
from .paths import (
    current_working_dir,
    normalize_cli_name,
)
from .profile import CliProfile
from .redaction import RedactionPlan, compile_redaction_plan, parameter_name_from_decls, redact_argv

_STANDARD_OPTION_KEYS = ("debug", "quiet", "environment", "config", "keep_temp", "log_file")
_GROUP_STANDARD_OPTIONS_KEY = "base_cli_standard_options"
DISPLAY_COMMAND_ENV = "BASE_CLI_DISPLAY_COMMAND"
_INVOCATION_ARGV: ContextVar[list[str] | None] = ContextVar("base_cli_invocation_argv", default=None)
_COMMAND_APP_ATTRIBUTE = "__base_cli_command_app__"
_COMMAND_APP_LOCK = RLock()
_REGISTRATION_OPEN = "open"
_REGISTRATION_MATERIALIZING = "materializing"
_REGISTRATION_FROZEN = "frozen"
_COMMAND_NAME_SUFFIXES = frozenset({"command", "cmd", "group", "grp"})


@dataclass
class _InvocationState:
    run_id: str | None = None
    log_file: Path | None = None
    debug: bool = False
    quiet: bool = False
    options_parsed: bool = False


@dataclass(frozen=True)
class _SubcommandRegistration:
    func: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    name: str


_INVOCATION_STATE: ContextVar[_InvocationState | None] = ContextVar("base_cli_invocation_state", default=None)


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


def _warn_lifecycle_failure(context: Context, message: str, exc: BaseException) -> None:
    """Report a secondary lifecycle failure without breaking teardown."""
    try:
        detail = str(exc) or type(exc).__name__
        context.log.warning("%s: %s", message, detail)
    except BaseException:  # pylint: disable=broad-exception-caught
        pass


def _capture_invocation_context(context: Context) -> None:
    state = _INVOCATION_STATE.get()
    if state is None:
        return
    state.run_id = context.run_id
    state.log_file = context.log_file
    state.debug = context.debug
    state.quiet = context.quiet


def _capture_standard_options(standard: dict[str, Any]) -> None:
    state = _INVOCATION_STATE.get()
    if state is None:
        return
    state.debug = bool(standard.get("debug"))
    state.quiet = bool(standard.get("quiet"))
    state.options_parsed = True


def _capture_effective_output_options(*, debug: bool, quiet: bool) -> None:
    state = _INVOCATION_STATE.get()
    if state is None:
        return
    state.debug = debug
    state.quiet = quiet


def _record_unexpected_traceback(context: Context, outcome: InvocationOutcome) -> None:
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


def _reset_active_context(context: Context, token: Any) -> None:
    try:
        reset_current_context(token)
    except BaseException as exc:  # pylint: disable=broad-exception-caught
        _warn_lifecycle_failure(context, "Active context reset failed", exc)
        try:
            recover_current_context(token)
        except BaseException:  # pylint: disable=broad-exception-caught
            pass


def _require_click():
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
    return click.command(name, *args_after_name, **attrs)


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
    ) -> None:
        if max_log_files is not None and max_log_files < 1:
            raise ValueError("max_log_files must be greater than 0 when set.")
        self._registration_lock = RLock()
        self._registration_state = _REGISTRATION_OPEN
        self._name = normalize_cli_name(name or sys.argv[0])
        self.version = version
        self.help = help
        self.log_to_file = log_to_file
        self.max_log_files = max_log_files
        # Standalone applications must not inherit a consumer's product
        # conventions. Consumers with product-specific policies should pass an
        # explicit profile.
        self.profile = profile or CliProfile.generic()
        self._click_command = None
        self._redaction_plan: RedactionPlan | None = None
        self._command_func: Callable[..., Any] | None = None
        self._command_args: tuple[Any, ...] = ()
        self._command_kwargs: dict[str, Any] = {}
        self._subcommands: list[_SubcommandRegistration] = []
        self._subcommand_names: set[str] = set()

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
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

    def _ensure_registration_open(self) -> None:
        if self._registration_state == _REGISTRATION_MATERIALIZING:
            raise RuntimeError(
                f"App '{self.name}' registration is unavailable while its Click command "
                "is being materialized."
            )
        if self._registration_state == _REGISTRATION_FROZEN:
            raise RuntimeError(
                f"App '{self.name}' registration is frozen because its Click command "
                "has already been materialized."
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

    def command(self, *command_args: Any, **command_kwargs: Any):
        with self._registration_lock:
            self._ensure_registration_open()
            self._validate_single_command_name(command_args, command_kwargs)

        def decorator(func: Callable[..., Any]):
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

    def subcommand(self, *command_args: Any, **command_kwargs: Any):
        with self._registration_lock:
            self._ensure_registration_open()
            _explicit_command_name(command_args, command_kwargs)

        def decorator(func: Callable[..., Any]):
            with self._registration_lock:
                self._ensure_registration_open()
                if self._command_func is not None:
                    raise RuntimeError(
                        f"App '{self.name}' already has a registered command. "
                        "Use either @app.command() or @app.subcommand(), not both."
                    )
                name = _resolved_command_name(func, command_args, command_kwargs)
                if name in self._subcommand_names:
                    raise RuntimeError(
                        f"App '{self.name}' already has a registered subcommand named '{name}'."
                    )
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
                raise RuntimeError(
                    f"App '{self.name}' Click command materialization is already in progress."
                )

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
            wrapper = self._build_command_wrapper(click, self._command_func, include_version=True)
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
            return command

        group_wrapper = _decorate_standard_options(click, _build_group_wrapper(click), self.version)
        group = click.group(name=self.name, help=self.help)(group_wrapper)
        for registration in self._subcommands:
            wrapper = self._build_command_wrapper(click, registration.func, include_version=False)
            command = _click_command_decorator(
                click,
                registration.name,
                registration.args,
                registration.kwargs,
            )(wrapper)
            _require_materialized_command_name(command, registration.name, self.name)
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
        include_version: bool,
    ) -> Callable[..., Any]:
        dry_run_parameter = getattr(func, "__base_cli_dry_run_parameter__", "dry_run")

        @functools.wraps(func)
        def wrapper(**kwargs: Any):
            standard = _merge_standard_options(
                _group_standard_options(click),
                _pop_standard_options(kwargs),
            )
            _validate_standard_options(click, standard)
            _capture_standard_options(standard)
            started_at = utc_now()
            started_monotonic_ns = time.monotonic_ns()
            context: Context | None = None
            recorder: RunRecorder | None = None
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
                        dry_run=bool(kwargs.get(dry_run_parameter)),
                    )
                except ConfigurationError as exc:
                    raise click.UsageError(str(exc)) from exc
                except RuntimeDirectoryError as exc:
                    raise click.ClickException(str(exc)) from exc

                recorder = RunRecorder(context, started_at, started_monotonic_ns)
                token = set_current_context(context)
                _capture_invocation_context(context)
                invocation_argv = redact_argv(_current_invocation_argv(), redaction_plan)
                _start_run_recorder(recorder)
                log_invocation(context.log, invocation_argv, None)
                if context.project_root is not None:
                    context.log.debug("project_root=%s", context.project_root)
                if context.manifest_path is not None:
                    context.log.debug("manifest_path=%s", context.manifest_path)
                result = func(context, **kwargs)
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
        wrapper = _decorate_standard_options(click, wrapper, self.version if include_version else None)
        return wrapper

    def _create_context(self, standard: dict[str, Any], dry_run: bool = False) -> Context:
        project = self.profile.discover_project(current_working_dir())
        manifest_path = project.manifest if project is not None else None
        explicit_config = Path(standard["config"]).expanduser() if standard.get("config") else None
        user_config = self.profile.load_user_config()
        workspace_root = self.profile.resolve_workspace_root(user_config)
        config = self.profile.load_config(project, explicit_config)

        environment = standard.get("environment") or config.get("environment") or "dev"
        debug = bool(standard.get("debug") or str(config.get("log_level", "")).lower() == "debug")
        quiet = bool(standard.get("quiet"))
        keep_temp = bool(standard.get("keep_temp") or config.get("keep_temp"))
        _capture_effective_output_options(debug=debug, quiet=quiet)

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
        context = Context(
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
                context.log = configure_logger(self.name, log_file, debug, quiet=quiet)
            except OSError as exc:
                target = f"persistent log file '{log_file}'" if log_file is not None else "stderr logging"
                raise RuntimeDirectoryError(f"Unable to configure {target}: {exc}") from exc
            context.log.debug("cli=%s run_id=%s environment=%s", self.name, run_id, environment)
            if self.max_log_files is not None and uses_default_log_file and log_file is not None:
                prune_log_files(layout.owner_root / "runs", log_file, self.max_log_files, context.log)

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
                                "project_root": str(selected_project_root),
                                "manifest": str(manifest_path) if manifest_path is not None else None,
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
    context: Context,
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


def get_command_app(command_func: Callable[..., Any]) -> App:
    """Return the isolated :class:`App` owned by ``@base_cli.command``."""

    with _COMMAND_APP_LOCK:
        owner = getattr(command_func, _COMMAND_APP_ATTRIBUTE, None)
        if isinstance(owner, App):
            with owner._registration_lock:  # pylint: disable=protected-access
                if owner._command_func is command_func:  # pylint: disable=protected-access
                    return owner
    raise TypeError(
        "Expected a base_cli.App or a function registered with @base_cli.command()."
    )


def run_app(
    app: App | Callable[..., Any],
    argv: list[str] | None = None,
    *,
    reraise_unexpected: bool = False,
) -> int:
    """Run an :class:`App` or registered command and return its process exit code."""

    if not isinstance(app, App):
        app = get_command_app(app)

    try:
        click = _require_click()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return ExitCode.FAILURE

    explicit_argv = argv is not None
    args = list(sys.argv[1:] if argv is None else argv)
    leading_debug, leading_quiet = _leading_output_flags(args)
    state = _InvocationState(debug=leading_debug, quiet=leading_quiet)
    state_token = _INVOCATION_STATE.set(state)
    try:
        try:
            display_command = app.profile.display_command()
            invocation_argv = _effective_invocation_argv(app, args, explicit_argv, display_command)
            invocation_token = _INVOCATION_ARGV.set(invocation_argv)
            try:
                result = app.click_command.main(
                    args=args,
                    prog_name=display_command or app.name,
                    standalone_mode=False,
                )
            finally:
                _reset_context_var(_INVOCATION_ARGV, invocation_token)
        except click.Abort as exc:
            outcome = outcome_from_exception(click, exc)
            if outcome.kind == "interrupted":
                print("Interrupted.", file=sys.stderr)
            else:
                print("Aborted!", file=sys.stderr)
            return outcome.exit_code
        except click.ClickException as exc:
            outcome = outcome_from_exception(click, exc)
            if outcome.kind == "unexpected_error":
                if reraise_unexpected:
                    raise
                _show_unexpected_error(state, exc)
                return outcome.exit_code
            exc.show()
            return outcome.exit_code
        except KeyboardInterrupt:
            print("Interrupted.", file=sys.stderr)
            return ExitCode.INTERRUPTED
        except SystemExit as exc:
            if exc.code is not None and not isinstance(exc.code, int):
                print(str(exc.code), file=sys.stderr)
            return system_exit_code(exc)
        except Exception as exc:
            if reraise_unexpected:
                raise
            _show_unexpected_error(state, exc)
            return ExitCode.FAILURE

        try:
            return _normalize_command_result(result)
        except TypeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return ExitCode.FAILURE
    finally:
        _reset_context_var(_INVOCATION_STATE, state_token)


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
            print("Re-run with --debug for a traceback.", file=sys.stderr)
        else:
            print("Diagnostic context was unavailable before option parsing completed.", file=sys.stderr)


def _normalize_command_result(result: Any) -> int:
    if result is None:
        return ExitCode.SUCCESS
    if isinstance(result, int):
        return result
    raise TypeError(
        "Commands must return None or an int exit code; "
        f"got {type(result).__name__}."
    )


def _leading_output_flags(argv: list[str]) -> tuple[bool, bool]:
    debug = False
    quiet = False
    for token in argv:
        if token == "--debug":
            debug = True
        elif token in ("--quiet", "-q"):
            quiet = True
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
    display_command = os.environ.get(DISPLAY_COMMAND_ENV, "").strip()
    if display_command:
        return display_command
    return default


def command(*args: Any, **kwargs: Any):
    explicit_name = _explicit_command_name(args, kwargs)

    def decorator(func: Callable[..., Any]):
        with _COMMAND_APP_LOCK:
            if getattr(func, _COMMAND_APP_ATTRIBUTE, None) is not None:
                raise RuntimeError(
                    f"Function '{func.__name__}' is already registered with "
                    "@base_cli.command()."
                )
            owner = App(name=explicit_name or _inferred_command_name(func))
            registered = owner.command(*args, **kwargs)(func)
            try:
                setattr(func, _COMMAND_APP_ATTRIBUTE, owner)
            except (AttributeError, TypeError) as exc:
                raise TypeError(
                    "@base_cli.command() requires a function that can retain its owning App."
                ) from exc
            return registered

    return decorator


def option(*param_decls: str, sensitive: bool = False, dry_run: bool = False, **attrs: Any):
    def decorator(func: Callable[..., Any]):
        specs = list(getattr(func, "__base_cli_param_specs__", []))
        specs.append(("option", param_decls, attrs, sensitive))
        func.__base_cli_param_specs__ = specs
        if dry_run:
            dry_run_parameter = parameter_name_from_decls(param_decls)
            existing_dry_run_parameter = getattr(func, "__base_cli_dry_run_parameter__", None)
            if existing_dry_run_parameter is not None:
                raise RuntimeError(
                    f"{func.__name__} already designates '{existing_dry_run_parameter}' as dry-run. "
                    "only one option can be designated dry_run=True."
                )
            func.__base_cli_dry_run_parameter__ = dry_run_parameter
        return func

    return decorator


def argument(*param_decls: str, sensitive: bool = False, **attrs: Any):
    def decorator(func: Callable[..., Any]):
        specs = list(getattr(func, "__base_cli_param_specs__", []))
        specs.append(("argument", param_decls, attrs, sensitive))
        func.__base_cli_param_specs__ = specs
        return func

    return decorator


def _decorate_standard_options(click: Any, func: Callable[..., Any], version: str | None):
    func = click.option("--log-file", type=click.Path(dir_okay=False), help="Override the persistent log file.")(func)
    func = click.option("--keep-temp", is_flag=True, default=None, help="Preserve this run's temp directory.")(func)
    func = click.option(
        "--config",
        type=_explicit_config_path_type(click),
        help="Load an additional config file.",
    )(func)
    func = click.option("--environment", help="Set the CLI environment.")(func)
    func = click.option(
        "--debug",
        is_flag=True,
        default=None,
        help="Enable DEBUG logging on the user-facing stream.",
    )(func)
    func = click.option(
        "--quiet",
        "-q",
        is_flag=True,
        default=None,
        help="Suppress INFO logs on the user-facing stream.",
    )(func)
    if version is not None:
        func = click.version_option(version)(func)
    return func


def _explicit_config_path_type(click: Any) -> Any:
    class ExplicitConfigPath(click.Path):
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
            return converted

    return ExplicitConfigPath(
        exists=True,
        dir_okay=False,
        readable=True,
        path_type=Path,
    )


def _pop_standard_options(kwargs: dict[str, Any]) -> dict[str, Any]:
    standard = {}
    for key in _STANDARD_OPTION_KEYS:
        standard[key] = kwargs.pop(key, None)
    return standard


def _merge_standard_options(group_standard: dict[str, Any], command_standard: dict[str, Any]) -> dict[str, Any]:
    merged = {}
    for key in _STANDARD_OPTION_KEYS:
        value = command_standard.get(key)
        merged[key] = group_standard.get(key) if value is None else value
    return merged


def _validate_standard_options(click: Any, standard: dict[str, Any]) -> None:
    if standard.get("debug") and standard.get("quiet"):
        raise click.UsageError("--debug and --quiet cannot be used together.")


def _group_standard_options(click: Any) -> dict[str, Any]:
    context = click.get_current_context(silent=True)
    parent = context.parent if context is not None else None
    if parent is None or not isinstance(parent.obj, dict):
        return {}
    standard = parent.obj.get(_GROUP_STANDARD_OPTIONS_KEY)
    return dict(standard) if isinstance(standard, dict) else {}


def _build_group_wrapper(click: Any) -> Callable[..., None]:
    @click.pass_context
    def group_wrapper(context: Any, **kwargs: Any) -> None:
        obj = dict(context.obj) if isinstance(context.obj, dict) else {}
        obj[_GROUP_STANDARD_OPTIONS_KEY] = _pop_standard_options(kwargs)
        context.obj = obj

    return group_wrapper
