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

The core lifecycle, rather than an optional history adapter, owns `run.json`.
Once command context construction succeeds, the file is written with
`status: "running"`. When persistence succeeds, the core writes a terminal
snapshot containing `status`, `outcome`, `exit_code`, `ended_at`, and
`duration_ms`. Terminal status is `ok` for exit code zero, `aborted` for the
interrupt exit code 130, and `error` for other nonzero exit codes. The outcome
discriminator is one of `success`,
`usage_error`, `nonzero_return`, `click_error`, `aborted`, `interrupted`,
`system_exit`, or `unexpected_error`.

History may enrich a matching record with consumer fields, but the core writes
the canonical lifecycle fields last. If terminal persistence fails, the
process keeps its primary result and the framework best-effort removes its
matching or corrupt record rather than leave history data or `running` state
looking authoritative. Writes are not yet promised to be atomic.

The ownership boundary intentionally excludes parser failures, help and version
requests, inherited runtime bindings, `log_to_file=False`, and dry-run mode.
Those invocations do not create or finalize a bundle. If context construction
fails after creating artifacts, rollback closes partial logging handlers and
erases new bundle-local temp files through the retained ownership handle. It
retains log files and empty directory boundaries rather than reopening pathname
replacement races, and does not delete pre-existing content, persistent
component caches, paths outside the selected run root, or a parent runtime's
metadata.

Temp cleanup uses the same fail-closed ownership proof during normal teardown
and startup rollback. The final leaf is claimed exclusively through a stable
parent handle, retained for the invocation, and checked against its captured
filesystem identity. Its path must remain a strict lexical and resolved
descendant of the selected run root, carry the invocation's run ID as its final
component, and contain no symlinked component. Cleanup refuses roots, the run
root itself, replaced directories, traversal paths, mounted targets, external
paths, pre-existing directories, missing Linux mount identities, and anything
it cannot inspect safely.

Files and symlinks are erased relative to the retained directory handle and
cleanup refuses cross-device descendants. All empty directory nodes are
retained: portable POSIX APIs cannot atomically bind `rmdir` to an
already-verified open directory, so pathname removal would reopen a replacement
race at every depth. If the host lacks the required handle operations, files
are retained with a warning. `--keep-temp` also retains files. A refusal never
replaces the command's primary result. The ownership claim assumes the private
per-user runtime tree is not maliciously mutated by another process with the
same account while ownership is acquired or cleanup runs; it is not a
cryptographic proof against a hostile same-account process.

Persistent component caches live under the owner's `cache/components/` path.
On POSIX systems, runtime directories are owner-only (`0700`) and runtime files
are owner-only (`0600`). On Windows, the default `%LOCALAPPDATA%` root relies
on the user-profile ACL inherited by its children; POSIX mode bits cannot
provide the same guarantee there. If `BASE_CLI_CACHE_DIR` points outside the
user profile on Windows, the consumer is responsible for supplying an
appropriately private ACL.
