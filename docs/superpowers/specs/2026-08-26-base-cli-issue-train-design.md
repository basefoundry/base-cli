# Base CLI open-issue train design

## Purpose

This train closes the seven implementation-ready issues currently open in
`basefoundry/base-cli` (#220–#226). The work is scoped to concrete correctness,
security, test-isolation, API-immutability, protocol-framing, and documentation
quality gaps identified against `origin/main`.

## Delivery policy

- One issue maps to one focused pull request and one merge commit/squash commit.
- Every issue is moved to **In Progress** before coding and remains assigned to
  `codeforester`, milestone `v1.0.0`, with its existing project fields intact.
- Each PR uses test-first development: add a regression test, observe the
  expected failure, implement the smallest compatible fix, then run focused and
  full validation.
- PRs are merged only after required hosted checks are green. The source branch,
  local worktree, and stale remote-tracking reference are removed after merge.
- The train runs in dependency order. #224 precedes #225 because both touch the
  command-protocol module; #226 is last because it removes internal train
  planning artifacts from the published documentation tree.

## Issue contracts

### #220 — complete delimiter-bearing secret redaction

Redaction must preserve a complete sensitive value when it contains commas,
semicolons, ampersands, query-like punctuation, or other non-whitespace text.
Punctuation may terminate a value only when it unambiguously begins another
assignment segment. The rule applies consistently to argv/history text and JSON
envelopes/log messages, without exposing any secret substring.

### #221 — Click-grammar-aware pre-parse JSON detection

The JSON-mode pre-parser must understand long options, short aliases, attached
short-option clusters, and the `--` end-of-options marker. It must not activate
JSON mode for option-looking positional values after `--`, and must preserve
Click's normal parse-error behavior and existing default/env detection.

### #222 — serialize all cwd-sensitive test invocations

Testing helpers must serialize both invocations that mutate process cwd and
invocations that observe cwd, so mixed calls cannot overlap while another call
temporarily changes cwd. Existing cwd restoration and non-cwd behavior remain
unchanged.

### #223 — sanitize terminal controls and bidi characters

Table-cell rendering must remove ANSI escapes and replace terminal-control,
format, bidi, newline, carriage-return, and tab characters with safe spaces
before width calculation, truncation, and emission. Built-in and Rich renderers
must use the same sanitized value; CSV/TSV output must remain parseable.

### #224 — immutable command-schema lookups

`CommandSchemaRegistry.schema()` must return a defensive copy. Mutating a
returned mapping must not alter the registry or the module-level compatibility
registry. Registration and validation semantics remain unchanged.

### #225 — validate custom command-protocol headers

Protocol headers must be non-empty, single-line, and free of framing-breaking
control characters (including NUL, CR, LF, and other terminal controls). The
default and documented custom header remain valid. Encode and decode reject the
same invalid headers with bounded, safe error messages.

### #226 — keep internal implementation plans out of published docs

Internal `docs/superpowers` material must not be shipped as an accidental public
documentation surface. Documentation validation must recursively inspect
Markdown and fail on unexpected unnaved pages, while allowing explicitly
documented non-page assets/exclusions. Public docs and their links must continue
to build with strict MkDocs validation.

## Verification and risk controls

The baseline full pytest suite is green before the train. Each PR adds focused
regressions and runs the affected module tests, `ruff`, strict `mypy` where
applicable, and the full pytest suite. The final PR also runs the documentation
validator and strict MkDocs build. Security and framing changes must assert both
positive preservation cases and negative leak/control cases.

