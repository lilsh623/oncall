"""Operator-only CLI for the deterministic demo release state."""

from typing import Annotated

import typer

from oncall.demo.release_state import DemoVersion, synchronize_release_state


app = typer.Typer(help="在验证真实 demo 版本后同步只读发布事实。")


@app.command("sync")
def sync(
    version: Annotated[DemoVersion, typer.Option("--version", case_sensitive=True)],
) -> None:
    """Only accept v1/v2 and never accept a path, URL, or command string."""

    changed = synchronize_release_state(version)
    result = "已更新" if changed else "已一致"
    typer.echo(f"demo 发布事实{result}: {version.value}")
