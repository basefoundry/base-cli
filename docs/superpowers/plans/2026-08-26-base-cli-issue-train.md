# Base CLI issue train implementation plan

## Baseline and branch protocol

1. Start each issue with `basectl gh issue start <number>` from the repository,
   create the printed worktree from `origin/main`, and set the issue's project
   status to **In Progress** while preserving priority, area, initiative, size,
   assignee, and milestone.
2. Work test-first in the isolated worktree. Never commit generated `uv.lock`.
3. Run focused tests after the red test and after the fix, then run the full
   suite and applicable quality/docs checks before commit and PR creation.
4. Open a ready PR with `Closes #<number>`, monitor required checks, squash-merge
   when green, mark the issue Done, and remove the local and remote branch.

## PR #220 — complete secret redaction

- Tests: extend adversarial redaction coverage for comma/semicolon/ampersand
  values, URL/query-like values, and adjacent assignment segments in argv and
  JSON text. First assert the current implementation leaks the suffix.
- Code: replace unconditional punctuation termination with an assignment-aware
  boundary rule shared by inline redaction paths; preserve whitespace and
  explicit next-key boundaries while treating punctuation inside the current
  secret as part of the value. Keep URL-credential redaction intact.
- Verify: focused redaction/adversarial tests, full pytest, Ruff, strict mypy.

## PR #221 — JSON pre-parser grammar

- Tests: add short aliases and attached clusters, `--` positional behavior,
  separate values, and parse-error JSON output regressions.
- Code: make `_json_requested` scan Click-compatible option tokens, stopping at
  `--`, recognizing declared long/short aliases and attached forms without
  changing default-map or environment detection.
- Verify: `_run`/JSON-contract tests, full pytest, Ruff, strict mypy.

## PR #222 — mixed invocation serialization

- Tests: add a deterministic barrier-based test where a no-cwd invocation and a
  cwd-mutating invocation overlap; assert the observer cannot run inside the
  temporary cwd and that the caller cwd is restored.
- Code: acquire the existing invocation lock for every `CliRunner.invoke` path,
  retaining the existing context-manager restoration behavior.
- Verify: testing-helper tests, full pytest, Ruff, strict mypy.

## PR #223 — terminal/control sanitization

- Tests: cover C0/C1, Unicode `Cc`/`Cf`, tab, bidi, ANSI, newline, and carriage
  return characters in built-in and Rich table output plus CSV/TSV parsing.
- Code: centralize cell sanitization, replacing unsafe/control/format characters
  with spaces before display-width/truncation and use the sanitized value in all
  renderers.
- Verify: output tests, full pytest, Ruff, strict mypy.

## PR #224 — schema lookup immutability

- Tests: mutate a mapping returned by `schema()` and assert subsequent lookups,
  registration, and the compatibility registry are unchanged.
- Code: return a shallow defensive copy at the public lookup boundary; retain
  internal stored mappings and existing validation errors.
- Verify: command-protocol tests, full pytest, Ruff, strict mypy.

## PR #225 — protocol-header validation

- Tests: reject empty, LF, CR, NUL, and other framing-control headers for both
  encode and decode; accept the default and a safe custom header; assert error
  text does not emit raw controls.
- Code: add one shared header validator and call it from `dumps_records` and
  `loads_records` before framing/parsing.
- Verify: command-protocol tests, full pytest, Ruff, strict mypy.

## PR #226 — documentation boundary validation

- Tests: make the docs validator recurse, detect unnaved Markdown, and honor an
  explicit exclusion policy. Add a fixture/test for an unexpected nested page.
- Code/docs: remove internal `docs/superpowers` train artifacts from the public
  tree; update the validator and docs workflow so strict MkDocs plus link
  validation fail on accidental pages while allowing declared assets.
- Verify: docs validator, strict MkDocs build, full pytest, Ruff, strict mypy.

## Final train verification

After all seven merges, update local `main` from `origin/main`, verify every
issue is closed/Done and every train branch is absent locally and remotely, run
the complete quality suite once more, and report merged PRs, commits, checks,
and any intentional skips.

