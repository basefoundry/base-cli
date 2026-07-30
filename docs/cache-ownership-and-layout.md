# Cache ownership and layout

Runtime state is rooted at `~/Library/Caches/base` on macOS and `~/.cache/base`
elsewhere. Set `BASE_CACHE_DIR` to override the root.

The `base` owner stores Base control-plane runs directly below `base/`. A
project-owned runtime uses `projects/<project>/<checkout-id>/` so separate
checkouts do not share mutable run state accidentally.

Each invocation has a private run bundle containing:

- `run.json` for lifecycle metadata;
- `logs/` for diagnostic logs; and
- `tmp/` for temporary command data.

Persistent component caches live under the owner's `cache/components/` path.
Runtime directories are owner-only (`0700`), and runtime files are owner-only
(`0600`).
