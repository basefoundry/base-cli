from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path


def _resolve_version() -> str:
    """Return the checkout version or the installed distribution version."""

    package_dir = Path(__file__).resolve().parent
    python_dir = package_dir.parent
    lib_dir = python_dir.parent
    checkout_root = lib_dir.parent
    if (
        python_dir.name == "python"
        and lib_dir.name == "lib"
        and (checkout_root / "pyproject.toml").is_file()
    ):
        version_file = checkout_root / "VERSION"
        if version_file.is_file():
            value = version_file.read_text(encoding="utf-8").splitlines()[0].strip()
            if value:
                return value

    try:
        return distribution_version("base-cli")
    except PackageNotFoundError:
        return "0.0.0"


__version__ = _resolve_version()

from . import command_filters, command_protocol, history, testing
from .attachment import (
    AttachmentAdapter,
    AttachmentContextFactory,
    AttachmentContract,
    AttachmentServiceFactory,
)
from .app import (
    App,
    argument,
    attach,
    command,
    delegated_display_command,
    get_command_app,
    option,
    run_app,
)
from .command_filters import CommandFilterNormalizer, command_matches, normalize_command_filter, normalize_command_filters
from .command_protocol import (
    BOOLEAN,
    DEFAULT_SCHEMA_REGISTRY,
    CommandCodec,
    NULLABLE_STRING,
    STRING,
    CommandProtocolError,
    CommandSchemaRegistry,
    FieldSpec,
    RECORD_SCHEMAS,
    dumps_record,
    dumps_records,
    loads_records,
    register_record_schema,
)
from .context import (
    ApplicationStateT,
    ConfigT,
    Context,
    ServicesT,
    get_current_context,
)
from .errors import ConfigurationError
from .exit_codes import ExitCode
from .inspection import inspection_envelope, render_inspection_json
from .logging import configure_logger, log_critical, log_debug, log_error, log_info, log_warning
from .lifecycle_options import (
    LIFECYCLE_META_KEY,
    LifecycleOption,
    LifecycleOptions,
    LifecycleValues,
    get_lifecycle_values,
)
from .output import (
    OutputFormatError,
    PUBLIC_OUTPUT_FORMATS,
    is_terminal,
    output_format_choices,
    render_document,
    render_records,
    resolve_output_format,
)
from .profile import (
    CliProfile,
    ConfigLoader,
    DisplayCommandResolver,
    HistoryDisplayResolver,
    HistoryWriter,
    ProjectDiscovery,
    ProjectInfo,
    RuntimeBinding,
    RuntimeResolver,
    UserConfigLoader,
    WorkspaceRootResolver,
)
from .runtime import RuntimeLayout

__all__ = [
    "App",
    "__version__",
    "AttachmentAdapter",
    "AttachmentContextFactory",
    "AttachmentContract",
    "AttachmentServiceFactory",
    "BOOLEAN",
    "ApplicationStateT",
    "CliProfile",
    "ConfigLoader",
    "CommandFilterNormalizer",
    "CommandCodec",
    "CommandProtocolError",
    "CommandSchemaRegistry",
    "ConfigurationError",
    "Context",
    "ConfigT",
    "DEFAULT_SCHEMA_REGISTRY",
    "DisplayCommandResolver",
    "ExitCode",
    "FieldSpec",
    "LIFECYCLE_META_KEY",
    "LifecycleOption",
    "LifecycleOptions",
    "LifecycleValues",
    "NULLABLE_STRING",
    "STRING",
    "command_filters",
    "command_matches",
    "command_protocol",
    "dumps_record",
    "dumps_records",
    "history",
    "inspection_envelope",
    "render_inspection_json",
    "testing",
    "argument",
    "attach",
    "command",
    "configure_logger",
    "delegated_display_command",
    "get_command_app",
    "get_current_context",
    "get_lifecycle_values",
    "log_critical",
    "log_debug",
    "log_error",
    "log_info",
    "log_warning",
    "loads_records",
    "normalize_command_filter",
    "normalize_command_filters",
    "OutputFormatError",
    "PUBLIC_OUTPUT_FORMATS",
    "ProjectInfo",
    "ProjectDiscovery",
    "RECORD_SCHEMAS",
    "RuntimeLayout",
    "RuntimeResolver",
    "is_terminal",
    "output_format_choices",
    "option",
    "render_document",
    "render_records",
    "register_record_schema",
    "resolve_output_format",
    "run_app",
    "RuntimeBinding",
    "ServicesT",
    "HistoryWriter",
    "HistoryDisplayResolver",
    "UserConfigLoader",
    "WorkspaceRootResolver",
]
