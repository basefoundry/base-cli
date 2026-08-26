from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

REDACTED = "[REDACTED]"
SECRET_KEY_RE = re.compile(r"(token|password|secret|api[-_]?key|authorization)", re.IGNORECASE)
URL_CREDENTIALS_RE = re.compile(r"(?P<prefix>[a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]+@")
# Punctuation is part of a value unless it is immediately followed by another
# assignment segment. This prevents ``PASSWORD=abc,def`` from exposing ``def``
# while preserving readable output for ``PASSWORD=secret,LABEL=visible``.
_INLINE_SEGMENT_END = (
    r"(?=(?:[&,;]\s*(?=[A-Za-z][A-Za-z0-9_-]*\s*[=:])"
    r"|\s+[A-Za-z][A-Za-z0-9_-]*\s*[=:])|$)"
)
_INLINE_KEY_VALUE_RE = re.compile(
    rf"(?P<key>(?<![A-Za-z0-9_-])[A-Za-z][A-Za-z0-9_-]*)(?P<separator>=)"
    rf"(?P<value>[^\n]*?){_INLINE_SEGMENT_END}"
)
_INLINE_COLON_VALUE_RE = re.compile(
    rf"(?P<key>(?<![A-Za-z0-9_-])[A-Za-z][A-Za-z0-9_-]*)"
    rf"(?P<separator>\s*:(?!//)\s*)(?P<value>[^\n]*?){_INLINE_SEGMENT_END}"
)


@dataclass(frozen=True)
class _OptionSpec:
    name: str
    aliases: tuple[str, ...]
    sensitive: bool
    takes_value: bool
    nargs: int


@dataclass(frozen=True)
class _ArgumentSpec:
    name: str
    sensitive: bool
    nargs: int


@dataclass(frozen=True)
class _CommandSpec:
    options: tuple[_OptionSpec, ...]
    option_aliases: Mapping[str, _OptionSpec]
    arguments: tuple[_ArgumentSpec, ...]
    subcommands: Mapping[str, _CommandSpec]
    allow_interspersed_args: bool
    ignore_unknown_options: bool
    token_normalize_func: Callable[[str], str] | None
    chain: bool


@dataclass(frozen=True)
class _OptionMatch:
    option: _OptionSpec
    attached_prefix: str | None = None
    has_unknown: bool = False


class RedactionPlan(set[str]):
    """A set-compatible collection with a private Click-aware scan plan.

    Consumer history writers have always received ``set[str]`` sensitive-name
    collections. Subclassing ``set`` keeps that contract while allowing core
    logging and history sinks to retain the option and argument schema needed
    to redact values by their argv position.
    """

    def __init__(self, values: Iterable[str] = (), *, root: _CommandSpec | None = None) -> None:
        super().__init__(values)
        self._root = root


def option_name_to_parameter(param_decl: str) -> str:
    _prefix, name = _split_option(param_decl)
    return name.replace("-", "_").lower()


def option_aliases_from_decls(param_decls: Sequence[str]) -> tuple[str, ...]:
    """Return raw Click option aliases, including both split flag forms."""

    aliases: list[str] = []
    for decl in param_decls:
        if decl.isidentifier():
            continue
        split_char = ";" if decl.startswith("/") else "/"
        first, separator, second = decl.partition(split_char)
        for alias in (first.rstrip(), second.lstrip() if separator else ""):
            if alias and _split_option(alias)[0]:
                aliases.append(alias)
    return tuple(aliases)


def parameter_name_from_decls(param_decls: tuple[str, ...]) -> str:
    """Resolve Click's destination name from raw option declarations."""

    for decl in param_decls:
        if decl.isidentifier():
            return decl

    possible_names: list[tuple[str, str]] = []
    for decl in param_decls:
        split_char = ";" if decl.startswith("/") else "/"
        primary = decl.partition(split_char)[0].rstrip()
        if _split_option(primary)[0]:
            possible_names.append(_split_option(primary))
    if not possible_names:
        return option_name_to_parameter(param_decls[0])
    possible_names.sort(key=lambda value: -len(value[0]))
    return possible_names[0][1].replace("-", "_").lower()


