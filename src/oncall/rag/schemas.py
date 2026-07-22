"""Typed contracts shared by knowledge ingestion and retrieval."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeChunk(BaseModel):
    """A rebuildable Milvus row derived from one Markdown document."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str = Field(min_length=64, max_length=64)
    project_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    document_id: str = Field(min_length=1, max_length=256)
    document_type: str = Field(default="sop", min_length=1, max_length=64)
    service: str = Field(default="", max_length=128)
    environment: str = Field(default="", max_length=64)
    title: str = Field(min_length=1, max_length=512)
    section_path: str = Field(max_length=2048)
    content: str = Field(min_length=1, max_length=65535)
    source_path: str = Field(min_length=1, max_length=2048)
    version: str = Field(default="1.0.0", min_length=1, max_length=64)
    review_status: Literal["approved", "draft", "rejected"] = "draft"


class KnowledgeQuery(BaseModel):
    """A project-scoped knowledge request with optional scalar filters."""

    model_config = ConfigDict(frozen=True)

    project_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    text: str = Field(min_length=1, max_length=4000)
    service: str | None = Field(default=None, max_length=128)
    environment: str | None = Field(default=None, max_length=64)
    document_type: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=5, ge=1, le=5)


class KnowledgeCitation(BaseModel):
    """Evidence returned to an Agent, including a Git source reference."""

    document_id: str
    title: str
    version: str
    section_path: str
    source_path: str
    content: str
    score: float


class IngestionSummary(BaseModel):
    """Small, CLI-friendly report for one project import."""

    project_id: str
    source_path: Path
    document_count: int
    chunk_count: int
    collection_name: str


class ProjectKnowledgeDefaults(BaseModel):
    """Metadata inherited by Markdown files in a project pack."""

    document_type: str = "sop"
    service: str = ""
    environment: str = ""
    version: str = "1.0.0"
    review_status: Literal["approved", "draft", "rejected"] = "draft"


class ProjectPack(BaseModel):
    """The V1 subset of ``project.yaml`` used by knowledge ingestion."""

    project_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    name: str
    knowledge_defaults: ProjectKnowledgeDefaults = Field(default_factory=ProjectKnowledgeDefaults)
