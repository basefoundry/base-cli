"""Production invocation runner and JSON/error rendering helpers."""

from __future__ import annotations

import inspect
import os
import sys
import tempfile
import traceback
from collections.abc import Callable, Mapping
from contextlib import redirect_stdout
from typing import Any, TextIO, cast

from ._app_core import (
    _ASYNC_CALLBACK_ERROR,
    _INVOCATION_ARGV,
    _INVOCATION_MAIN_BYPASS,
    _INVOCATION_STATE,
    _LIFECYCLE_CAPTURE_META_KEY,
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
from .redaction import option_aliases_from_decls

_MAX_JSON_CAPTURE_BYTES = 1_048_576


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
    output_capture: TextIO | None = None
    try:
        try:
            display_command = app.profile.display_command()
            invocation_argv = _effective_invocation_argv(app, args, display_command)
            command = app.click_command
            click = dialect_for_command(command)
            if not state.json_output:
                state.json_output = _json_requested(
                    args,
                    app.lifecycle_options,
                    default_map=_command_default_map(command),
                    command=command,
                    prog_name=display_command or app.name,
                )
            invocation_token = _INVOCATION_ARGV.set(invocation_argv)
            try:
                bypass_token = _INVOCATION_MAIN_BYPASS.set(command)
                # Capture only an active JSON invocation. Human and NDJSON
                # paths retain the real stdout stream and its flush behavior.
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
) -> bool:
    option = lifecycle_options.json
    if option is None:
        return False

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

    # Once the command object is available, let Click resolve the option. Its
    # parser knows about auto_envvar_prefix, nested default maps, callable
    # defaults, and the complete boolean environment grammar (including `t`
    # and `y`). This is used only for the pre-invocation capture decision; the
    # real command is still parsed and invoked exactly once below.
    if command is not None:
        click_value = _click_lifecycle_value(command, args, option, prog_name)
        if click_value is not None:
            return click_value

    if option.envvar is not None:
        envvars = (option.envvar,) if isinstance(option.envvar, str) else option.envvar
        if any(os.environ.get(name, "").lower() in {"1", "true", "yes", "on"} for name in envvars):
            return True
    if default_map is not None:
        key = option.name or _option_destination(option)
        value = default_map.get(key)
        if isinstance(value, bool):
            return value
    return option.default is True


def _click_lifecycle_value(
    command: Any,
    args: list[str],
    option: LifecycleOption,
    prog_name: str | None,
) -> bool | None:
    """Resolve a lifecycle flag with the owning Click command parser."""

    contexts: list[Any] = []
    current_command = command
    current_args = list(args)
    current_context: Any | None = None
    try:
        current_context = command.make_context(
            prog_name,
            current_args,
            resilient_parsing=True,
        )
        contexts.append(current_context)
        while current_args:
            resolve_command = getattr(current_command, "resolve_command", None)
            if not callable(resolve_command):
                break
            command_name, next_command, remaining = resolve_command(
                current_context,
                _remaining_context_args(current_context),
            )
            if command_name is None or next_command is None:
                break
            next_context = next_command.make_context(
                command_name,
                remaining,
                parent=current_context,
                resilient_parsing=True,
            )
            contexts.append(next_context)
            current_command = next_command
            current_context = next_context
            current_args = _remaining_context_args(current_context)

        destination = option.name or _option_destination(option)
        for context in reversed(contexts):
            params = getattr(context, "params", {})
            value = params.get(destination) if isinstance(params, Mapping) else None
            if isinstance(value, bool):
                return value
            context_default_map = getattr(context, "default_map", None)
            if isinstance(context_default_map, Mapping):
                mapped_value = context_default_map.get(destination)
                if isinstance(mapped_value, bool):
                    return mapped_value
            meta = getattr(context, "meta", {})
            captures = meta.get(_LIFECYCLE_CAPTURE_META_KEY) if isinstance(meta, Mapping) else None
            if isinstance(captures, Mapping):
                for captured in captures.values():
                    if not isinstance(captured, Mapping):
                        continue
                    raw = captured.get("json")
                    raw_value = getattr(raw, "value", None)
                    if isinstance(raw_value, bool):
                        return raw_value
    except (Exception, SystemExit):
        # Invalid command lines still need the lightweight explicit-token
        # detector above so Click can render its normal machine error. A
        # resilient parse may not be able to resolve a leaf command; in that
        # case retain the existing fallback behavior.
        return None
    finally:
        for context in reversed(contexts):
            close = getattr(context, "close", None)
            if callable(close):
                close()
    return None


def _remaining_context_args(context: Any) -> list[str]:
    """Return unparsed group/command arguments without Click deprecation warnings."""

    values = getattr(context, "__dict__", {})
    protected = values.get("_protected_args", ()) if isinstance(values, Mapping) else ()
    args = getattr(context, "args", ())
    return [*protected, *args]


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
    return cast(
        TextIO,
        tempfile.SpooledTemporaryFile(
            max_size=_MAX_JSON_CAPTURE_BYTES,
            mode="w+",
            encoding="utf-8",
            newline="",
        ),
    )


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