def compile_redaction_plan(
    command: Any,
    sensitive_names: Iterable[str] = (),
    *,
    selected_path: Sequence[tuple[str, Any]] | None = None,
    selected_paths: Sequence[Sequence[tuple[str, Any]]] | None = None,
) -> RedactionPlan:
    """Compile a recursively Click-aware redaction plan for ``command``.

    Parameters may be marked with the private ``_base_cli_sensitive`` flag.
    ``sensitive_names`` remains useful for adapters that already have a set of
    destination names or raw aliases. Secret-looking parameter names are
    protected automatically as defense in depth.
    """

    if selected_path is not None and selected_paths is not None:
        raise TypeError("selected_path and selected_paths are mutually exclusive.")
    effective_paths = (
        (tuple(selected_path),)
        if selected_path is not None
        else tuple(tuple(path) for path in selected_paths)
        if selected_paths is not None
        else None
    )
    explicit = _sensitive_forms(sensitive_names)
    compatible_names: set[str] = set(sensitive_names)
    root = _compile_command(
        command,
        explicit,
        compatible_names,
        active=set(),
        inherited_token_normalize_func=None,
        selected_paths=effective_paths,
        force_no_interspersed=False,
    )
    return RedactionPlan(compatible_names, root=root)


def redact_argv(argv: list[str], sensitive_options: set[str]) -> list[str]:
    if isinstance(sensitive_options, RedactionPlan) and sensitive_options._root is not None:
        redacted = _redact_with_plan(argv, sensitive_options._root)
    else:
        redacted = _redact_without_schema(argv, sensitive_options)
    return [_redact_inline_text(value) for value in redacted]


def is_secret_key(value: str) -> bool:
    return SECRET_KEY_RE.search(value) is not None


def redact_text_value(value: str) -> str:
    return URL_CREDENTIALS_RE.sub(lambda match: f"{match.group('prefix')}{REDACTED}@", value)


def _compile_command(
    command: Any,
    explicit: set[str],
    compatible_names: set[str],
    *,
    active: set[int],
    inherited_token_normalize_func: Callable[[str], str] | None,
    selected_paths: tuple[tuple[tuple[str, Any], ...], ...] | None,
    force_no_interspersed: bool,
) -> _CommandSpec:
    identity = id(command)
    if identity in active:
        return _CommandSpec(
            options=(),
            option_aliases={},
            arguments=(),
            subcommands={},
            allow_interspersed_args=True,
            ignore_unknown_options=False,
            token_normalize_func=inherited_token_normalize_func,
            chain=False,
        )
    active.add(identity)
    try:
        context_settings = dict(getattr(command, "context_settings", None) or {})
        token_normalize_func = context_settings.get("token_normalize_func")
        if token_normalize_func is None:
            token_normalize_func = inherited_token_normalize_func
        options: list[_OptionSpec] = []
        arguments: list[_ArgumentSpec] = []
        for parameter in tuple(getattr(command, "params", ())):
            name = str(getattr(parameter, "name", "") or "")
            is_option = getattr(parameter, "param_type_name", None) == "option"
            raw_aliases = (
                tuple(
                    str(alias)
                    for alias in (
                        *tuple(getattr(parameter, "opts", ())),
                        *tuple(getattr(parameter, "secondary_opts", ())),
                    )
                    if alias
                )
                if is_option
                else ()
            )
            sensitive = (
                bool(getattr(parameter, "_base_cli_sensitive", False))
                or bool(getattr(parameter, "hide_input", False))
                or _parameter_is_sensitive(
                    name,
                    raw_aliases,
                    explicit,
                )
            )
            nargs = _parameter_nargs(parameter)
            if raw_aliases:
                is_flag = bool(getattr(parameter, "is_flag", False) or getattr(parameter, "count", False))
                options.append(
                    _OptionSpec(
                        name=name,
                        aliases=raw_aliases,
                        sensitive=sensitive,
                        takes_value=not is_flag,
                        nargs=max(1, nargs),
                    )
                )
                if sensitive:
                    _add_compatible_names(compatible_names, name, raw_aliases)
            else:
                arguments.append(_ArgumentSpec(name=name, sensitive=sensitive, nargs=nargs))
                if sensitive and name:
                    compatible_names.add(name)

        subcommands: dict[str, _CommandSpec] = {}
        chain = bool(getattr(command, "chain", False))
        if selected_paths is not None:
            selected_children: dict[str, tuple[Any, list[tuple[tuple[str, Any], ...]]]] = {}
            for path in selected_paths:
                if not path:
                    continue
                command_name, child = path[0]
                key = str(command_name)
                if key not in selected_children:
                    selected_children[key] = (child, [])
                elif selected_children[key][0] is not child:
                    raise RuntimeError(f"Selected command name '{key}' resolved to multiple Click commands.")
                selected_children[key][1].append(path[1:])
            for command_name, (child, child_paths) in selected_children.items():
                subcommands[command_name] = _compile_command(
                    child,
                    explicit,
                    compatible_names,
                    active=active,
                    inherited_token_normalize_func=token_normalize_func,
                    selected_paths=tuple(child_paths),
                    force_no_interspersed=chain,
                )
        else:
            raw_subcommands = getattr(command, "commands", None)
            if isinstance(raw_subcommands, Mapping):
                for command_name, child in raw_subcommands.items():
                    if child is not None:
                        subcommands[str(command_name)] = _compile_command(
                            child,
                            explicit,
                            compatible_names,
                            active=active,
                            inherited_token_normalize_func=token_normalize_func,
                            selected_paths=None,
                            force_no_interspersed=chain,
                        )

        allow_interspersed_args = context_settings.get("allow_interspersed_args")
        if allow_interspersed_args is None:
            allow_interspersed_args = getattr(command, "allow_interspersed_args", True)
        if force_no_interspersed:
            allow_interspersed_args = False
        ignore_unknown_options = context_settings.get("ignore_unknown_options")
        if ignore_unknown_options is None:
            ignore_unknown_options = getattr(command, "ignore_unknown_options", False)
        return _CommandSpec(
            options=tuple(options),
            option_aliases=_build_option_alias_map(tuple(options), token_normalize_func),
            arguments=tuple(arguments),
            subcommands=subcommands,
            allow_interspersed_args=bool(allow_interspersed_args),
            ignore_unknown_options=bool(ignore_unknown_options),
            token_normalize_func=token_normalize_func,
            chain=chain,
        )
    finally:
        active.remove(identity)


