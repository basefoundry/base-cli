from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ._private_files import write_private_json
from ._runtime import refresh_run_bundle_index
from .context import Context
from .exit_codes import ExitCode
from .history import compact_optional_path, format_timestamp, status_for_exit_code


@dataclass(frozen=True)
class InvocationOutcome:
    """Private normalized result of one lifecycle-owned invocation."""

    kind: str
    status: str
    exit_code: int


@dataclass(frozen=True)
class RunRecorder:
    """Write core-owned lifecycle snapshots for one Context."""

    context: Context[Any, Any, Any]
    started_at: datetime
    started_monotonic_ns: int

    def start(self) -> None:
        if self.context._run_metadata_path is None:
            return
        write_private_json(
            self.context._run_metadata_path,
            self._metadata(status="running"),
        )

    def finish(
        self,
        outcome: InvocationOutcome,
        *,
        ended_at: datetime,
        ended_monotonic_ns: int,
    ) -> None:
        if self.context._run_metadata_path is None:
            return
        elapsed_ns = max(0, ended_monotonic_ns - self.started_monotonic_ns)
        metadata = self._existing_metadata()
        metadata.update(self._metadata(status=outcome.status))
        metadata.update(
            {
                "outcome": outcome.kind,
                "exit_code": outcome.exit_code,
                "ended_at": format_timestamp(ended_at),
                "duration_ms": round(elapsed_ns / 1_000_000),
            }
        )
        write_private_json(self.context._run_metadata_path, metadata)
        owner_root = self.context.owner_root
        if owner_root is not None:
            refresh_run_bundle_index(owner_root / "runs", logger=self.context.log)

    def _existing_metadata(self) -> dict[str, Any]:
        path = self.context._run_metadata_path
        if path is None:
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict) or payload.get("run_id") != self.context.run_id:
            return {}
        return dict(payload)

    def discard_owned_record(self) -> None:
        """Remove our misleading record after terminal persistence fails."""
        path = self.context._run_metadata_path
        if path is None:
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("run_id") == self.context.run_id:
                path.unlink()
        except (UnicodeDecodeError, json.JSONDecodeError):
            try:
                path.unlink()
            except OSError:
                pass
        except OSError:
            pass

    def _metadata(self, *, status: str) -> dict[str, Any]:
        context = self.context
        return {
            "schema_version": 1,
            "run_id": context.run_id,
            "owner": context.runtime_owner,
            "cli": context.cli_name,
            "status": status,
            "started_at": format_timestamp(self.started_at),
            "project": context.project_name,
            "project_root": compact_optional_path(context.project_root),
            "manifest": compact_optional_path(context.manifest_path),
            "workspace_root": compact_optional_path(context.workspace_root),
            # ``--keep-temp`` is an explicit request to retain diagnostics;
            # retention therefore protects the complete invocation bundle,
            # not only its temporary directory.
            "preserve": context.keep_temp,
        }


def outcome_from_exit_code(exit_code: int) -> InvocationOutcome:
    if exit_code == ExitCode.SUCCESS:
        return InvocationOutcome("success", "ok", exit_code)
    if exit_code == ExitCode.USAGE_ERROR:
        return InvocationOutcome("usage_error", "error", exit_code)
    return InvocationOutcome("nonzero_return", status_for_exit_code(exit_code), exit_code)


def outcome_from_exception(click: Any, exc: BaseException) -> InvocationOutcome:
    if isinstance(exc, KeyboardInterrupt):
        return InvocationOutcome("interrupted", "aborted", ExitCode.INTERRUPTED)
    if isinstance(exc, EOFError):
        return InvocationOutcome("aborted", "error", ExitCode.FAILURE)
    if isinstance(exc, click.Abort):
        if isinstance(exc.__cause__, KeyboardInterrupt):
            return InvocationOutcome("interrupted", "aborted", ExitCode.INTERRUPTED)
        return InvocationOutcome("aborted", "error", ExitCode.FAILURE)
    if isinstance(exc, click.exceptions.Exit):
        exit_code = _click_exception_exit_code(exc)
        if exit_code is None:
            return InvocationOutcome("unexpected_error", "error", ExitCode.FAILURE)
        return outcome_from_exit_code(exit_code)
    if isinstance(exc, click.UsageError):
        exit_code = _click_exception_exit_code(exc)
        if exit_code is None:
            return InvocationOutcome("unexpected_error", "error", ExitCode.FAILURE)
        return InvocationOutcome("usage_error", status_for_exit_code(exit_code), exit_code)
    if isinstance(exc, click.ClickException):
        exit_code = _click_exception_exit_code(exc)
        if exit_code is None:
            return InvocationOutcome("unexpected_error", "error", ExitCode.FAILURE)
        return InvocationOutcome("click_error", status_for_exit_code(exit_code), exit_code)
    if isinstance(exc, SystemExit):
        exit_code = system_exit_code(exc)
        return InvocationOutcome("system_exit", status_for_exit_code(exit_code), exit_code)
    return InvocationOutcome("unexpected_error", "error", ExitCode.FAILURE)


def system_exit_code(exc: SystemExit) -> int:
    if exc.code is None:
        return ExitCode.SUCCESS
    if isinstance(exc.code, int):
        return int(exc.code)
    return ExitCode.FAILURE


def _click_exception_exit_code(exc: Any) -> int | None:
    try:
        return int(exc.exit_code)
    except BaseException:  # pylint: disable=broad-exception-caught
        return None
