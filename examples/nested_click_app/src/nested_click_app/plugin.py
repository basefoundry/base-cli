"""A deliberately tiny plugin loaded through the public entry-point API."""

from __future__ import annotations


def install() -> dict[str, str]:
    """Return plugin metadata; a real product would register commands here."""

    return {"name": "sample", "status": "loaded"}
