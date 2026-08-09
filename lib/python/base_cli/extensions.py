"""Lazy, deterministic discovery of optional Python entry-point extensions.

The core package deliberately does not import or execute third-party plugins
at import time.  Consumers opt into discovery with :class:`ExtensionDiscovery`
and choose when a selected entry point is loaded.
"""

from __future__ import annotations

import importlib.metadata as metadata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Protocol, cast

COMMAND_ENTRY_POINT_GROUP = "base_cli.commands"
PROFILE_ENTRY_POINT_GROUP = "base_cli.profiles"
PLUGIN_ENTRY_POINT_GROUP = "base_cli.plugins"
EXTENSION_API_VERSION = "1"
ENTRY_POINT_GROUPS = (
    COMMAND_ENTRY_POINT_GROUP,
    PROFILE_ENTRY_POINT_GROUP,
    PLUGIN_ENTRY_POINT_GROUP,
)

__all__ = [
    "COMMAND_ENTRY_POINT_GROUP",
    "CommandExtension",
    "ENTRY_POINT_GROUPS",
    "EXTENSION_API_VERSION",
    "ExtensionCompatibilityError",
    "ExtensionCollisionError",
    "ExtensionDescriptor",
    "ExtensionDiscovery",
    "ExtensionDiscoveryError",
    "ExtensionLoadError",
    "ExtensionLoadResult",
    "ExtensionsDisabledError",
    "PLUGIN_ENTRY_POINT_GROUP",
    "PluginExtension",
    "ProfileExtension",
    "PROFILE_ENTRY_POINT_GROUP",
]


class ExtensionDiscoveryError(RuntimeError):
    """Base class for actionable extension discovery failures."""


class ExtensionsDisabledError(ExtensionDiscoveryError):
    """Raised when a caller attempts to load an extension while disabled."""


class ExtensionCollisionError(ExtensionDiscoveryError):
    """Raised when more than one distribution claims the same extension name."""

    def __init__(self, group: str, name: str, descriptors: Sequence[ExtensionDescriptor]) -> None:
        self.group = group
        self.name = name
        self.descriptors = tuple(descriptors)
        details = ", ".join(
            f"{descriptor.distribution or '<unknown>'} {descriptor.version or '<unknown>'}"
            for descriptor in self.descriptors
        )
        super().__init__(
            f"Extension name '{name}' is claimed more than once in group '{group}': {details}. "
            "Rename one entry point or select a different name explicitly."
        )


class ExtensionCompatibilityError(ExtensionDiscoveryError):
    """Raised when an extension declares an unsupported SDK version."""

    def __init__(self, descriptor: ExtensionDescriptor, supported: Sequence[str]) -> None:
        self.descriptor = descriptor
        self.supported = tuple(supported)
        expected = ", ".join(self.supported)
        super().__init__(
            f"Extension '{descriptor.key}' declares API version {descriptor.api_version!r}; "
            f"supported versions are: {expected}. Install a compatible extension release."
        )


class ExtensionLoadError(ExtensionDiscoveryError):
    """Wrap an extension import failure without hiding its source metadata."""

    def __init__(self, descriptor: ExtensionDescriptor, cause: BaseException) -> None:
        self.descriptor = descriptor
        self.cause = cause
        super().__init__(
            f"Unable to load {descriptor.group} extension '{descriptor.name}' "
            f"from {descriptor.distribution or '<unknown distribution>'} "
            f"({descriptor.value}): {cause}"
        )


@dataclass(frozen=True)
class ExtensionDescriptor:
    """Stable metadata for one entry point, before its object is loaded."""

    group: str
    name: str
    value: str
    distribution: str | None
    version: str | None
    extras: tuple[str, ...] = ()
    api_version: str = EXTENSION_API_VERSION
    capabilities: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """Return the fully qualified allowlist key ``group:name``."""

        return f"{self.group}:{self.name}"


@dataclass(frozen=True)
class ExtensionLoadResult:
    """Result of an isolated bulk extension load."""

    descriptor: ExtensionDescriptor
    value: Any | None = None
    error: ExtensionLoadError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class EntryPointProvider(Protocol):
    """Provide an iterable of importlib metadata entry-point objects."""

    def __call__(self) -> Iterable[Any]: ...


class CommandExtension(Protocol):
    """Callable contract for ``base_cli.commands`` entry points."""

    def __call__(self, app: Any) -> None: ...


class ProfileExtension(Protocol):
    """Callable contract for ``base_cli.profiles`` entry points."""

    def __call__(self, name: str) -> Any: ...


class PluginExtension(Protocol):
    """Callable contract for ``base_cli.plugins`` entry points."""

    def __call__(self, app: Any) -> None: ...