def _parameter_nargs(parameter: Any) -> int:
    value = getattr(parameter, "nargs", 1)
    return value if isinstance(value, int) else 1


def _build_option_alias_map(
    options: tuple[_OptionSpec, ...],
    normalize: Callable[[str], str] | None,
) -> Mapping[str, _OptionSpec]:
    alias_groups: dict[str, list[int]] = {}
    for index, option in enumerate(options):
        for alias in option.aliases:
            normalized = _normalize_option_token(alias, normalize)
            alias_groups.setdefault(normalized, []).append(index)

    effective_sensitive = [option.sensitive for option in options]
    changed = True
    while changed:
        changed = False
        for indices in alias_groups.values():
            if any(effective_sensitive[index] for index in indices):
                for index in indices:
                    if not effective_sensitive[index]:
                        effective_sensitive[index] = True
                        changed = True

    aliases: dict[str, _OptionSpec] = {}
    for index, option in enumerate(options):
        effective = option
        if effective_sensitive[index] and not option.sensitive:
            effective = _OptionSpec(
                name=option.name,
                aliases=option.aliases,
                sensitive=True,
                takes_value=option.takes_value,
                nargs=option.nargs,
            )
        for alias in option.aliases:
            aliases[_normalize_option_token(alias, normalize)] = effective
    return aliases


def _parameter_is_sensitive(name: str, aliases: tuple[str, ...], explicit: set[str]) -> bool:
    identifiers = {name, option_name_to_parameter(name)} if name else set()
    for alias in aliases:
        identifiers.add(alias)
        identifiers.add(option_name_to_parameter(alias))
    return bool(identifiers & explicit) or any(is_secret_key(identifier) for identifier in identifiers)


def _add_compatible_names(values: set[str], name: str, aliases: tuple[str, ...]) -> None:
    if name:
        values.add(name)
    for alias in aliases:
        values.add(alias)
        values.add(option_name_to_parameter(alias))


def _sensitive_forms(values: Iterable[str]) -> set[str]:
    result: set[str] = set()
    for value in values:
        result.add(value)
        result.add(option_name_to_parameter(value))
    return result


