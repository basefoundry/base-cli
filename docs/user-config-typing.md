# Typed user configuration

This document explains the intentional typing boundary for `Context.user_config`
and the recommended patterns for typed access in consuming applications.

## Decision for the 0.4.x line

Keep the existing public shape:

```python
Context[ConfigT, ApplicationStateT, ServicesT]
```

and keep `Context.user_config` annotated as `object | None`. The value is
loaded and owned by `CliProfile`; base-cli only passes it to the optional
workspace-root resolver and stores it on the active context. This opacity is
intentional: a generic framework must not impose a mapping, dataclass, schema,
serialization format, or validation policy on consumers.

Do not add a fourth `Context` type parameter in a 0.4.x patch or minor release.
Adding `UserConfigT` would require changing every public callback protocol,
`ContextVar`, history callback, attachment factory, example, and consumer type
alias at once. It would also not infer a type from an arbitrary
`CliProfile` instance without making the profile generic as well.

## Recommended consumer pattern

Consumers should define one typed accessor at their product boundary and use it
inside commands. The cast is intentionally centralized and does not leak into
every command:

```python
from dataclasses import dataclass
from typing import Any, cast

import base_cli


@dataclass(frozen=True)
class UserSettings:
    workspace: str
    preferred_environment: str = "dev"


def user_settings(
    context: base_cli.Context[Any, Any, Any],
) -> UserSettings | None:
    return cast(UserSettings | None, context.user_config)


@app.command()
def status(context: base_cli.Context[Any, Any, Any]) -> None:
    settings = user_settings(context)
    if settings is not None:
        context.log.info("workspace=%s", settings.workspace)
```

The accessor is the appropriate place for consumer-owned validation if the
profile can receive data from an untrusted or mutable source. A consumer that
needs runtime validation should parse into `UserSettings` in
`load_user_config()` and make the accessor a narrow assertion rather than
re-parsing on every command.

The framework's existing `WorkspaceRootResolver` remains deliberately typed as
`object | None -> Path | None`; it is a projection boundary, not a schema
owner. Consumers may close over their typed loader or use a typed helper before
passing the profile to `App`.

## Why not a protocol-only fix?

A `UserConfigProtocol` would not solve the core problem. Consumer settings may
be a dataclass, mapping, immutable model, or `None`, and a protocol would either
be too broad to provide useful editor support or would force unrelated
consumers to implement framework-owned members. Structural typing is useful in
the consumer accessor, but not as a required base-cli schema.

## Future `0.5.0` option

If several independent consumers demonstrate that the accessor pattern is
insufficient, a major compatibility boundary may introduce:

```python
Context[ConfigT, ApplicationStateT, ServicesT, UserConfigT]
```

That change must be designed as an end-to-end generic flow, not just a field
annotation. Any such design must cover:

1. a generic `UserConfigLoader[UserConfigT]` and a generic `CliProfile`;
2. inference and explicit type aliases for `App` and every callback protocol;
3. compatibility defaults for existing `Context[A, B, C]` annotations;
4. `ContextVar` and history/attachment callback typing;
5. strict mypy fixtures for typed and untyped consumers; and
6. migration guidance and a deprecation window for the three-parameter form.

No fourth type parameter should ship until those questions have a reviewed
answer and at least two independent typed consumer fixtures exercise it.

## Future compatibility follow-up

This document is the design decision for issue [#105](https://github.com/basefoundry/base-cli/issues/105).
The next implementation can add an optional framework helper only if repeated
consumer accessors show a common, stable runtime contract. Such a helper must
remain additive, preserve the opaque field, and never change the existing
`Context` generic arity.
