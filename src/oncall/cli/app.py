"""Root Typer application exposed as the ``oncall`` command."""

import typer

from oncall.cli.demo_release import app as demo_release_app
from oncall.cli.knowledge import app as knowledge_app
from oncall.cli.users import app as users_app


app = typer.Typer(help="智能 OnCall Agent 运维命令行。")
app.add_typer(users_app, name="users")
app.add_typer(knowledge_app, name="knowledge")
app.add_typer(demo_release_app, name="demo-release")
