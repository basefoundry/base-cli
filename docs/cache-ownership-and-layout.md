# Cache ownership and layout

Runtime state is rooted at the cache root supplied to `CliProfile.generic()` or
the platform cache directory. The generic profile places each application in a
sanitized application namespace and does not impose a product-wide cache name.

Consumer profiles may choose a different cache root or owner-aware layout when
their application needs stronger isolation between projects or checkouts.

Each invocation has a private run bundle containing:

- `run.json` for lifecycle metadata;
- `logs/` for diagnostic logs; and
- `tmp/` for temporary command data.

Persistent component caches live under the owner's `cache/components/` path.
Runtime directories are owner-only (`0700`), and runtime files are owner-only
(`0600`).