def _redact_with_plan(argv: list[str], root: _CommandSpec) -> list[str]:
    result = list(argv)
    if not argv:
        return result

    # Invocation argv always includes the display/program token at index zero.
    # It is metadata, not part of the Click grammar.
    _redact_command(argv, list(range(1, len(argv))), root, result)

    return result


def _redact_command(
    argv: list[str],
    token_indices: list[int],
    command: _CommandSpec,
    result: list[str],
) -> list[int]:
    positional_indices = _scan_options(argv, token_indices, command, result)
    argument_spans, extra_indices = _allocate_argument_spans(positional_indices, command.arguments)
    for argument, span in zip(command.arguments, argument_spans, strict=False):
        if argument.sensitive:
            for index in span:
                result[index] = REDACTED

    if not command.subcommands or not extra_indices:
        return extra_indices

    remaining = extra_indices
    while remaining:
        command_index = remaining[0]
        command_name = argv[command_index]
        child = command.subcommands.get(command_name)
        if child is None and command.token_normalize_func is not None:
            child = command.subcommands.get(command.token_normalize_func(command_name))
        if child is None:
            return remaining
        remaining = _redact_command(argv, remaining[1:], child, result)
        if not command.chain:
            return remaining
    return remaining


def _scan_options(
    argv: list[str],
    token_indices: list[int],
    command: _CommandSpec,
    result: list[str],
) -> list[int]:
    positional: list[int] = []
    offset = 0
    while offset < len(token_indices):
        index = token_indices[offset]
        value = argv[index]
        if value == "--":
            positional.extend(token_indices[offset + 1 :])
            break

        match = _match_option(value, command)
        if match is not None:
            values_needed = match.option.nargs if match.option.takes_value else 0
            if match.attached_prefix is not None:
                values_needed -= 1
                if match.option.sensitive:
                    result[index] = f"{match.attached_prefix}{REDACTED}"

            if match.has_unknown:
                positional.append(index)

            offset += 1
            while values_needed > 0 and offset < len(token_indices):
                value_index = token_indices[offset]
                if match.option.sensitive:
                    result[value_index] = REDACTED
                offset += 1
                values_needed -= 1
            continue

        if _looks_like_option(value, command):
            if command.ignore_unknown_options:
                positional.append(index)
            offset += 1
            continue

        if not command.allow_interspersed_args:
            positional.extend(token_indices[offset:])
            break
        positional.append(index)
        offset += 1
    return positional


def _allocate_argument_spans(
    positional_indices: list[int],
    arguments: tuple[_ArgumentSpec, ...],
) -> tuple[list[list[int]], list[int]]:
    spans: list[list[int]] = [[] for _ in arguments]
    wildcard = next((index for index, argument in enumerate(arguments) if argument.nargs < 0), None)
    left = 0
    right = len(positional_indices)

    prefix_end = wildcard if wildcard is not None else len(arguments)
    for argument_index in range(prefix_end):
        count = max(1, arguments[argument_index].nargs)
        end = min(left + count, right)
        spans[argument_index] = positional_indices[left:end]
        left = end

    if wildcard is not None:
        for argument_index in range(len(arguments) - 1, wildcard, -1):
            count = max(1, arguments[argument_index].nargs)
            start = max(left, right - count)
            spans[argument_index] = positional_indices[start:right]
            right = start
        spans[wildcard] = positional_indices[left:right]
        return spans, []

    return spans, positional_indices[left:]


