# Contributing to base-cli

Thank you for improving this project.

Please read the [Code of Conduct](CODE_OF_CONDUCT.md) before participating.

## Workflow

1. Create or choose a GitHub issue before starting implementation work.
2. Use exactly one standard issue category label: `bug`, `enhancement`,
   `documentation`, `ci`, or `security`.
3. Create an issue-backed branch:

   ```text
   <category>/<issue>-<YYYYMMDD>-<slug>
   ```

   The category prefix must match the issue's single standard category label.
   This branch shape is tool-independent; `feat/`, `agent/`, `codex/`, and
   bare issue-number prefixes are invalid.

4. Use a dedicated Git worktree for each pull request so the main checkout can
   stay on the default branch:

   ```bash
   git fetch origin
   git worktree add -b <branch> ../base-cli-worktrees/<slug> origin/<default-branch>
   ```

5. Keep the pull request scoped to the issue and link it with
   `Fixes #<issue>` or `Closes #<issue>` when merge should close the issue.
6. Run the project checks before opening or updating a pull request.
7. Update `CHANGELOG.md` only for notable user-visible or release-worthy
   changes.
8. After merge, sync the default branch, remove the worktree, and delete merged
   local and remote branches when safe:

   ```bash
   git pull --ff-only origin <default-branch>
   git worktree remove ../base-cli-worktrees/<slug>
   git branch -d <branch>
   git push origin --delete <branch>
   ```

Useful commands:

```bash
basectl check base-cli
basectl doctor base-cli
basectl test base-cli
```
