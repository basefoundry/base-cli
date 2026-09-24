"""Production invocation runner and JSON/error rendering helpers."""

from __future__ import annotations

import inspect
import io
import os
import sys
import tempfile
import traceback
from collections.abc import Callable, Mapping
from contextlib import redirect_stdout
from threading import Lock
from typing import Any, TextIO, cast

from ._app_core import (
    _ASYNC_CALLBACK_ERROR,
    _INVOCATION_ARGV,
    _INVOCATION_MAIN_BYPASS,
    _INVOCATION_STATE,
    DISPLAY_COMMAND_ENV,
    App,
    _InvocationState,
    _require_click,
    _reset_context_var,
    get_command_app,
)
from ._click_compat import dialect_for_command
from ._lifecycle import InvocationOutcome, outcome_from_exception, outcome_from_exit_code, system_exit_code
from .exit_codes import ExitCode
from .json_contracts import dumps_envelope, error_envelope, success_envelope
from .lifecycle_options import LifecycleOption, LifecycleOptions
from .output import OutputFormatError
from .redaction import option_aliases_from_decls

_MAX_JSON_CAPTURE_BYTES = 8 * 1_048_576
_RUN_APP_LOCK = Lock()


class JsonCaptureLimitError(RuntimeError):
    """Raised when a JSON invocation exceeds its bounded stdout contract."""


class _BoundedJsonCapture(io.TextIOBase):
    """Text stream that bounds UTF-8 output before it reaches the spool."""

    encoding = "utf-8"
    errors = "strict"

    def __init__(self, limit_bytes: int) -> None:
        super().__init__()
        self._limit_bytes = limit_bytes
        self._bytes_written = 0
        self._stream = cast(
            TextIO,
            tempfile.SpooledTemporaryFile(
                max_size=min(limit_bytes, 1_048_576),
                mode="w+",
                encoding="utf-8",
                newline="",
            ),
        )

    def write(self, value: str) -> int:
        encoded_size = len(value.encode("utf-8"))
        if self._bytes_written + encoded_size > self._limit_bytes:
            raise JsonCaptureLimitError(
                f"JSON stdout exceeded the {_format_bytes(self._limit_bytes)} limit; use NDJSON for large record sets."
            )
        written = self._stream.write(value)
        self._bytes_written += encoded_size
        return written

    def flush(self) -> None:
        self._stream.flush()

    def read(self, size: int | None = -1) -> str:
        return self._stream.read(-1 if size is None else size)

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        return self._stream.seek(offset, whence)

    def tell(self) -> int:
        return self._stream.tell()

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._stream.close()


class _DeferredJsonCapture(io.TextIOBase):
    """Buffer parser-time stdout until Click resolves the lifecycle output mode."""

    encoding = "utf-8"
    errors = "strict"

    def __init__(self, stream: TextIO, limit_bytes: int) -> None:
        super().__init__()
        self._stdout = stream
        self._limit_bytes = limit_bytes
        self._bytes_written = 0
        self._mode: bool | None = None
        self._stream = cast(
            TextIO,
            tempfile.SpooledTemporaryFile(
                max_size=min(limit_bytes, 1_048_576),
                mode="w+",
                encoding="utf-8",
                newline="",
            ),
        )

    @property
    def pending(self) -> bool:
        return self._mode is None

    @property
    def json_output(self) -> bool:
        return self._mode is True

    def resolve_json_output(self, enabled: bool) -> None:
        if self._mode is not None:
            if self._mode == enabled:
                return
            if not self._mode and enabled:
                raise RuntimeError("JSON output mode was resolved after stdout had been released.")
        if enabled:
            self._mode = True
            if self._bytes_written > self._limit_bytes:
                raise JsonCaptureLimitError(
                    f"JSON stdout exceeded the {_format_bytes(self._limit_bytes)} limit; use NDJSON for large record sets."
                )
            return

        self._stream.flush()
        self._stream.seek(0)
        while chunk := self._stream.read(64 * 1024):
            self._stdout.write(chunk)
        self._stdout.flush()
        self._mode = False

    def write(self, value: str) -> int:
        if self._mode is False:
            return self._stdout.write(value)
        encoded_size = len(value.encode("utf-8"))
        if self._mode is True and self._bytes_written + encoded_size > self._limit_bytes:
            raise JsonCaptureLimitError(
                f"JSON stdout exceeded the {_format_bytes(self._limit_bytes)} limit; use NDJSON for large record sets."
            )
        written = self._stream.write(value)
        self._bytes_written += encoded_size
        return written

    def flush(self) -> None:
        if self._mode is False:
            self._stdout.flush()
        else:
            self._stream.flush()

    def read(self, size: int | None = -1) -> str:
        return self._stream.read(-1 if size is None else size)

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        return self._stream.seek(offset, whence)

    def tell(self) -> int:
        return self._stream.tell()

    def isatty(self) -> bool:
        if self._mode is True:
            return False
        try:
            return bool(self._stdout.isatty())
        except (AttributeError, OSError, ValueError):
            return False

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._stream.close()