def _match_option(value: str, command: _CommandSpec) -> _OptionMatch | None:
    aliases = command.option_aliases
    option_name, separator, _attached = value.partition("=")
    exact = aliases.get(_normalize_option_token(option_name, command.token_normalize_func))
    if separator:
        if exact is not None and exact.takes_value:
            return _OptionMatch(exact, f"{option_name}=")
        if exact is not None:
            return None
    elif exact is not None:
        return _OptionMatch(exact)

    short_prefix = value[:1]
    short_prefixes = {
        alias[:1]
        for option in command.options
        for alias in option.aliases
        if len(alias) == 2 and len(_split_option(alias)[0]) == 1
    }
    if short_prefix not in short_prefixes or value[1:2] == short_prefix or len(value) <= 2:
        return None
    last_option: _OptionSpec | None = None
    has_unknown = False
    for position, character in enumerate(value[1:]):
        short_option = aliases.get(_normalize_option_token(f"{short_prefix}{character}", command.token_normalize_func))
        if short_option is None:
            if command.ignore_unknown_options:
                has_unknown = True
                continue
            return None
        last_option = short_option
        if short_option.takes_value:
            attached = value[position + 2 :]
            attached_prefix = value[: position + 2] if attached else None
            if attached_prefix is not None and attached.startswith("="):
                attached_prefix += "="
            return _OptionMatch(short_option, attached_prefix, has_unknown)
    return _OptionMatch(last_option, has_unknown=has_unknown) if last_option is not None else None


def _normalize_option_token(value: str, normalize: Callable[[str], str] | None) -> str:
    if normalize is None:
        return value
    prefix, name = _split_option(value)
    if not prefix:
        return value
    return f"{prefix}{normalize(name)}"


def _looks_like_option(value: str, command: _CommandSpec) -> bool:
    if value == "--" or _match_option(value, command) is not None:
        return True
    prefix, _name = _split_option(value)
    prefixes: set[str] = set()
    for option in command.options:
        for alias in option.aliases:
            alias_prefix, _alias_name = _split_option(alias)
            if alias_prefix:
                prefixes.update((alias_prefix, alias_prefix[:1]))
    return bool(prefix) and prefix in prefixes


def _redact_without_schema(argv: list[str], sensitive_options: set[str]) -> list[str]:
    result = list(argv)
    sensitive = _sensitive_forms(sensitive_options)
    short_aliases = _legacy_short_aliases(sensitive_options)
    option_parsing = True
    index = 0
    while index < len(argv):
        value = argv[index]
        if option_parsing and value == "--":
            option_parsing = False
            index += 1
            continue
        if not option_parsing:
            index += 1
            continue

        option_name, separator, _attached = value.partition("=")
        normalized = option_name_to_parameter(option_name)
        explicitly_sensitive = option_name in sensitive or normalized in sensitive
        automatically_sensitive = _is_option_alias(option_name) and is_secret_key(normalized)
        if explicitly_sensitive:
            if separator:
                result[index] = f"{option_name}={REDACTED}"
            elif index + 1 < len(argv):
                result[index + 1] = REDACTED
                index += 1
            index += 1
            continue

        attached_alias = next(
            (alias for alias in short_aliases if value.startswith(alias) and len(value) > len(alias)),
            None,
        )
        if attached_alias is not None:
            suffix = value[len(attached_alias) :]
            equals = "=" if suffix.startswith("=") else ""
            result[index] = f"{attached_alias}{equals}{REDACTED}"
        elif automatically_sensitive:
            if separator:
                result[index] = f"{option_name}={REDACTED}"
            elif index + 1 < len(argv):
                result[index + 1] = REDACTED
                index += 1
        index += 1
    return result


def _legacy_short_aliases(sensitive_options: Iterable[str]) -> tuple[str, ...]:
    aliases: set[str] = set()
    for value in sensitive_options:
        prefix, name = _split_option(value)
        if len(prefix) == 1 and len(name) == 1:
            aliases.add(value)
        elif len(value) == 1 and value.isidentifier():
            aliases.add(f"-{value}")
    return tuple(sorted(aliases, key=len, reverse=True))


def _redact_inline_text(value: str) -> str:
    value = _INLINE_KEY_VALUE_RE.sub(_redact_inline_segment, value)
    value = _INLINE_COLON_VALUE_RE.sub(_redact_inline_segment, value)
    return redact_text_value(value)


def _redact_inline_segment(match: re.Match[str]) -> str:
    key = match.group("key")
    if not is_secret_key(option_name_to_parameter(key)):
        return match.group(0)
    return f"{key}{match.group('separator')}{REDACTED}"


def _is_option_alias(value: str) -> bool:
    return bool(_split_option(value)[0])


def _split_option(value: str) -> tuple[str, str]:
    first = value[:1]
    if not first or first.isalnum() or first == "_":
        return "", value
    if value[1:2] == first:
        return value[:2], value[2:]
    return first, value[1:]
