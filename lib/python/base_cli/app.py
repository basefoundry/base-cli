"""Compatibility facade for the public application API.

The implementation is kept in :mod:`base_cli._app_core` so the historical
``base_cli.app`` import path remains stable while internal lifecycle seams can
be organized without changing consumer-facing imports.
"""

from __future__ import annotations

import sys as _sys

from . import _app_core as _implementation

# Keep ``import base_cli.app as app_module`` patchable for existing consumers
# and tests that intentionally inspect the private implementation boundary.
# The alias also means private compatibility names continue to resolve exactly
# as they did before the decomposition.
_sys.modules[__name__] = _implementation
