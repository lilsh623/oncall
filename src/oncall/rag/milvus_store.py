"""Milvus collection lifecycle and project-scoped hybrid search."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from pymilvus import AnnSearchRequest, DataType, Function, FunctionType, MilvusClient, RRFRanker

from oncall.rag.embeddings import EmbeddingDimensionError
from oncall.rag.schemas import KnowledgeChunk, KnowledgeCitation, KnowledgeQuery


class MilvusKnowledgeStore:
    """Dense + BM25 index whose rows can always be rebuilt from Git."""

    _OUTPUT_FIELDS = [
        "document_id",
        "title",
        "version",
        "section_path",
        "source_path",
        "content",
    ]
    _WRITE_BATCH_SIZE = 100

    def __init__(self, *, uri: str, collection_name: str, dimension: int) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.collection_name = collection_name
        self.dimension = dimension
        self.client = MilvusClient(uri=uri)

    def ensure_collection(self) -> None:
        """Create the collection once or reject an unsafe dimension mismatch."""

        if self.client.has_collection(collection_name=self.collection_name):
            actual = self._collection_dimension()
            if actual != self.dimension:
                raise EmbeddingDimensionError(
                    f"Milvus collection {self.collection_name!r} uses dimension {actual}; "
                    f"configured embedding dimension is {self.dimension}. "
                    "Use a new collection name or rebuild the index explicitly."
                )
            self.client.load_collection(collection_name=self.collection_name)
            return

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
        self.client.load_collection(collection_name=self.collection_name)

    def _collection_dimension(self) -> int:
        description = self.client.describe_collection(collection_name=self.collection_name)
        for field in description.get("fields", []):
            if field.get("name") != "dense_vector":
                continue
            params = field.get("params") or {}
            raw_dimension = params.get("dim", field.get("dim"))
            if raw_dimension is not None:
                return int(raw_dimension)
        raise EmbeddingDimensionError(
            f"Milvus collection {self.collection_name!r} has no readable dense_vector dimension"
        )

    def _project_chunk_ids(self, project_id: str) -> set[str]:
        iterator = self.client.query_iterator(
            collection_name=self.collection_name,
            batch_size=1000,
            filter=f"project_id == {_literal(project_id)}",
            output_fields=["chunk_id"],
        )
        chunk_ids: set[str] = set()
        try:
            while batch := iterator.next():
                chunk_ids.update(str(row["chunk_id"]) for row in batch)
        finally:
            iterator.close()
        return chunk_ids

    def _delete_ids(self, chunk_ids: Iterable[str]) -> None:
        ids = list(chunk_ids)
        for batch in _batches(ids, self._WRITE_BATCH_SIZE):
            self.client.delete(collection_name=self.collection_name, ids=batch)

    def replace_project(
        self,
        chunks: Iterable[KnowledgeChunk],
        dense_vectors: Iterable[Sequence[float]],
    ) -> int:
        """Upsert a complete new snapshot before deleting stale project rows."""

        chunk_list = list(chunks)
        vector_list = list(dense_vectors)
        if not chunk_list:
            raise ValueError("at least one knowledge chunk is required")
        if len(chunk_list) != len(vector_list):
            raise ValueError("every chunk must have exactly one dense vector")
        project_ids = {chunk.project_id for chunk in chunk_list}
        if len(project_ids) != 1:
            raise ValueError("one import may only replace one project")
        for vector in vector_list:
            if len(vector) != self.dimension:
                raise EmbeddingDimensionError(
                    f"vector dimension {len(vector)} does not match collection dimension {self.dimension}"
                )

        project_id = next(iter(project_ids))
        rows: list[dict[str, Any]] = []
        for chunk, vector in zip(chunk_list, vector_list, strict=True):
            row = chunk.model_dump(mode="json")
            row["dense_vector"] = list(vector)
            rows.append(row)
        new_ids = {row["chunk_id"] for row in rows}
        if len(new_ids) != len(rows):
            raise ValueError("knowledge import produced duplicate chunk IDs")

        old_ids = self._project_chunk_ids(project_id)
        attempted_new_ids: set[str] = set()
        upserted = 0
        try:
            for batch in _batches(rows, self._WRITE_BATCH_SIZE):
                attempted_new_ids.update(row["chunk_id"] for row in batch)
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
        except Exception as error:
            # Deterministic IDs let us remove only rows introduced by this failed
            # attempt. Pre-existing rows remain available throughout the import.
            try:
                self._delete_ids(attempted_new_ids - old_ids)
                self.client.flush(collection_name=self.collection_name)
            except Exception as cleanup_error:  # pragma: no cover - best-effort recovery note
                error.add_note(f"failed to clean partial Milvus upserts: {cleanup_error}")
            raise

        self._delete_ids(old_ids - new_ids)
        self.client.flush(collection_name=self.collection_name)
        return upserted

    def hybrid_search(self, query: KnowledgeQuery, dense_vector: Sequence[float]) -> list[KnowledgeCitation]:
        """Fuse dense semantic and BM25 keyword rankings using RRF."""

        if len(dense_vector) != self.dimension:
            raise EmbeddingDimensionError(
                f"query dimension {len(dense_vector)} does not match collection dimension {self.dimension}"
            )
        scalar_filter = _query_filter(query)
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


def _query_filter(query: KnowledgeQuery) -> str:
    # These two clauses are mandatory tenant and governance boundaries.
    clauses = [f"project_id == {_literal(query.project_id)}", 'review_status == "approved"']
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
