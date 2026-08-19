# JSON Retention Precedence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make implicit JSON-mode retention count-only (20 bundles) while preserving explicit retention, human-mode defaults, and the public `max_log_files` compatibility path.

**Architecture:** Keep `App.retention` as the configured policy value and add one private boolean recording whether retention was explicitly configured. At context creation, select explicit policy first, then legacy `max_log_files`, then JSON’s count-only default, and finally the existing human-mode safe defaults. Validate the behavior through real invocation tests using aged run bundles.

**Tech Stack:** Python 3.10+, Click, `unittest`, `pytest`, `RetentionPolicy`, `base_cli.testing.invoke`, Ruff, mypy.

---

### Task 1: Add failing JSON-retention behavior tests

**Files:**
- Modify: `tests/test_json_contracts.py` near `JsonContractTests.test_json_mode_is_opt_in_and_human_output_remains_unchanged`
- Read: `lib/python/base_cli/_runtime.py` for run-bundle metadata shape

- [ ] **Step 1: Add a local aged-bundle helper and three behavior tests**

Add imports for `write_private_json` from `base_cli._private_files`, then add these helpers and tests inside `JsonContractTests`:

```python
    def _write_aged_bundle(self, home: Path, app_name: str) -> Path:
        bundle = home / ".cache" / app_name / "runs" / "aged"
        (bundle / "logs").mkdir(parents=True)
        (bundle / "logs" / "primary.log").write_text("old\n", encoding="utf-8")
        write_private_json(
            bundle / "run.json",
            {
                "run_id": "aged",
                "status": "ok",
                "started_at": "2020-01-01T00:00:00Z",
                "preserve": False,
            },
        )
        return bundle

    def _retention_app(self, name: str, **kwargs: object) -> base_cli.App:
        return base_cli.App(
            name=name,
            lifecycle_options=self._lifecycle_options(),
            **kwargs,
        )

    def test_implicit_json_retention_is_count_only(self) -> None:
        app = self._retention_app("json-count-only")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            aged = self._write_aged_bundle(home, "json-count-only")
            result = base_cli.testing.invoke(app, ["--json"], home=home)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(aged.exists())

    def test_explicit_json_retention_overrides_count_only_default(self) -> None:
        app = self._retention_app(
            "json-explicit-retention",
            retention=base_cli.RetentionPolicy(max_age_seconds=60),
        )

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            aged = self._write_aged_bundle(home, "json-explicit-retention")
            result = base_cli.testing.invoke(app, ["--json"], home=home)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertFalse(aged.exists())

    def test_implicit_human_retention_keeps_safe_defaults(self) -> None:
        app = base_cli.App(name="human-safe-defaults")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            aged = self._write_aged_bundle(home, "human-safe-defaults")
            result = base_cli.testing.invoke(app, home=home)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertFalse(aged.exists())
```

- [ ] **Step 2: Run the new tests and verify the expected RED state**

Run:

```bash
UV_CACHE_DIR=/private/tmp/base-cli-uv-cache uv run --extra dev --extra typer pytest tests/test_json_contracts.py -q
```

Expected: the new implicit JSON test fails because the aged bundle is removed by the current safe-default age bound; the explicit and human tests pass.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_json_contracts.py
git commit -m "test: expose JSON retention precedence bug"
```

### Task 2: Implement the minimal retention precedence fix

**Files:**
- Modify: `lib/python/base_cli/_app_core.py:469-497` (`App.__init__`)
- Modify: `lib/python/base_cli/_app_core.py:1240-1275` (`_create_context` pruning selection)

- [ ] **Step 1: Record whether retention was explicitly configured**

Before the retention-selection branches in `App.__init__`, assign:

```python
        self._retention_explicit = retention is not None or any(
            value is not None for value in (max_run_bundles, max_run_age_seconds, max_run_total_bytes)
        )
```

Use the existing validation and policy construction unchanged. Do not remove or rename `max_log_files`.

- [ ] **Step 2: Make implicit JSON selection precede implicit human defaults**

Replace the pruning condition with this exact precedence shape:

```python
            if uses_default_log_file and log_file is not None:
                if self.retention is not None and (self._retention_explicit or not context.json_output):
                    prune_run_bundles(
                        layout.owner_root / "runs",
                        layout.run_root,
                        policy=self.retention,
                        logger=context.log,
                    )
                elif self.max_log_files is not None:
                    # Compatibility for the original public option.  The
                    # legacy pass handles pre-metadata flat log directories;
                    # metadata-backed runs are routed to bundle retention by
                    # the helper itself.
                    prune_log_files(
                        layout.owner_root / "runs",
                        log_file,
                        self.max_log_files,
                        context.log,
                    )
                    prune_run_bundles(
                        layout.owner_root / "runs",
                        layout.run_root,
                        policy=RetentionPolicy(max_bundles=self.max_log_files),
                        logger=context.log,
                    )
                elif context.json_output:
                    prune_run_bundles(
                        layout.owner_root / "runs",
                        layout.run_root,
                        policy=RetentionPolicy(max_bundles=_JSON_DEFAULT_MAX_LOG_FILES),
                        logger=context.log,
                    )
