from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ._private_files import write_private_json
from .context import Context
from .exit_codes import ExitCode
from .history import format_timestamp


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
            if (
                isinstance(payload, dict)
                and payload.get("run_id") == self.context.run_id
            ):
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
            "project_root": str(context.project_root) if context.project_root else None,
            "manifest": str(context.manifest_path) if context.manifest_path else None,
            "workspace_root": str(context.workspace_root) if context.workspace_root else None,
        }


def outcome_from_exit_code(exit_code: int) -> InvocationOutcome:
    if exit_code == ExitCode.SUCCESS:
        return InvocationOutcome("success", "ok", exit_code)
    if exit_code == ExitCode.USAGE_ERROR:
        return InvocationOutcome("usage_error", "error", exit_code)
    if exit_code == ExitCode.INTERRUPTED:
        return InvocationOutcome("interrupted", "error", exit_code)
    return InvocationOutcome("nonzero_return", "error", exit_code)


def outcome_from_exception(click: Any, exc: BaseException) -> InvocationOutcome:
    if isinstance(exc, KeyboardInterrupt):
        return InvocationOutcome("interrupted", "error", ExitCode.INTERRUPTED)
    if isinstance(exc, EOFError):
        return InvocationOutcome("aborted", "error", ExitCode.FAILURE)
    if isinstance(exc, click.Abort):
        if isinstance(exc.__cause__, KeyboardInterrupt):
            return InvocationOutcome("interrupted", "error", ExitCode.INTERRUPTED)
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
        return InvocationOutcome("usage_error", _status_for_exit_code(exit_code), exit_code)
    if isinstance(exc, click.ClickException):
        exit_code = _click_exception_exit_code(exc)
        if exit_code is None:
            return InvocationOutcome("unexpected_error", "error", ExitCode.FAILURE)
        return InvocationOutcome("click_error", _status_for_exit_code(exit_code), exit_code)
    if isinstance(exc, SystemExit):
        exit_code = system_exit_code(exc)
        return InvocationOutcome("system_exit", _status_for_exit_code(exit_code), exit_code)
    return InvocationOutcome("unexpected_error", "error", ExitCode.FAILURE)


def system_exit_code(exc: SystemExit) -> int:
    if exc.code is None:
        return ExitCode.SUCCESS
    if isinstance(exc.code, int):
        return int(exc.code)
    return ExitCode.FAILURE


def _status_for_exit_code(exit_code: int) -> str:
    return "ok" if exit_code == ExitCode.SUCCESS else "error"


def _click_exception_exit_code(exc: Any) -> int | None:
    try:
        return int(exc.exit_code)
    except BaseException:  # pylint: disable=broad-exception-caught
        return None
