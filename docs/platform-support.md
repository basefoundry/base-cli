# Platform support

`base-cli` is a pure-Python framework. Its Linux support is distribution-neutral
and is validated on Ubuntu, Debian, and Fedora-family environments. The
package does not install or manage operating-system packages; consumers remain
responsible for Python and any external tools their commands need.

## Support matrix

| Environment | Support level | Boundary |
| --- | --- | --- |
| macOS | Supported | Generic Python CLI framework and runtime helpers |
| Ubuntu/Debian | Supported | Representative Linux distributions in CI |
| Fedora/RHEL family | Supported | Representative Fedora-family validation in CI |
| WSL2 | Supported | Python runs inside the Linux distribution |
| Native Windows 10/11 | Supported for `base-cli` core | Click apps, runtime state, logging, history, and test helpers |

Native Windows support applies to the generic Python package. It does not make
Base or `basectl` natively Windows-compatible; those consumers have their own
Unix-tooling and shell boundaries. The package does not provide package-manager
integration, shell startup management, or WSL/Windows path translation.

Recursive invocation-temp content erasure requires descriptor-relative,
no-follow directory operations. Linux, macOS, and WSL2 provide those
primitives; the empty leaf is retained on every platform because portable
POSIX has no identity-bound `rmdir`. Empty nested directories and ancestors are
retained for the same reason. Linux additionally requires readable mount IDs
and fails closed if they are unavailable. Native Windows currently uses the
secure fallback: it retains both directories and files and emits a cleanup
warning rather than perform race-prone pathname recursion.

The supported Python range is Python 3.10 through 3.14. Bug reports should
include the operating system, distribution or WSL version when relevant,
Python version, and whether paths live on the native filesystem or a mounted
filesystem.

## Dependency support

The core runtime dependency contract is Click `>=8.1,<8.6`, tested across the
8.1 through 8.5 lines. YAML configuration
and YAML output use the optional `base-cli[yaml]` extra, which supplies PyYAML
`>=6.0,<7`. The lower bound is the oldest supported line; the upper bound
prevents an unreviewed major release from entering a production install. The
CI [dependency matrix](https://github.com/basefoundry/base-cli/actions/workflows/dependency-matrix.yml)
exercises the supported Click lines on the oldest and newest supported Python
versions. Optional integrations have independent extras and version windows;
see [`api-stability.md`](api-stability.md).

## WSL2

WSL2 is supported when Python runs inside the Linux distribution. Validate a
checkout from the WSL shell with:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install ".[dev]"
.venv/bin/python -m pytest
```

Prefer a checkout in the WSL filesystem (for example, under `~/work`) for
normal development. Windows-mounted paths such as `/mnt/c` remain usable, but
their filesystem performance, case-sensitivity, and permission behavior are
provided by the Windows mount and are outside the Linux filesystem contract.

WSL2 support does not imply that the generic package translates paths between
Linux and Windows or that a consumer's native Windows commands are available
inside the distribution.

On native Windows, private metadata replacement retries only sharing-violation
and lock-violation errors (`winerror` 32 and 33). Access-denied and other
permanent permission/path errors fail immediately. Transient retries are
bounded by a one-second elapsed deadline; the destination remains untouched if
that deadline is exhausted.
