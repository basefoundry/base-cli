# JSON-mode retention precedence

## Problem

`App` assigns implicit `RetentionPolicy.safe_defaults()` when no retention
configuration is supplied. `_create_context()` therefore consumes that policy
before it can reach the JSON-only retention branch. JSON mode currently gets
the general bundle policy (count, age, and total bytes), while the documented
contract promises a count-only limit of the most recent 20 bundles.

## Design

Keep `App.retention` and the public `max_log_files` parameter unchanged. Add an
internal marker that distinguishes an explicitly configured retention policy
or run bounds from the implicit human-mode default. Select the effective
policy in `_create_context()` with this precedence:

1. Explicit `retention=` or `max_run_*` bounds use the configured policy.
2. `max_log_files=` continues through its existing compatibility path.
3. An implicit JSON invocation uses `RetentionPolicy(max_bundles=20)` only.
4. An implicit human invocation keeps `RetentionPolicy.safe_defaults()`.

No public API is removed or renamed. Removing or deprecating
`max_log_files` is explicitly out of scope and should be handled by a separate
pre-1.0 compatibility-boundary change.

## Testing

Add behavior-level tests that create an old retained bundle and invoke an app:

- implicit JSON mode keeps the old bundle when the count is below 20, proving
  the JSON policy has no age bound;
- an explicit retention policy still removes the old bundle when its age bound
  requires removal; and
- implicit human mode retains the existing safe-default age behavior.

The existing JSON contract, retention, and compatibility tests remain the
regression suite for output and legacy behavior.

## Documentation and release

The JSON contract documentation already states the intended count-only policy.
Add an Unreleased changelog entry describing the corrected precedence. This is
a behavior correction with no public API or JSON schema change.