```

The first branch covers explicit policies and implicit human defaults. The JSON branch is now reachable only for an implicit JSON invocation. The legacy branch remains unchanged.

- [ ] **Step 3: Run the focused tests and verify GREEN**

Run:

```bash
UV_CACHE_DIR=/private/tmp/base-cli-uv-cache uv run --extra dev --extra typer pytest tests/test_json_contracts.py -q
```

Expected: all JSON contract tests pass, including the three new retention tests.

- [ ] **Step 4: Commit the implementation**

```bash
git add lib/python/base_cli/_app_core.py
git commit -m "fix: honor JSON-only retention defaults"
```

### Task 3: Document and validate the complete change

**Files:**
- Modify: `CHANGELOG.md` under `## [Unreleased]` / `### Changed`
- Validate: `tests/test_json_contracts.py`, full `tests/`, docs, type, and quality gates

- [ ] **Step 1: Add the changelog entry**

Add:

```markdown
- Apply the documented count-only 20-bundle retention default to implicit JSON
  mode while preserving explicit retention policies, human-mode safe defaults,
  and the `max_log_files` compatibility path.
```

- [ ] **Step 2: Run focused and full verification**

```bash
UV_CACHE_DIR=/private/tmp/base-cli-uv-cache uv run --extra dev --extra typer pytest tests/test_json_contracts.py -q
UV_CACHE_DIR=/private/tmp/base-cli-uv-cache uv run --extra dev --extra typer --extra quality pytest -q
UV_CACHE_DIR=/private/tmp/base-cli-uv-cache uv run --extra quality ruff format --check lib/python/base_cli/_app_core.py tests/test_json_contracts.py
UV_CACHE_DIR=/private/tmp/base-cli-uv-cache uv run --extra quality ruff check lib/python/base_cli/_app_core.py tests/test_json_contracts.py
UV_CACHE_DIR=/private/tmp/base-cli-uv-cache uv run --extra quality mypy --no-incremental --strict lib/python/base_cli
./tests/validate.sh
git diff --check
```

Expected: every command exits 0; pytest reports no failures; Ruff and mypy report no issues.

- [ ] **Step 3: Commit the changelog**

```bash
git add CHANGELOG.md
git commit -m "docs: note JSON retention precedence fix"
```

### Task 4: Publish, merge, and clean up

**Files:**
- No additional source files; publish the design, test, implementation, and changelog commits.

- [ ] **Step 1: Inspect status and push the canonical branch**

```bash
git status --short --branch
git log --oneline --decorate -5
git push -u origin bug/177-20260819-dead-json-only-retention-branch-in-create-context-can-never
```

- [ ] **Step 2: Open a PR linked to #177**

Use a ready PR because the user authorized the normal merge train:

```bash
gh pr create --repo basefoundry/base-cli --base main \
  --head bug/177-20260819-dead-json-only-retention-branch-in-create-context-can-never \
  --title "Fix JSON-mode retention precedence (#177)" \
  --body-file /private/tmp/base-cli-pr-177.md
```

The PR body must summarize the precedence fix, explicitly state that `max_log_files` is preserved, list the verification commands, and include `Closes #177.`

- [ ] **Step 3: Wait for required CI and merge**

```bash
gh pr checks <pr-number> --repo basefoundry/base-cli --watch --interval 15
gh pr view <pr-number> --repo basefoundry/base-cli --json state,mergeStateStatus,mergeable,headRefOid,url
gh pr merge <pr-number> --repo basefoundry/base-cli --squash --delete-branch
```

Merge only when the PR is `OPEN`, `MERGEABLE`, `CLEAN`, and all required checks pass.

- [ ] **Step 4: Mark the issue Done and verify Project metadata**

```bash
BASE_CACHE_DIR=/private/tmp/base-cli-issue-177 /Users/rameshhp/work/base/bin/basectl gh project issue set-fields 177 --repo basefoundry/base-cli --project base-cli --owner basefoundry --status Done --priority P3 --area Runtime --initiative 'v1.0 Readiness' --size T
gh project item-list 14 --owner basefoundry --limit 200 --format json | jq '.items[] | select(.content.number == 177)'
```

Expected: issue #177 is closed, linked to the merged PR, and remains assigned to `codeforester` with P3/Runtime/v1.0 Readiness/T metadata.

- [ ] **Step 5: Remove only the #177 worktree and branch, then sync main**

```bash
git -C /Users/rameshhp/work/base-cli worktree remove /Users/rameshhp/work/base-cli-worktrees/177-dead-json-only-retention-branch-in-creat
git -C /Users/rameshhp/work/base-cli branch -D bug/177-20260819-dead-json-only-retention-branch-in-create-context-can-never
git -C /Users/rameshhp/work/base-cli fetch origin --prune
git -C /Users/rameshhp/work/base-cli pull --ff-only origin main
git -C /Users/rameshhp/work/base-cli status --short --branch
```

Expected: only the main checkout remains for #177, it is clean and synchronized, and the remote issue branch is deleted.