def _format_bytes(value: int) -> str:
    if value % 1_048_576 == 0:
        return f"{value // 1_048_576} MiB"
    return f"{value} bytes"


def run_app(
    app: App | Callable[..., Any],
    argv: list[str] | None = None,
    *,
    reraise_unexpected: bool = False,
) -> int:
    """Run an App, registered command, or attached Click tree and return its status.

    ``run_app`` is a process-wide boundary: recursive or concurrent calls fail
    fast before entering Click or changing stdout and logging handlers.
    """

    if not isinstance(app, App):
        app = get_command_app(app)

    if not _RUN_APP_LOCK.acquire(blocking=False):
        active_state = _INVOCATION_STATE.get()
        if active_state is not None:
            identity = getattr(active_state.owner_app, "name", app.name)
            _emit_run_rejection(
                active_state,
                f"Nested run_app() for CLI identity '{identity}' is not supported; "
                "call the command logic directly instead.",
            )
        else:
            state = _InvocationState(
                owner_app=app,
                json_output=_json_requested(
                    list(sys.argv[1:] if argv is None else argv),
                    app.lifecycle_options,
                ),
            )
            _emit_run_rejection(
                state,
                "Concurrent run_app() calls in one process are not supported; "
                "invoke each CLI in a separate process or serialize calls.",
            )
        return ExitCode.FAILURE

    try:
        return _run_app_invocation(
            app,
            argv,
            reraise_unexpected=reraise_unexpected,
        )
    finally:
        _RUN_APP_LOCK.release()


def _run_app_invocation(
    app: App,
    argv: list[str] | None = None,
    *,
    reraise_unexpected: bool = False,
) -> int:
    """Run one process-wide invocation after acquiring the output/runtime boundary."""

    try:
        click = _require_click()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return ExitCode.FAILURE

    args = list(sys.argv[1:] if argv is None else argv)
    command = app.click_command
    click = dialect_for_command(command)
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
    )
    state_token = _INVOCATION_STATE.set(state)
    output_capture: TextIO | None = None
    try:
        try:
            display_command = app.profile.display_command()
            invocation_argv = _effective_invocation_argv(app, args, display_command)
            json_decision = _json_requested(
                args,
                app.lifecycle_options,
                default_map=_command_default_map(command),
                command=command,
                prog_name=display_command or app.name,
            )
            state.json_output = bool(json_decision)
            invocation_token = _INVOCATION_ARGV.set(invocation_argv)
            try:
                bypass_token = _INVOCATION_MAIN_BYPASS.set(command)
                if json_decision is None:
                    output_capture = cast(TextIO, _DeferredJsonCapture(sys.stdout, _MAX_JSON_CAPTURE_BYTES))
                    state.output_router = output_capture
                else:
                    # Human and NDJSON paths retain the real stdout stream;
                    # only a resolved JSON invocation uses the bounded spool.
                    output_capture = _new_json_capture() if state.json_output else None
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
                    if isinstance(output_capture, _DeferredJsonCapture):
                        if output_capture.pending:
                            output_capture.resolve_json_output(state.json_output)
                        if not output_capture.json_output:
                            output_capture = None
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
        except JsonCaptureLimitError as exc:
            if state.json_output:
                outcome = InvocationOutcome("capture_limit", "error", ExitCode.FAILURE)
                _emit_json_error(state, outcome, str(exc), output_capture)
                return outcome.exit_code
            raise
        except OutputFormatError as exc:
            if reraise_unexpected:
                raise
            outcome = InvocationOutcome("output_format_error", "error", ExitCode.USAGE_ERROR)
            if state.json_output:
                _emit_json_error(state, outcome, str(exc), output_capture)
            else:
                print(f"Error: {exc}", file=sys.stderr)
            return outcome.exit_code
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
        if output_capture is not None:
            output_capture.close()
        _reset_context_var(_INVOCATION_STATE, state_token)


