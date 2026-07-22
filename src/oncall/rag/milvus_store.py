"""Milvus collection lifecycle and project-scoped hybrid search."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Any
from uuid import uuid4

from pymilvus import AnnSearchRequest, DataType, Function, FunctionType, MilvusClient, RRFRanker

from oncall.rag.embeddings import EmbeddingDimensionError
from oncall.rag.schemas import KnowledgeChunk, KnowledgeCitation, KnowledgeQuery


_MANIFEST_SUFFIX = "__manifest"


class MilvusSchemaContractError(RuntimeError):
    """An existing collection cannot safely serve this implementation."""


class ManifestPublishUncertain(RuntimeError):
    """A pointer write may have succeeded but cannot currently be read back."""


class ManifestPublishSuperseded(RuntimeError):
    """Another host won the active-generation pointer after this build."""


class EmbeddingModelMismatchError(RuntimeError):
    """The active snapshot was built by a different embedding model."""


@dataclass(frozen=True)
class ActiveSnapshot:
    project_id: str
    active_generation: str
    embedding_model: str
    embedding_dimension: int


class MilvusKnowledgeStore:
    """Generation-based Dense + BM25 index published through a manifest pointer."""

    _OUTPUT_FIELDS = [
        "document_id",
        "title",
        "version",
        "section_path",
        "source_path",
        "content",
    ]
    _WRITE_BATCH_SIZE = 100
    _MAIN_FIELD_TYPES = {
        "chunk_id": DataType.VARCHAR,
        "project_id": DataType.VARCHAR,
        "document_id": DataType.VARCHAR,
        "document_type": DataType.VARCHAR,
        "service": DataType.VARCHAR,
        "environment": DataType.VARCHAR,
        "title": DataType.VARCHAR,
        "section_path": DataType.VARCHAR,
        "content": DataType.VARCHAR,
        "source_path": DataType.VARCHAR,
        "version": DataType.VARCHAR,
        "review_status": DataType.VARCHAR,
        "import_generation": DataType.VARCHAR,
        "embedding_model": DataType.VARCHAR,
        "dense_vector": DataType.FLOAT_VECTOR,
        "sparse_vector": DataType.SPARSE_FLOAT_VECTOR,
    }
    _MANIFEST_FIELD_TYPES = {
        "project_id": DataType.VARCHAR,
        "active_generation": DataType.VARCHAR,
        "embedding_model": DataType.VARCHAR,
        "embedding_dimension": DataType.INT64,
        "marker_vector": DataType.FLOAT_VECTOR,
    }
    _MAIN_MIN_LENGTHS = {
        "chunk_id": 64,
        "project_id": 128,
        "document_id": 256,
        "document_type": 64,
        "service": 128,
        "environment": 64,
        "title": 512,
        "section_path": 2048,
        "content": 65535,
        "source_path": 2048,
        "version": 64,
        "review_status": 16,
        "import_generation": 36,
        "embedding_model": 256,
    }
    _MANIFEST_MIN_LENGTHS = {
        "project_id": 128,
        "active_generation": 36,
        "embedding_model": 256,
    }

    def __init__(
        self,
        *,
        uri: str,
        collection_name: str,
        dimension: int,
        embedding_model: str,
    ) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        if not embedding_model or len(embedding_model) > 256:
            raise ValueError("embedding_model must contain 1 to 256 characters")
        if len(collection_name + _MANIFEST_SUFFIX) > 255:
            raise ValueError("collection_name is too long for its __manifest companion")
        self.collection_name = collection_name
        self.manifest_collection_name = collection_name + _MANIFEST_SUFFIX
        self.dimension = dimension
        self.embedding_model = embedding_model
        self.client = MilvusClient(uri=uri)

    def ensure_collection(self) -> None:
        """Create or strictly validate both the data and manifest collections."""

        if self.client.has_collection(collection_name=self.collection_name):
            self._validate_main_collection()
        else:
            try:
                self._create_main_collection()
            except Exception:
                # Another host may win first-collection creation. Only accept its
                # result after applying the exact same contract validation below.
                if not self.client.has_collection(collection_name=self.collection_name):
                    raise
            self._validate_main_collection()
        self.client.load_collection(collection_name=self.collection_name)

        if self.client.has_collection(collection_name=self.manifest_collection_name):
            self._validate_manifest_collection()
        else:
            try:
                self._create_manifest_collection()
            except Exception:
                if not self.client.has_collection(collection_name=self.manifest_collection_name):
                    raise
            self._validate_manifest_collection()
        self.client.load_collection(collection_name=self.manifest_collection_name)

    def _create_main_collection(self) -> None:
        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("project_id", DataType.VARCHAR, max_length=128)
        schema.add_field("document_id", DataType.VARCHAR, max_length=256)
        schema.add_field("document_type", DataType.VARCHAR, max_length=64)
        schema.add_field("service", DataType.VARCHAR, max_length=128)
        schema.add_field("environment", DataType.VARCHAR, max_length=64)
        schema.add_field("title", DataType.VARCHAR, max_length=512)
        schema.add_field("section_path", DataType.VARCHAR, max_length=2048)
        schema.add_field(
            "content",
            DataType.VARCHAR,
            max_length=65535,
            enable_analyzer=True,
            analyzer_params={"tokenizer": "standard", "filter": ["lowercase"]},
        )
        schema.add_field("source_path", DataType.VARCHAR, max_length=2048)
        schema.add_field("version", DataType.VARCHAR, max_length=64)
        schema.add_field("review_status", DataType.VARCHAR, max_length=16)
        schema.add_field("import_generation", DataType.VARCHAR, max_length=36)
        schema.add_field("embedding_model", DataType.VARCHAR, max_length=256)
        schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=self.dimension)
        schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_function(
            Function(
                name="content_bm25",
                function_type=FunctionType.BM25,
                input_field_names=["content"],
                output_field_names=["sparse_vector"],
            )
        )
        indexes = self.client.prepare_index_params()
        indexes.add_index(
            field_name="dense_vector",
            index_name="dense_vector_idx",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )
        indexes.add_index(
            field_name="sparse_vector",
            index_name="sparse_vector_idx",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
        )
        self.client.create_collection(
            collection_name=self.collection_name,
            schema=schema,
            index_params=indexes,
            consistency_level="Strong",
        )

    def _create_manifest_collection(self) -> None:
        # Milvus collections require a vector field, so this companion collection
        # carries a fixed marker vector while all meaningful data remains scalar.
        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("project_id", DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field("active_generation", DataType.VARCHAR, max_length=36)
        schema.add_field("embedding_model", DataType.VARCHAR, max_length=256)
        schema.add_field("embedding_dimension", DataType.INT64)
        schema.add_field("marker_vector", DataType.FLOAT_VECTOR, dim=2)
        indexes = self.client.prepare_index_params()
        indexes.add_index(
            field_name="marker_vector",
            index_name="marker_vector_idx",
            index_type="AUTOINDEX",
            metric_type="L2",
        )
        self.client.create_collection(
            collection_name=self.manifest_collection_name,
            schema=schema,
            index_params=indexes,
            consistency_level="Strong",
        )

    def _validate_main_collection(self) -> None:
        description = self.client.describe_collection(collection_name=self.collection_name)
        self._validate_fields(
            self.collection_name,
            description,
            self._MAIN_FIELD_TYPES,
            primary_field="chunk_id",
            vector_dimensions={"dense_vector": self.dimension},
            minimum_lengths=self._MAIN_MIN_LENGTHS,
        )
        functions = description.get("functions") or []
        has_bm25 = any("sparse_vector" in (item.get("output_field_names") or []) for item in functions)
        if not has_bm25:
            self._schema_error(self.collection_name, "missing BM25 function producing sparse_vector")

    def _validate_manifest_collection(self) -> None:
        description = self.client.describe_collection(collection_name=self.manifest_collection_name)
        self._validate_fields(
            self.manifest_collection_name,
            description,
            self._MANIFEST_FIELD_TYPES,
            primary_field="project_id",
            vector_dimensions={"marker_vector": 2},
            minimum_lengths=self._MANIFEST_MIN_LENGTHS,
        )

    def _validate_fields(
        self,
        collection_name: str,
        description: dict[str, Any],
        expected_types: dict[str, DataType],
        *,
        primary_field: str,
        vector_dimensions: dict[str, int],
        minimum_lengths: dict[str, int],
    ) -> None:
        if description.get("auto_id") or description.get("enable_dynamic_field"):
            self._schema_error(collection_name, "auto_id and dynamic fields must both be disabled")
        if description.get("consistency_level_name") != "Strong":
            self._schema_error(collection_name, "consistency level must be Strong")
        actual = {field.get("name"): field for field in description.get("fields", [])}
        missing = sorted(set(expected_types) - set(actual))
        if missing:
            self._schema_error(collection_name, f"missing fields {missing}")
        for name, expected_type in expected_types.items():
            field = actual[name]
            try:
                type_matches = int(field.get("type")) == int(expected_type)
            except (TypeError, ValueError):
                type_matches = False
            if not type_matches:
                self._schema_error(
                    collection_name,
                    f"field {name!r} has type {field.get('type')!r}, expected {expected_type.name}",
                )
        if not actual[primary_field].get("is_primary"):
            self._schema_error(collection_name, f"field {primary_field!r} is not the primary key")
        for name, minimum_length in minimum_lengths.items():
            raw_length = (actual[name].get("params") or {}).get("max_length")
            if raw_length is None or int(raw_length) < minimum_length:
                self._schema_error(
                    collection_name,
                    f"field {name!r} max_length is {raw_length}, expected at least {minimum_length}",
                )
        for name, expected_dimension in vector_dimensions.items():
            params = actual[name].get("params") or {}
            raw_dimension = params.get("dim", actual[name].get("dim"))
            if raw_dimension is None or int(raw_dimension) != expected_dimension:
                raise EmbeddingDimensionError(
                    f"Milvus collection {collection_name!r} field {name!r} uses dimension "
                    f"{raw_dimension}; expected {expected_dimension}. Configure a new collection "
                    "name or explicitly rebuild the old index."
                )

    @staticmethod
    def _schema_error(collection_name: str, detail: str) -> None:
        raise MilvusSchemaContractError(
            f"Milvus collection {collection_name!r} is incompatible: {detail}. "
            "Configure a new MILVUS_KNOWLEDGE_COLLECTION name or explicitly rebuild/drop the old index; "
            "the service will not silently mutate an existing schema."
        )

    def _read_active_snapshot(self, project_id: str) -> ActiveSnapshot | None:
        rows = self.client.query(
            collection_name=self.manifest_collection_name,
            ids=[project_id],
            output_fields=["project_id", "active_generation", "embedding_model", "embedding_dimension"],
        )
        if not rows:
            return None
        row = rows[0]
        return ActiveSnapshot(
            project_id=str(row["project_id"]),
            active_generation=str(row["active_generation"]),
            embedding_model=str(row["embedding_model"]),
            embedding_dimension=int(row["embedding_dimension"]),
        )

    def preflight_search(self, project_id: str) -> ActiveSnapshot | None:
        """Resolve and validate the active pointer before paid query embedding."""

        active = self._read_active_snapshot(project_id)
        if active is None:
            return None
        if active.embedding_dimension != self.dimension:
            raise EmbeddingDimensionError(
                f"active project snapshot uses dimension {active.embedding_dimension}; configured "
                f"dimension is {self.dimension}"
            )
        if active.embedding_model != self.embedding_model:
            raise EmbeddingModelMismatchError(
                f"active project snapshot uses embedding model {active.embedding_model!r}; configured "
                f"model is {self.embedding_model!r}"
            )
        return active

    def _generation_chunk_ids(self, project_id: str, generation: str) -> set[str]:
        iterator = self.client.query_iterator(
            collection_name=self.collection_name,
            batch_size=1000,
            filter=(
                f"project_id == {_literal(project_id)} and "
                f"import_generation == {_literal(generation)}"
            ),
            output_fields=["chunk_id"],
        )
        chunk_ids: set[str] = set()
        try:
            while batch := iterator.next():
                chunk_ids.update(str(row["chunk_id"]) for row in batch)
        finally:
            iterator.close()
        return chunk_ids

    def _delete_generation(self, project_id: str, generation: str) -> None:
        self.client.delete(
            collection_name=self.collection_name,
            filter=(
                f"project_id == {_literal(project_id)} and "
                f"import_generation == {_literal(generation)}"
            ),
        )
        self.client.flush(collection_name=self.collection_name)

    def _publish_snapshot(self, project_id: str, generation: str) -> None:
        publish_error: Exception | None = None
        try:
            result = self.client.upsert(
                collection_name=self.manifest_collection_name,
                data=[
                    {
                        "project_id": project_id,
                        "active_generation": generation,
                        "embedding_model": self.embedding_model,
                        "embedding_dimension": self.dimension,
                        "marker_vector": [0.0, 0.0],
                    }
                ],
            )
            if "upsert_count" not in result or int(result["upsert_count"]) != 1:
                raise RuntimeError(f"Milvus manifest upsert did not confirm one row: {result}")
            self.client.flush(collection_name=self.manifest_collection_name)
        except Exception as error:
            publish_error = error

        try:
            active = self._read_active_snapshot(project_id)
        except Exception as readback_error:
            uncertain = ManifestPublishUncertain(
                f"cannot confirm active knowledge generation {generation!r} for project {project_id!r}"
            )
            if publish_error is not None:
                uncertain.add_note(f"manifest write response: {publish_error}")
            uncertain.add_note(f"manifest readback failed: {readback_error}")
            raise uncertain from readback_error

        if active is not None and active.active_generation == generation:
            if active.embedding_model != self.embedding_model or active.embedding_dimension != self.dimension:
                raise RuntimeError("manifest readback generation matched but embedding metadata did not")
            return
        if publish_error is not None:
            raise publish_error
        raise ManifestPublishSuperseded(
            f"knowledge generation {generation!r} was superseded by another publisher"
        )

    def replace_project(
        self,
        chunks: Iterable[KnowledgeChunk],
        dense_vectors: Iterable[Sequence[float]],
    ) -> int:
        """Build an invisible generation, verify it, then publish one pointer row."""

        chunk_list = list(chunks)
        vector_list = list(dense_vectors)
        if not chunk_list:
            raise ValueError("at least one knowledge chunk is required")
        if len(chunk_list) != len(vector_list):
            raise ValueError("every chunk must have exactly one dense vector")
        project_ids = {chunk.project_id for chunk in chunk_list}
        if len(project_ids) != 1:
            raise ValueError("one import may only replace one project")
        source_ids = {chunk.chunk_id for chunk in chunk_list}
        if len(source_ids) != len(chunk_list):
            raise ValueError("knowledge import produced duplicate source chunk IDs")
        for vector in vector_list:
            if len(vector) != self.dimension:
                raise EmbeddingDimensionError(
                    f"vector dimension {len(vector)} does not match collection dimension {self.dimension}"
                )

        project_id = next(iter(project_ids))
        generation = str(uuid4())
        rows: list[dict[str, Any]] = []
        for chunk, vector in zip(chunk_list, vector_list, strict=True):
            row = chunk.model_dump(mode="json")
            row["chunk_id"] = hashlib.sha256(
                f"{generation}\0{chunk.chunk_id}".encode("utf-8")
            ).hexdigest()
            row["import_generation"] = generation
            row["embedding_model"] = self.embedding_model
            row["dense_vector"] = list(vector)
            rows.append(row)
        expected_ids = {row["chunk_id"] for row in rows}

        try:
            upserted = 0
            for batch in _batches(rows, self._WRITE_BATCH_SIZE):
                result = self.client.upsert(collection_name=self.collection_name, data=batch)
                if "upsert_count" not in result:
                    raise RuntimeError(f"Milvus upsert response omitted upsert_count: {result}")
                count = int(result["upsert_count"])
                if count != len(batch):
                    raise RuntimeError(f"Milvus reported {count} upserts for a batch of {len(batch)}")
                upserted += count
            if upserted != len(rows):
                raise RuntimeError(f"Milvus reported {upserted} upserts; expected {len(rows)}")
            self.client.flush(collection_name=self.collection_name)
            actual_ids = self._generation_chunk_ids(project_id, generation)
            if actual_ids != expected_ids:
                raise RuntimeError(
                    f"Milvus generation verification found {len(actual_ids)} IDs; expected {len(expected_ids)}"
                )
        except Exception as error:
            try:
                self._delete_generation(project_id, generation)
            except Exception as cleanup_error:  # pragma: no cover - best-effort recovery note
                error.add_note(f"failed to clean incomplete generation {generation}: {cleanup_error}")
            raise

        # Once publication has been attempted, this generation may have been
        # active briefly even if another host wins the readback. On error it is
        # deliberately retained for a future grace-period GC, never deleted here.
        self._publish_snapshot(project_id, generation)

        # Old complete generations intentionally remain invisible but queryable
        # by readers that already captured their pointer. A future GC must apply a
        # grace period; synchronous deletion here would violate reader snapshots.
        return upserted

    def hybrid_search(self, query: KnowledgeQuery, dense_vector: Sequence[float]) -> list[KnowledgeCitation]:
        """Search only the project's active, model-compatible generation."""

        active = self.preflight_search(query.project_id)
        if active is None:
            return []
        if len(dense_vector) != self.dimension:
            raise EmbeddingDimensionError(
                f"query dimension {len(dense_vector)} does not match collection dimension {self.dimension}"
            )
        scalar_filter = _query_filter(
            query,
            generation=active.active_generation,
            embedding_model=active.embedding_model,
        )
        candidate_limit = min(max(query.limit * 4, 20), 100)
        requests = [
            AnnSearchRequest(
                data=[list(dense_vector)],
                anns_field="dense_vector",
                param={"metric_type": "COSINE", "params": {}},
                limit=candidate_limit,
                filter=scalar_filter,
            ),
            AnnSearchRequest(
                data=[query.text],
                anns_field="sparse_vector",
                param={"metric_type": "BM25", "params": {}},
                limit=candidate_limit,
                filter=scalar_filter,
            ),
        ]
        batches = self.client.hybrid_search(
            collection_name=self.collection_name,
            reqs=requests,
            ranker=RRFRanker(),
            limit=query.limit,
            output_fields=self._OUTPUT_FIELDS,
        )
        hits = batches[0] if batches else []
        citations: list[KnowledgeCitation] = []
        for hit in hits:
            entity = hit.get("entity", hit)
            citations.append(
                KnowledgeCitation(
                    document_id=entity["document_id"],
                    title=entity["title"],
                    version=entity["version"],
                    section_path=entity["section_path"],
                    source_path=entity["source_path"],
                    content=entity["content"],
                    score=float(hit.get("distance", hit.get("score", 0.0))),
                )
            )
        return citations


def _literal(value: str) -> str:
    """Encode a user value as one Milvus string literal, not expression syntax."""

    return json.dumps(value, ensure_ascii=False)


def _query_filter(query: KnowledgeQuery, *, generation: str, embedding_model: str) -> str:
    clauses = [
        f"project_id == {_literal(query.project_id)}",
        f"import_generation == {_literal(generation)}",
        f"embedding_model == {_literal(embedding_model)}",
        'review_status == "approved"',
    ]
    if query.service is not None:
        clauses.append(f"service == {_literal(query.service)}")
    if query.environment is not None:
        clauses.append(f"environment == {_literal(query.environment)}")
    if query.document_type is not None:
        clauses.append(f"document_type == {_literal(query.document_type)}")
    if query.version is not None:
        clauses.append(f"version == {_literal(query.version)}")
    return " and ".join(clauses)


def _batches(items: Sequence[Any], batch_size: int) -> Iterator[list[Any]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    for start in range(0, len(items), batch_size):
        yield list(items[start : start + batch_size])
