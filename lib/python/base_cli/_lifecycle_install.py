"""Native and attached lifecycle option installation internals."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ._app_core import (
    _ATTACHED_LIFECYCLE_OPTION_ORDER,
    _CLICK_LIFECYCLE_BINDINGS_ATTRIBUTE,
    _FLAG_LIFECYCLE_OPTION_KEYS,
    _LIFECYCLE_CAPTURE_META_KEY,
    _LIFECYCLE_RESOLUTION_META_KEY,
    _NATIVE_LIFECYCLE_OPTION_ORDER,
    _STANDARD_OPTION_KEYS,
    _explicit_config_path_type,
    _LifecycleBinding,
    _LifecycleResolution,
    _RawLifecycleValue,
)
from .lifecycle_options import LIFECYCLE_META_KEY, LifecycleOption, LifecycleOptions, LifecycleValues


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


__all__ = [
    "_lifecycle_option_attrs",
    "_lifecycle_param_decls",
    "_context_depth",
    "_capture_lifecycle_option",
    "_make_lifecycle_value_option",
    "_make_lifecycle_version_option",
    "_normalized_parameter_declarations",
    "_normalized_parameter_declaration_sets",
    "_reject_duplicate_lifecycle_declarations",
    "_lifecycle_collision_details",
    "_implicit_help_declarations",
    "_missing_adopted_declarations",
    "_reject_implicit_help_collision",
    "_native_lifecycle_collision_error",
    "_install_native_lifecycle_options",
    "_parameter_source_rank",
    "_prefer_lifecycle_value",
    "_normalize_lifecycle_values",
    "_resolve_lifecycle_values",
    "_standard_options_from_values",
    "_add_attached_standard_options",
]
