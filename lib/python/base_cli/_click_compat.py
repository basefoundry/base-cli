"""Click dialect selection for third-party command trees.

The native base-cli command builder uses the public :mod:`click` package.  A
recent Typer release intentionally vendors its own Click fork, so a Typer
command tree must be extended with parameters and exceptions from that same
fork.  This module keeps that boundary in one place instead of spreading
``isinstance`` checks and private Typer imports through the lifecycle code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

_DIALECT_ATTRIBUTE = "__base_cli_click_dialect__"


@dataclass(frozen=True)
class _VendoredClickDialect:
    """The small Click surface required by the attached lifecycle."""

    module: Any
    Command: Any
    Option: Any
    Path: Any
    version_option: Callable[..., Any]
    exceptions: Any
    Abort: Any
    UsageError: Any
    ClickException: Any

    def __getattr__(self, name: str) -> Any:
        return getattr(self.module, name)


def _vendor_version_option_factory(
    typer_option: Any,
    echo: Callable[..., Any],
) -> Callable[..., Any]:
    """Build the Click ``version_option`` decorator for Typer's fork."""

    def version_option(
        version: str | None = None,
        *param_decls: str,
        package_name: str | None = None,
        prog_name: str | None = None,
        message: str | None = None,
        **kwargs: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        del package_name, prog_name
        if version is None:
            raise RuntimeError("A version is required for the Typer adapter.")
        if message is None:
            message = "%(prog)s, version %(version)s"
        if not param_decls:
            param_decls = ("--version",)
        kwargs.setdefault("is_flag", True)
        kwargs.setdefault("expose_value", False)
        kwargs.setdefault("is_eager", True)

        def callback(ctx: Any, _param: Any, value: bool) -> None:
            if not value or getattr(ctx, "resilient_parsing", False):
                return
            root_name = getattr(ctx.find_root(), "info_name", None) or "cli"
            rendered = message % {
                "prog": root_name,
                "package": "base-cli",
                "version": version,
            }
            echo(rendered, color=getattr(ctx, "color", None))
            ctx.exit()

        def decorator(source: Callable[..., Any]) -> Callable[..., Any]:
            parameter = typer_option(
                param_decls=list(param_decls),
                callback=callback,
                **kwargs,
            )
            params = list(getattr(source, "__click_params__", ()))
            params.append(parameter)
            cast(Any, source).__click_params__ = params
            return source

        return decorator

    return version_option


def _vendored_typer_dialect(typer: Any) -> _VendoredClickDialect | None:
    module = getattr(typer, "_click", None)
    if module is None or not hasattr(module, "Command"):
        return None
    try:
        from typer.core import TyperOption
        from typer.models import TyperPath
    except (ImportError, AttributeError):
        return None

    core = module.core
    exceptions = module.exceptions

    def option(param_decls: list[str], **attrs: Any) -> Any:
        return TyperOption(param_decls=list(param_decls), **attrs)

    return _VendoredClickDialect(
        module=module,
        Command=module.Command,
        Option=option,
        Path=TyperPath,
        version_option=_vendor_version_option_factory(TyperOption, module.echo),
        exceptions=exceptions,
        Abort=getattr(module, "Abort", getattr(exceptions, "Abort", core.Abort)),
        UsageError=getattr(module, "UsageError", core.UsageError),
        ClickException=module.ClickException,
    )


def dialect_for_typer(typer: Any) -> Any:
    """Return the Click implementation that owns a Typer command tree."""

    import click

    dialect = _vendored_typer_dialect(typer)
    return dialect if dialect is not None else click


def exit_exception_type(click: Any) -> type[BaseException]:
    """Return the owning dialect's exit exception across Click variants.

    Typer 0.27.2 keeps ``Exit`` on its core module (and maps it to
    ``typer.exceptions.Exit``) instead of exporting it from
    ``click.exceptions``. Older Click and Typer releases use the latter.
    """
    exceptions = getattr(click, "exceptions", None)
    candidate = getattr(exceptions, "Exit", None)
    if isinstance(candidate, type):
        return candidate
    core = getattr(click, "core", None)
    candidate = getattr(core, "Exit", None)
    if isinstance(candidate, type):
        return candidate
    raise AttributeError("Click dialect does not expose an Exit exception type")


def mark_command_dialect(command: Any, dialect: Any) -> Any:
    """Remember the owning dialect on a generated command object."""

    try:
        setattr(command, _DIALECT_ATTRIBUTE, dialect)
    except (AttributeError, TypeError):
        # Click command instances are mutable in all supported releases.  If a
        # third-party command forbids attributes, class detection still works.
        pass
    return command


def dialect_for_command(command: Any) -> Any:
    """Resolve the Click implementation that owns ``command``."""

    marked = getattr(command, _DIALECT_ATTRIBUTE, None)
    if marked is not None:
        return marked

    import click

    if isinstance(command, click.Command):
        return click

    try:
        import typer
    except ImportError:
        return click
    dialect = _vendored_typer_dialect(typer)
    if dialect is not None and isinstance(command, dialect.Command):
        mark_command_dialect(command, dialect)
        return dialect
    return click


def is_command(command: Any) -> bool:
    """Return whether a command belongs to a supported Click dialect."""

    import click

    if isinstance(command, click.Command):
        return True
    dialect = dialect_for_command(command)
    return dialect is not click and isinstance(command, dialect.Command)


__all__ = ["dialect_for_command", "dialect_for_typer", "exit_exception_type", "is_command", "mark_command_dialect"]
