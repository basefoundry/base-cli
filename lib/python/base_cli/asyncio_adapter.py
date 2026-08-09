"""Explicit asyncio support for commands that opt into an async boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

__all__ = ["run_async"]

_T = TypeVar("_T")


async def _await_result(awaitable: Awaitable[_T]) -> _T:
    return await awaitable


def run_async(awaitable: Awaitable[_T]) -> _T:
    """Run one awaitable with an adapter-owned event loop.

    ``base_cli`` invokes this helper from synchronous Click callbacks. The
    adapter owns the loop for the duration of the command, and ``asyncio.run``
    cancels pending tasks and closes the loop before returning. Calling it from
    an already-running event loop is rejected so a consumer cannot accidentally
    create nested-loop behavior with ambiguous cancellation or signal rules.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await_result(awaitable))

    close = getattr(awaitable, "close", None)
    if callable(close):
        close()
    raise RuntimeError(
        "base_cli.run_async() owns the event loop for a CLI invocation and "
        "cannot be called while another event loop is running."
    )
