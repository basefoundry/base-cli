"""Small strict-typing example for the public base-cli extension contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import base_cli


@dataclass(frozen=True)
class ApplicationState:
    invocation_count: int = 0


@dataclass(frozen=True)
class Services:
    workspace: Path


Config = dict[str, Any]
TypedContext = base_cli.Context[Config, ApplicationState, Services]


def profile() -> base_cli.CliProfile:
    """Return a profile whose extension points use only public contracts."""

    def resolve_runtime(
        cli_name: str,
        project: base_cli.ProjectInfo | None,
    ) -> base_cli.RuntimeBinding:
        root = Path(".base-cli-cache").resolve()
        run_id = "sample-run"
        layout = base_cli.RuntimeLayout(
            owner_root=root / cli_name,
            run_root=root / cli_name / "runs" / run_id,
            state_dir=root / cli_name,
            log_dir=root / cli_name / "runs" / run_id / "logs",
            cache_dir=root / cli_name / "cache",
            temp_dir=root / cli_name / "runs" / run_id / "tmp",
        )
        return base_cli.RuntimeBinding(
            cache_root=root,
            layout=layout,
            application_home=None,
            runtime_owner="typed-sample",
            project_root=project.root if project is not None else None,
            project_name=project.name if project is not None else None,
            inherited_path=None,
            history_parent_run_id=None,
            run_id=run_id,
        )

    return base_cli.CliProfile.generic(resolve_runtime=resolve_runtime)


app = base_cli.App(
    name="typed-sample",
    profile=profile(),
    log_to_file=False,
)


@app.command()
@base_cli.option("--verbose", is_flag=True)
def main(ctx: TypedContext, verbose: bool) -> None:
    """Use a consumer-owned context payload without private imports."""

    del verbose
    assert isinstance(ctx.config, dict)
    _ = ctx.application_context
    _ = ctx.services


if __name__ == "__main__":
    raise SystemExit(base_cli.run_app(app))
