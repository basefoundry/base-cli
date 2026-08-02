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

The supported Python range is Python 3.10 through 3.14. Bug reports should
include the operating system, distribution or WSL version when relevant,
Python version, and whether paths live on the native filesystem or a mounted
filesystem.

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
