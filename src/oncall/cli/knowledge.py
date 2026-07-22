"""CLI for explicitly rebuilding the operational knowledge index."""

from pathlib import Path
from typing import Annotated

import typer

from oncall.rag.service import ingest_project_knowledge


app = typer.Typer(help="导入已审核的 Markdown SOP 和 Runbook。")


@app.command("ingest")
def ingest(
    project_id: Annotated[str, typer.Option("--project", help="project.yaml 中的项目 ID。")],
    path: Annotated[Path, typer.Option("--path", exists=True, readable=True, help="Markdown 文件或目录。")],
) -> None:
    """Rebuild a project's Milvus knowledge index from Git-managed Markdown."""

    summary = ingest_project_knowledge(project_id, path)
    typer.echo(
        f"已导入项目 {summary.project_id}: {summary.document_count} 个文档，"
        f"{summary.chunk_count} 个 Chunk，Collection={summary.collection_name}"
    )
