"""Typed contracts for attaching base-cli lifecycle behavior to Click trees.

The Click adapter implementation remains private to :mod:`base_cli.app`, but
its boundary is deliberately small and typed. Consumers can use the adapter
protocol when wrapping or composing an attached command without importing the
framework's private Click machinery.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from .context import Context

__all__ = [
    "AttachmentAdapter",
    "AttachmentContextFactory",
    "AttachmentContract",
    "AttachmentServiceFactory",
]


CommandT = TypeVar("CommandT")


class AttachmentContextFactory(Protocol):
    """Create consumer-owned application state for an active Context."""

    def __call__(self, context: Context[Any, Any, Any]) -> Any: ...


class AttachmentServiceFactory(Protocol):
    """Create consumer-owned services for an active Context."""

    def __call__(self, context: Context[Any, Any, Any]) -> Any: ...


class AttachmentAdapter(Protocol[CommandT]):
    """Lifecycle adapter contract implemented by :class:`base_cli.App`.

    ``attach`` must return the exact command object it receives. This preserves
    Click's concrete command subtype, aliases, lazy resolution, and callback
    identity for consumers that compose multiple adapters.
    """

    def attach(
        self,
        command: CommandT,
        *,
        context_factory: AttachmentContextFactory | None = None,
        service_factory: AttachmentServiceFactory | None = None,
        sensitive_parameters: set[str] | frozenset[str] = frozenset(),
    ) -> CommandT: ...


@dataclass(frozen=True)
class AttachmentContract(Generic[CommandT]):
    """Immutable attachment state shared by the private Click adapter."""

    app: Any
    command: CommandT
    context_factory: Callable[[Context[Any, Any, Any]], Any] | None
    service_factory: Callable[[Context[Any, Any, Any]], Any] | None
    sensitive_parameters: frozenset[str]
    lifecycle_options: Any
    standard_bindings: dict[str, Any]