def _json_requested(
    args: list[str],
    lifecycle_options: LifecycleOptions,
    *,
    default_map: Mapping[str, Any] | None = None,
    command: Any | None = None,
    prog_name: str | None = None,
) -> bool | None:
    option = lifecycle_options.json
    if option is None:
        return False

    if command is not None:
        return _click_lifecycle_value(
            command,
            args,
            option,
            prog_name,
            default_map=default_map,
        )

    positive_declarations, negative_declarations = _lifecycle_flag_declarations(option)
    explicit_value: bool | None = None
    for argument in args:
        if argument == "--":
            break
        explicit_value = _explicit_lifecycle_value(
            argument,
            positive_declarations,
            negative_declarations,
            explicit_value,
        )
    if explicit_value is not None:
        return explicit_value

    if option.envvar is not None:
        envvars = (option.envvar,) if isinstance(option.envvar, str) else option.envvar
        if any(os.environ.get(name, "").lower() in {"1", "true", "yes", "on"} for name in envvars):
            return True
    if default_map is not None:
        key = option.name or _option_destination(option)
        value = default_map.get(key)
        if isinstance(value, bool):
            return value
    if callable(option.default):
        return None
    return option.default is True


def _click_lifecycle_value(
    command: Any,
    args: list[str],
    option: LifecycleOption,
    prog_name: str | None,
    *,
    default_map: Mapping[str, Any] | None = None,
) -> bool | None:
    """Inspect Click's raw parsers without processing parameter values.

    ``make_context`` is intentionally avoided: parsing a temporary context
    executes user parameter types, defaults, callbacks, lazy group resolvers,
    and close hooks. The low-level parser only tokenizes values and option
    arity, which is enough to decide whether stdout needs JSON capture.
    """

    click = dialect_for_command(command)
    destination = option.name or _option_destination(option)
    selected: tuple[int, int, bool] | None = None
    unresolved: tuple[int, int] | None = None
    current_command = command
    current_args = list(args)
    parent_context: Any | None = None
    info_name = prog_name or getattr(command, "name", None) or "cli"
    depth = 0
    try:
        for _ in range(64):
            context_settings = dict(getattr(current_command, "context_settings", None) or {})
            context_settings.update(
                resilient_parsing=True,
                allow_extra_args=True,
                ignore_unknown_options=True,
            )
            if parent_context is None and default_map is not None:
                context_settings["default_map"] = default_map
            context = click.Context(
                current_command,
                parent=parent_context,
                info_name=info_name,
                **context_settings,
            )
            parameters = current_command.get_params(context)
            parser = current_command.make_parser(context)
            parsed_values, remaining, _parameter_order = parser.parse_args(list(current_args))
            parameter = next((candidate for candidate in parameters if candidate.name == destination), None)

            if parameter is not None:
                parsed_value = parsed_values.get(destination)
                if isinstance(parsed_value, bool):
                    selected = _prefer_json_value(selected, 4, depth, parsed_value)

                try:
                    environment_value = parameter.value_from_envvar(context)
                    if environment_value is not None:
                        converted = parameter.type_cast_value(context, environment_value)
                        if isinstance(converted, bool):
                            selected = _prefer_json_value(selected, 3, depth, converted)
                except Exception:
                    # The real Click parse owns diagnostics for malformed
                    # environment values; preflight only needs a safe hint.
                    pass

                parameter_default = getattr(parameter, "default", None)
                if callable(parameter_default):
                    unresolved = _prefer_json_source(unresolved, 1, depth)
                elif isinstance(parameter_default, bool) or parameter_default is None:
                    selected = _prefer_json_value(
                        selected,
                        1,
                        depth,
                        bool(parameter_default),
                    )

            context_default_map = getattr(context, "default_map", None)
            if isinstance(context_default_map, Mapping) and destination in context_default_map:
                mapped_value = context_default_map[destination]
                if isinstance(mapped_value, bool):
                    selected = _prefer_json_value(selected, 2, depth, mapped_value)
                elif callable(mapped_value):
                    unresolved = _prefer_json_source(unresolved, 2, depth)

            if not remaining or not isinstance(getattr(current_command, "commands", None), Mapping):
                break
            command_name = remaining[0]
            commands = current_command.commands
            next_command = commands.get(command_name)
            normalize = getattr(context, "token_normalize_func", None)
            if next_command is None and callable(normalize):
                next_command = commands.get(normalize(command_name))
            if next_command is None:
                # A lazy group may resolve this name during the real dispatch.
                # Defer only successful stdout until Click supplies the actual
                # lifecycle value; never call get_command speculatively.
                resolver = getattr(type(current_command), "get_command", None)
                group_type = getattr(click, "Group", None)
                base_resolver = getattr(group_type, "get_command", None)
                if callable(resolver) and resolver is not base_resolver:
                    unresolved = _prefer_json_source(unresolved, 4, depth + 1)
                break
            parent_context = context
            current_command = next_command
            current_args = remaining[1:]
            info_name = normalize(command_name) if callable(normalize) else command_name
            depth += 1
    except (Exception, SystemExit):
        # The real parser owns malformed-command diagnostics. Keep the best
        # known source without running consumer parsing hooks here.
        pass

    if unresolved is not None and (selected is None or unresolved >= selected[:2]):
        return None
    return False if selected is None else selected[2]


