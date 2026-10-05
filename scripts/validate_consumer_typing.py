"""Type-check every example and compatibility consumer at strict settings."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    # Git discovery also includes new, untracked source directories. Infrastructure
    # scripts/tests have separate runtime gates; all product/example code is typed.
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.py"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    files = sorted(set(result.stdout.splitlines()))
    excluded = {"lib", "scripts", "tests"}
    sources = [name for name in files if Path(name).parts[0] not in excluded]
    if not sources:
        raise RuntimeError("No consumer Python sources found")
    print("Consumer typing sources:")
    print("\n".join(f"- {source}" for source in sources))
    return subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--explicit-package-bases", *sources],
        cwd=root,
        env={
            **os.environ,
            "MYPYPATH": os.pathsep.join(
                str(p)
                for p in [
                    root / "lib/python",
                    *sorted(root.glob("examples/*/src")),
                    *sorted(root.glob("compatibility/consumers/*/src")),
                ]
            ),
        },
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