class ExtensionDiscovery:
    """Discover and lazily load command, profile, and plugin entry points.

    Discovery is metadata-only until :meth:`load` is called.  Results are
    cached per instance; call :meth:`refresh` when the environment has changed.
    Entry-point names are sorted deterministically by group, name, distribution,
    version, and target value.  Duplicate names are never resolved by
    installation order: :meth:`load` raises :class:`ExtensionCollisionError`.
    """

    def __init__(
        self,
        *,
        disabled: bool = False,
        allowlist: Iterable[str] | None = None,
        entry_points: Iterable[Any] | EntryPointProvider | None = None,
        paths: Iterable[Path] | None = None,
        supported_api_versions: Iterable[str] = (EXTENSION_API_VERSION,),
    ) -> None:
        if entry_points is not None and paths is not None:
            raise ValueError("pass either entry_points or paths, not both")
        self.disabled = disabled
        self.allowlist = frozenset(allowlist) if allowlist is not None else None
        self._entry_points = entry_points
        self._paths = tuple(Path(path) for path in paths) if paths is not None else None
        self.supported_api_versions = frozenset(str(version) for version in supported_api_versions)
        if not self.supported_api_versions:
            raise ValueError("supported_api_versions must contain at least one version")
        self._raw_cache: tuple[Any, ...] | None = None
        self._metadata_cache: tuple[ExtensionDescriptor, ...] | None = None
        self._descriptor_cache: dict[str, tuple[ExtensionDescriptor, ...]] = {}
        self._loaded_cache: dict[tuple[str, str], Any] = {}
        self._lock = RLock()

    def list(self, group: str | None = None) -> tuple[ExtensionDescriptor, ...]:
        """Return allowed metadata descriptors without importing extensions."""

        if self.disabled:
            return ()
        if group is not None:
            _validate_group(group)
        with self._lock:
            if group is not None and group in self._descriptor_cache:
                return self._descriptor_cache[group]
            descriptors = self._metadata_descriptors()
            if group is not None:
                descriptors = tuple(descriptor for descriptor in descriptors if descriptor.group == group)
                self._descriptor_cache[group] = descriptors
            return descriptors

    def list_commands(self) -> tuple[ExtensionDescriptor, ...]:
        """Return command extension descriptors."""

        return self.list(COMMAND_ENTRY_POINT_GROUP)

    def list_profiles(self) -> tuple[ExtensionDescriptor, ...]:
        """Return profile extension descriptors."""

        return self.list(PROFILE_ENTRY_POINT_GROUP)

    def list_plugins(self) -> tuple[ExtensionDescriptor, ...]:
        """Return plugin extension descriptors."""

        return self.list(PLUGIN_ENTRY_POINT_GROUP)

    def load(self, group: str, name: str) -> Any:
        """Load one uniquely named extension and cache the resulting object."""

        if self.disabled:
            raise ExtensionsDisabledError("Python extension discovery is disabled")
        _validate_group(group)
        matches = tuple(descriptor for descriptor in self.list(group) if descriptor.name == name)
        if not matches:
            raise ExtensionDiscoveryError(f"No extension named '{name}' exists in group '{group}'.")
        if len(matches) > 1:
            raise ExtensionCollisionError(group, name, matches)
        key = (group, name)
        with self._lock:
            if key in self._loaded_cache:
                return self._loaded_cache[key]
        descriptor = matches[0]
        if descriptor.api_version not in self.supported_api_versions:
            raise ExtensionCompatibilityError(descriptor, tuple(sorted(self.supported_api_versions)))
        try:
            value = self._load_descriptor(descriptor)
        except BaseException as exc:  # isolate third-party import failures
            raise ExtensionLoadError(descriptor, exc) from exc
        with self._lock:
            self._loaded_cache[key] = value
        return value

    def load_all(self, group: str) -> tuple[ExtensionLoadResult, ...]:
        """Load every allowed extension independently, preserving good results."""

        if self.disabled:
            raise ExtensionsDisabledError("Python extension discovery is disabled")
        _validate_group(group)
        results: list[ExtensionLoadResult] = []
        for descriptor in self.list(group):
            try:
                results.append(ExtensionLoadResult(descriptor, value=self.load(group, descriptor.name)))
            except ExtensionLoadError as exc:
                results.append(ExtensionLoadResult(descriptor, error=exc))
            except ExtensionCollisionError as exc:
                results.append(ExtensionLoadResult(descriptor, error=ExtensionLoadError(descriptor, exc)))
            except ExtensionCompatibilityError as exc:
                results.append(ExtensionLoadResult(descriptor, error=ExtensionLoadError(descriptor, exc)))
        return tuple(results)

    def refresh(self) -> None:
        """Clear metadata and loaded-object caches for a new environment snapshot."""

        with self._lock:
            self._raw_cache = None
            self._metadata_cache = None
            self._descriptor_cache.clear()
            self._loaded_cache.clear()

    def _metadata_descriptors(self) -> tuple[ExtensionDescriptor, ...]:
        if self._metadata_cache is not None:
            return self._metadata_cache
        descriptors: list[ExtensionDescriptor] = []
        for entry_point in self._raw_entry_points():
            group = getattr(entry_point, "group", None)
            name = getattr(entry_point, "name", None)
            value = getattr(entry_point, "value", None)
            if group not in ENTRY_POINT_GROUPS or not isinstance(name, str) or not isinstance(value, str):
                continue
            descriptor = _descriptor_from_entry_point(entry_point)
            if self._allowed(descriptor):
                descriptors.append(descriptor)
        descriptors.sort(key=_descriptor_sort_key)
        self._metadata_cache = tuple(descriptors)
        return self._metadata_cache

    def _raw_entry_points(self) -> tuple[Any, ...]:
        if self._raw_cache is not None:
            return self._raw_cache
        if self._paths is not None:
            values: list[Any] = []
            for distribution in metadata.distributions(path=[str(path) for path in self._paths]):
                values.extend(distribution.entry_points)
            self._raw_cache = tuple(values)
            return self._raw_cache
        source = self._entry_points
        if source is None:
            self._raw_cache = _normalise_entry_points(metadata.entry_points())
        else:
            raw_values: Any = source() if callable(source) else source
            self._raw_cache = _normalise_entry_points(raw_values)
        return self._raw_cache

    def _allowed(self, descriptor: ExtensionDescriptor) -> bool:
        if self.allowlist is None:
            return True
        return bool(
            descriptor.name in self.allowlist
            or descriptor.key in self.allowlist
            or (descriptor.distribution is not None and descriptor.distribution in self.allowlist)
        )

    def _load_descriptor(self, descriptor: ExtensionDescriptor) -> Any:
        for entry_point in self._raw_entry_points():
            if (
                getattr(entry_point, "group", None) == descriptor.group
                and getattr(entry_point, "name", None) == descriptor.name
                and getattr(entry_point, "value", None) == descriptor.value
            ):
                return entry_point.load()
        raise ImportError("entry point disappeared before it could be loaded")


