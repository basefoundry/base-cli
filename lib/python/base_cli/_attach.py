"""Click attachment instrumentation and lifecycle resources."""

from __future__ import annotations

import functools
import sys
import time
from collections.abc import Callable, Iterable
from typing import Any

from ._app_core import (
    _ATTACHED_INVOCATION,
    _CLICK_APP_OWNER_ATTRIBUTE,
    _CLICK_ATTACHMENT_ATTRIBUTE,
    _CLICK_ATTACHMENT_LOCK,
    _CLICK_INSTRUMENTED_ATTRIBUTE,
    _CLICK_INSTRUMENTED_SENTINEL,
    _CLICK_MAIN_INSTRUMENTED_ATTRIBUTE,
    _CLICK_MAIN_INSTRUMENTED_SENTINEL,
    _CLICK_ORIGINAL_INVOKE_ATTRIBUTE,
    _CLICK_ORIGINAL_MAIN_ATTRIBUTE,
    _CLICK_ORIGINAL_RESOLVE_ATTRIBUTE,
    _INVOCATION_ARGV,
    _INVOCATION_MAIN_BYPASS,
    _INVOCATION_STATE,
    App,
    _AttachedInvocation,
    _capture_invocation_context,
    _capture_standard_options,
    _ClickAttachment,
    _finish_run_recorder,
    _record_lifecycle_diagnostic,
    _reset_active_context,
    _reset_context_var,
    _start_run_recorder,
    _validate_standard_options,
    _warn_lifecycle_failure,
)
from ._click_compat import exit_exception_type
from ._lifecycle import RunRecorder, outcome_from_exception, outcome_from_exit_code
from ._lifecycle_install import _resolve_lifecycle_values, _standard_options_from_values
from ._run import _reject_async_callback, _reject_async_result
from ._runtime import RuntimeDirectoryError
from .context import Context, set_current_context
from .errors import ConfigurationError
from .exit_codes import ExitCode
from .history import utc_now
from .integrations import TelemetrySession, finish_telemetry, start_telemetry
from .lifecycle_options import LifecycleValues


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
                self.record_exception(exit_exception_type(self.click)(code))
                return original_click_exit(code)

            self.original_click_exit = original_click_exit
            self.click_exit_wrapper = lifecycle_aware_exit
            self.click_context.exit = lifecycle_aware_exit
            return self
        except BaseException as exc:
            if self.context is not None:
                self.outcome = outcome_from_exception(self.click, exc)
                _record_lifecycle_diagnostic(self.context, self.outcome)
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
            _record_lifecycle_diagnostic(self.context, self.outcome)

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


__all__ = [
    "_AttachedLifecycleResource",
    "_normalize_sensitive_parameters",
    "_normalize_attached_option_declaration",
    "_selected_click_path",
    "_selected_click_paths",
    "_click_command_has_pending_children",
    "_with_attached_lifecycle_resource",
    "_instrument_attached_click_command",
    "_restore_attached_click_command",
    "_instrument_attached_click_main",
    "_restore_attached_click_main",
]
