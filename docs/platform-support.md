# Platform support

`base-cli` is a pure-Python framework. Its Linux support is distribution-neutral
and is validated on Ubuntu, Debian, and Fedora-family environments. The
package does not install or manage operating-system packages; consumers remain
responsible for Python and any external tools their commands need.

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
