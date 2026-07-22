"""Root Typer application exposed as the ``oncall`` command."""

import typer

from oncall.cli.users import app as users_app


app = typer.Typer(help="智能 OnCall Agent 运维命令行。")
app.add_typer(users_app, name="users")
