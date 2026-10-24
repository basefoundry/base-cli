# AI context for base-cli

This directory is the repository's curated orientation for AI-assisted work and
portable export. It summarizes verified repository behavior; it is not generated
runtime evidence or an authoritative release/status registry. Start with
[INDEX.md](INDEX.md).

Keep purpose, architecture, commands, and workflows aligned with code and
canonical documentation. Update the affected pages in the same meaningful PR;
for typo-only or internal changes with no contract impact, record no context
impact in the PR. Resolve disagreements by correcting this pack from its sources.
Do not copy credentials, local databases, private machine paths, personal records,
or transient logs into it. Link to evidence rather than copying runtime data.

From this checkout, with Base available:

```bash
basectl export-context --list-files
basectl export-context --print
```

Markdown exports combine Markdown files in INDEX order, then append unlisted
files deterministically. Links outside this directory are source references;
the exporter does not inline their targets. Keep core explanations self-contained.
Use real regular files, not symlinks. Store generated bundles outside the project
and keep generation timestamps out of source pages so unchanged exports remain
byte-identical. This pack is picked up automatically by workspace export jobs.