def _prefer_json_value(
    selected: tuple[int, int, bool] | None,
    rank: int,
    depth: int,
    value: bool,
) -> tuple[int, int, bool]:
    candidate = (rank, depth, value)
    return candidate if selected is None or candidate[:2] >= selected[:2] else selected


def _prefer_json_source(
    selected: tuple[int, int] | None,
    rank: int,
    depth: int,
) -> tuple[int, int]:
    candidate = (rank, depth)
    return candidate if selected is None or candidate >= selected else selected


def _option_destination(option: LifecycleOption) -> str:
    for declaration in option.param_decls:
        if declaration.startswith("--"):
            return declaration[2:].split("/", 1)[0].replace("-", "_")
    return ""


def _command_default_map(command: Any) -> Mapping[str, Any] | None:
    settings = getattr(command, "context_settings", None)
    if not isinstance(settings, Mapping):
        return None
    default_map = settings.get("default_map")
    return default_map if isinstance(default_map, Mapping) else None


def _new_json_capture() -> TextIO:
    return cast(TextIO, _BoundedJsonCapture(_MAX_JSON_CAPTURE_BYTES))


def _emit_run_rejection(state: _InvocationState, message: str) -> None:
    if state.json_output:
        _emit_json_error(
            state,
            InvocationOutcome("invocation_rejected", "error", ExitCode.FAILURE),
            message,
            None,
        )
        return
    print(f"ERROR: {message}", file=sys.stderr)


def _explicit_lifecycle_value(
    argument: str,
    positive_declarations: tuple[str, ...],
    negative_declarations: tuple[str, ...],
    current: bool | None,
) -> bool | None:
    """Apply one raw token using the option grammar needed before Click parses."""

    for declaration in positive_declarations:
        if argument == declaration or argument.startswith(f"{declaration}="):
            current = True
    for declaration in negative_declarations:
        if argument == declaration or argument.startswith(f"{declaration}="):
            current = False

    prefix = argument[:1]
    if prefix not in "-+/" or argument.startswith(f"{prefix}{prefix}"):
        return current

    positive_short = {declaration for declaration in positive_declarations if len(declaration) == 2}
    negative_short = {declaration for declaration in negative_declarations if len(declaration) == 2}
    for character in argument[1:]:
        if character == "=":
            break
        declaration = f"{prefix}{character}"
        if declaration in positive_short:
            current = True
        elif declaration in negative_short:
            current = False
    return current


def _captured_stdout(output_capture: TextIO | None) -> str:
    if output_capture is None:
        return ""
    position = output_capture.tell()
    output_capture.seek(0)
    value = output_capture.read()
    output_capture.seek(position)
    return value


def _emit_json_success(
    state: _InvocationState,
    exit_code: int,
    output_capture: TextIO | None,
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
    output_capture: TextIO | None,
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
    if isinstance(result, bool):
        raise TypeError("Commands must return None or an int exit code from 0 through 255; bool is not an exit code.")
    if isinstance(result, int) and 0 <= result <= 255:
        return result
    if isinstance(result, int):
        raise TypeError(f"Commands must return None or an int exit code from 0 through 255; got {result}.")
    raise TypeError(f"Commands must return None or an int exit code from 0 through 255; got {type(result).__name__}.")


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
    display_command: str | None,
) -> list[str]:
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


__all__ = [
    "run_app",
    "_json_requested",
    "_captured_stdout",
    "_emit_json_success",
    "_emit_json_error",
    "_show_unexpected_error",
    "_normalize_command_result",
    "_reject_async_callback",
    "_reject_async_result",
    "_lifecycle_flag_declarations",
    "_primary_lifecycle_declaration",
    "_leading_output_flags",
    "_effective_invocation_argv",
    "_current_invocation_argv",
    "delegated_display_command",
]
