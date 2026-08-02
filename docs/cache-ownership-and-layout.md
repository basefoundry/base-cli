# Cache ownership and layout

Runtime state is rooted at the cache root supplied to `CliProfile.generic()` or
the platform cache directory. The generic profile places each application in a
sanitized application namespace and does not impose a product-wide cache name.

When no explicit cache root is supplied, the generic profile follows these
platform conventions:

- Linux and WSL2: `XDG_CACHE_HOME`, or `~/.cache` when it is unset;
- macOS: `~/Library/Caches`; and
- Windows: `%LOCALAPPDATA%`, with `~/AppData/Local` as a fallback.

`BASE_CLI_CACHE_DIR` overrides these defaults on every platform. WSL2 is
supported when the process runs inside the Linux distribution; Windows-mounted
paths such as `/mnt/c` retain their own filesystem performance and permission
characteristics.

Consumer profiles may choose a different cache root or owner-aware layout when
their application needs stronger isolation between projects or checkouts.

Each invocation has a private run bundle containing:

- `run.json` for lifecycle metadata;
- `logs/` for diagnostic logs; and
- `tmp/` for temporary command data.

Persistent component caches live under the owner's `cache/components/` path.
On POSIX systems, runtime directories are owner-only (`0700`) and runtime files
are owner-only (`0600`). On Windows, the default `%LOCALAPPDATA%` root relies
on the user-profile ACL inherited by its children; POSIX mode bits cannot
provide the same guarantee there. If `BASE_CLI_CACHE_DIR` points outside the
user profile on Windows, the consumer is responsible for supplying an
appropriately private ACL.
