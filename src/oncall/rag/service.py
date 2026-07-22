"""Application services for importing and querying operational knowledge."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Any, Iterator, Mapping

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


def _find_repository_root(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _ensure_inside_without_symlinks(path: Path, root: Path) -> None:
    """Reject path traversal and symlink escapes from a project pack."""

    root_absolute = Path(os.path.abspath(root))
    path_absolute = Path(os.path.abspath(path))
    try:
        relative = path_absolute.relative_to(root_absolute)
        path_absolute.resolve().relative_to(root_absolute.resolve())
    except ValueError as error:
        raise ValueError(f"knowledge path {path} resolves outside project pack {root}") from error

    current = root_absolute
    if current.is_symlink():
        raise ValueError(f"symlinks are not allowed in knowledge imports: {current}")
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"symlinks are not allowed in knowledge imports: {current}")


def _discover_documents(source: Path, project_root: Path) -> list[Path]:
    """Collect only regular ``.md`` files beneath the selected safe root."""

    _ensure_inside_without_symlinks(source, project_root)
    if source.is_file():
        if source.suffix != ".md":
            raise ValueError(f"knowledge import only accepts .md files, not {source.name}")
        return [source]
    if not source.is_dir():
        raise ValueError(f"knowledge path is not a regular file or directory: {source}")

    documents: list[Path] = []
    for directory, directory_names, file_names in os.walk(source, followlinks=False):
        directory_path = Path(directory)
        for name in (*directory_names, *file_names):
            candidate = directory_path / name
            if candidate.is_symlink():
                raise ValueError(f"symlinks are not allowed in knowledge imports: {candidate}")
        for name in file_names:
            candidate = directory_path / name
            if candidate.suffix == ".md":
                _ensure_inside_without_symlinks(candidate, source)
                _ensure_inside_without_symlinks(candidate, project_root)
                documents.append(candidate)
    return sorted(documents)


@contextmanager
def _project_ingestion_lock(project_id: str, project_file: Path) -> Iterator[None]:
    """Serialize local CLI imports without relying on Milvus transactions."""

    repository_root = _find_repository_root(project_file.parent)
    if repository_root is not None:
        lock_directory = repository_root / ".runtime/knowledge-locks"
    else:
        project_key = hashlib.sha256(str(project_file.parent.resolve()).encode("utf-8")).hexdigest()
        lock_directory = Path(tempfile.gettempdir()) / "intelligent-oncall-knowledge-locks" / project_key
    lock_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_name = hashlib.sha256(project_id.encode("utf-8")).hexdigest() + ".lock"
    lock_path = lock_directory / lock_name
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"knowledge import already running for project {project_id!r}") from error
        try:
            lock_file.seek(0)
            lock_file.truncate()
            lock_file.write(str(os.getpid()))
            lock_file.flush()
            yield
        finally:
            # Keep the empty inode to avoid an unlink/recreate race with waiters.
            lock_file.seek(0)
            lock_file.truncate()
            lock_file.flush()
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


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
    repository_root = _find_repository_root(project_file.parent)
    reference_root = repository_root or project_file.parent
    return resolved.relative_to(reference_root.resolve()).as_posix()


def ingest_project_knowledge(
    project_id: str,
    path: str | Path,
    *,
    settings: Settings | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    store: MilvusKnowledgeStore | None = None,
) -> IngestionSummary:
    """Rebuild one project's Milvus index from reviewed Git Markdown."""

    source = Path(os.path.abspath(Path(path).expanduser()))
    if source.is_symlink():
        raise ValueError(f"symlinks are not allowed in knowledge imports: {source}")
    if not source.exists():
        raise ValueError(f"knowledge path does not exist: {source}")
    if source.is_file() and source.suffix != ".md":
        raise ValueError(f"knowledge import only accepts .md files, not {source.name}")
    project_file = _find_project_file(source)
    project_root = project_file.parent
    _ensure_inside_without_symlinks(project_file, project_root)
    _ensure_inside_without_symlinks(source, project_root)

    with _project_ingestion_lock(project_id, project_file):
        project = _load_project(project_file, project_id)
        documents = _discover_documents(source, project_root)
        if not documents:
            raise ValueError(f"knowledge path contains no Markdown documents: {source}")

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
