"""Application services for importing and querying operational knowledge."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from oncall.config import Settings, get_settings
from oncall.rag.chunking import chunk_markdown, split_front_matter
from oncall.rag.embeddings import BailianEmbeddingProvider, EmbeddingProvider
from oncall.rag.milvus_store import MilvusKnowledgeStore
from oncall.rag.schemas import (
    IngestionSummary,
    KnowledgeCitation,
    KnowledgeChunk,
    KnowledgeQuery,
    ProjectPack,
)


def _runtime(
    *,
    settings: Settings | None,
    embedding_provider: EmbeddingProvider | None,
    store: MilvusKnowledgeStore | None,
) -> tuple[EmbeddingProvider, MilvusKnowledgeStore]:
    config = None
    if embedding_provider is None or store is None:
        config = settings or get_settings()
    embedding = embedding_provider
    if embedding is None:
        assert config is not None
        embedding = BailianEmbeddingProvider(
            api_key=config.bailian_api_key.get_secret_value(),
            base_url=config.bailian_base_url,
            model=config.bailian_embedding_model,
            dimension=config.bailian_embedding_dimension,
        )
    knowledge_store = store
    if knowledge_store is None:
        assert config is not None
        knowledge_store = MilvusKnowledgeStore(
            uri=config.milvus_uri,
            collection_name=config.milvus_knowledge_collection,
            dimension=embedding.dimension,
        )
    if knowledge_store.dimension != embedding.dimension:
        raise ValueError("embedding provider and Milvus store dimensions must match")
    return embedding, knowledge_store


def _find_project_file(path: Path) -> Path:
    cursor = path if path.is_dir() else path.parent
    for candidate_root in (cursor, *cursor.parents):
        candidate = candidate_root / "project.yaml"
        if candidate.is_file():
            return candidate
    raise ValueError(f"no project.yaml found above {path}")


def _load_project(path: Path, expected_project_id: str) -> ProjectPack:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path} must contain a YAML mapping")
    project = ProjectPack.model_validate(raw)
    if project.project_id != expected_project_id:
        raise ValueError(
            f"CLI project {expected_project_id!r} does not match {path} project_id {project.project_id!r}"
        )
    return project


def _metadata(raw_front_matter: str | None, document: Path) -> dict[str, str]:
    if raw_front_matter is None:
        return {}
    parsed: Any = yaml.safe_load(raw_front_matter)
    if parsed is None:
        return {}
    if not isinstance(parsed, Mapping):
        raise ValueError(f"front matter in {document} must be a YAML mapping")
    allowed = {
        "document_id",
        "document_type",
        "service",
        "environment",
        "title",
        "version",
        "review_status",
    }
    unknown = set(parsed) - allowed
    if unknown:
        raise ValueError(f"unsupported front matter keys in {document}: {sorted(unknown)}")
    return {str(key): str(value) for key, value in parsed.items()}


def _portable_source_path(document: Path, project_file: Path) -> str:
    resolved = document.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return resolved.relative_to(project_file.parent.resolve()).as_posix()


def ingest_project_knowledge(
    project_id: str,
    path: str | Path,
    *,
    settings: Settings | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    store: MilvusKnowledgeStore | None = None,
) -> IngestionSummary:
    """Rebuild one project's Milvus index from reviewed Git Markdown."""

    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise ValueError(f"knowledge path does not exist: {source}")
    documents = sorted(source.rglob("*.md") if source.is_dir() else [source])
    if not documents:
        raise ValueError(f"knowledge path contains no Markdown documents: {source}")
    project_file = _find_project_file(source)
    project = _load_project(project_file, project_id)

    chunks: list[KnowledgeChunk] = []
    for document in documents:
        raw_markdown = document.read_text(encoding="utf-8")
        raw_front_matter, markdown = split_front_matter(raw_markdown)
        chunks.extend(
            chunk_markdown(
                project_id=project_id,
                source_path=_portable_source_path(document, project_file),
                markdown=markdown,
                metadata=_metadata(raw_front_matter, document),
                defaults=project.knowledge_defaults,
            )
        )
    if not chunks:
        raise ValueError("Markdown documents produced no non-empty knowledge chunks")

    embedding, knowledge_store = _runtime(
        settings=settings,
        embedding_provider=embedding_provider,
        store=store,
    )
    # Validate the stored dimension before making a paid embedding request.
    knowledge_store.ensure_collection()
    vectors = embedding.embed_documents([chunk.content for chunk in chunks])
    inserted = knowledge_store.replace_project(chunks, vectors)
    if inserted != len(chunks):
        raise RuntimeError(f"Milvus reported {inserted} inserted chunks; expected {len(chunks)}")
    return IngestionSummary(
        project_id=project_id,
        source_path=source,
        document_count=len(documents),
        chunk_count=len(chunks),
        collection_name=knowledge_store.collection_name,
    )


def search_knowledge(
    query: KnowledgeQuery,
    *,
    settings: Settings | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    store: MilvusKnowledgeStore | None = None,
) -> list[KnowledgeCitation]:
    """Return at most five approved citations from the requested project."""

    embedding, knowledge_store = _runtime(
        settings=settings,
        embedding_provider=embedding_provider,
        store=store,
    )
    knowledge_store.ensure_collection()
    return knowledge_store.hybrid_search(query, embedding.embed_query(query.text))