def _validate_group(group: str) -> None:
    if group not in ENTRY_POINT_GROUPS:
        expected = ", ".join(ENTRY_POINT_GROUPS)
        raise ValueError(f"Unsupported extension group '{group}'. Expected one of: {expected}.")


def _normalise_entry_points(values: Any) -> tuple[Any, ...]:
    if isinstance(values, Mapping):
        return tuple(entry_point for group in values.values() for entry_point in group)
    return tuple(values)


def _descriptor_from_entry_point(entry_point: Any) -> ExtensionDescriptor:
    distribution = getattr(entry_point, "dist", None)
    distribution_name: str | None = None
    version: str | None = None
    if distribution is not None:
        distribution_name = getattr(distribution, "name", None)
        version = getattr(distribution, "version", None)
        if distribution_name is None:
            distribution_metadata = getattr(distribution, "metadata", None)
            if distribution_metadata is not None:
                distribution_name = distribution_metadata.get("Name")
    extras = getattr(entry_point, "extras", ()) or ()
    api_versions = tuple(
        extra.removeprefix("base-cli-api-v")
        for extra in extras
        if isinstance(extra, str) and extra.startswith("base-cli-api-v")
    )
    if len(api_versions) > 1:
        raise ValueError("an extension entry point may declare only one base-cli-api-vN extra")
    api_version = api_versions[0] if api_versions else EXTENSION_API_VERSION
    capabilities = tuple(
        sorted(
            extra.removeprefix("base-cli-cap-")
            for extra in extras
            if isinstance(extra, str) and extra.startswith("base-cli-cap-")
        )
    )
    return ExtensionDescriptor(
        group=cast(str, entry_point.group),
        name=cast(str, entry_point.name),
        value=cast(str, entry_point.value),
        distribution=distribution_name,
        version=str(version) if version is not None else None,
        extras=tuple(str(extra) for extra in extras),
        api_version=api_version,
        capabilities=capabilities,
    )


def _descriptor_sort_key(descriptor: ExtensionDescriptor) -> tuple[str, ...]:
    return (
        descriptor.group.casefold(),
        descriptor.name.casefold(),
        descriptor.name,
        (descriptor.distribution or "").casefold(),
        descriptor.version or "",
        descriptor.value,
    )
