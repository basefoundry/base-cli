"""Capture process stdout, including inherited child descriptors, for JSON runs."""

from __future__ import annotations

import codecs
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from threading import Event, Thread
from typing import TextIO


@contextmanager
def capture_stdout(sink: TextIO, limit: int, limit_error: type[Exception]) -> Iterator[None]:
    """Drain fd 1 concurrently, restore it, then replay through the JSON limiter.

    The invocation owns the process output boundary. Children must be waited for
    before returning; a child retaining stdout is a deterministic capture error.
    """
    original = sys.stdout
    original.flush()
    saved = os.dup(1)
    read_fd, write_fd = os.pipe()
    spool = tempfile.SpooledTemporaryFile(max_size=1_048_576, mode="w+b")
    abandoned = Event()
    errors: list[BaseException] = []
    overflow = False

    def drain() -> None:
        nonlocal overflow
        total = 0
        try:
            with os.fdopen(read_fd, "rb", buffering=0) as reader:
                while chunk := reader.read(65536):
                    if abandoned.is_set():
                        break
                    total += len(chunk)
                    # Unresolved parser output retains the existing deferred
                    # spool semantics. Once JSON is selected, native writers
                    # are bounded too; continue draining to avoid child deadlock.
                    json_mode = not hasattr(sink, "json_output") or bool(sink.json_output)
                    if json_mode and total > limit:
                        overflow = True
                        continue
                    spool.write(chunk)
        except BaseException as exc:
            errors.append(exc)
        finally:
            if abandoned.is_set():
                spool.close()

    worker = Thread(target=drain, name="base-cli-stdout-capture", daemon=True)
    writer: TextIO | None = None
    try:
        worker.start()
        os.dup2(write_fd, 1)
        os.close(write_fd)
        write_fd = -1
        writer = os.fdopen(os.dup(1), "w", encoding="utf-8", errors="strict", buffering=1)
        with redirect_stdout(writer):
            try:
                yield
            finally:
                writer.flush()
                # sys.__stdout__ can have its own Python buffering.
                if sys.__stdout__ is not None and sys.__stdout__ is not writer:
                    try:
                        sys.__stdout__.flush()
                    except (OSError, ValueError):
                        pass
    finally:
        try:
            if writer is not None:
                writer.close()
        finally:
            os.dup2(saved, 1)
            os.close(saved)
            if write_fd != -1:
                os.close(write_fd)
        worker.join(timeout=2)
        if worker.is_alive():
            abandoned.set()
            raise limit_error(
                "A child retained stdout after the command returned; wait for all child processes in JSON mode."
            )
        try:
            if errors:
                raise OSError("Could not capture process stdout") from errors[0]
            if overflow:
                size = f"{limit // 1_048_576} MiB" if limit % 1_048_576 == 0 else f"{limit} bytes"
                raise limit_error(f"JSON stdout exceeded the {size} limit; use NDJSON for large record sets.")
            spool.seek(0)
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            while chunk := spool.read(65536):
                sink.write(decoder.decode(chunk))
            sink.write(decoder.decode(b"", final=True))
        finally:
            spool.close()
