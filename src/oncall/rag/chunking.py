"""Markdown-aware chunking without making the source index authoritative."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Iterable, Mapping

from oncall.rag.schemas import KnowledgeChunk, ProjectKnowledgeDefaults


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TOKEN = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+(?:[./:@+-][A-Za-z0-9_]+)*|[^\s]", re.UNICODE)
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)


@dataclass(frozen=True)
class _TokenSpan:
    start: int
    end: int


@dataclass(frozen=True)
class _Section:
    path: tuple[str, ...]
    body: str


def split_front_matter(markdown: str) -> tuple[str | None, str]:
    """Return raw YAML front matter and the Markdown body."""

    match = _FRONT_MATTER.match(markdown)
    if match is None:
        return None, markdown
    return match.group(1), markdown[match.end() :]


def _sections(markdown: str, fallback_title: str) -> list[_Section]:
    headings: list[str] = []
    current_path = (fallback_title,)
    current_lines: list[str] = []
    sections: list[_Section] = []

    def flush() -> None:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append(_Section(current_path, body))

    for line in markdown.splitlines():
        heading = _HEADING.match(line)
        if heading is None:
            current_lines.append(line)
            continue
        flush()
        level = len(heading.group(1))
        text = heading.group(2).strip()
        headings[level - 1 :] = [text]
        current_path = tuple(headings)
        current_lines = [line]
    flush()
    return sections


def _token_spans(text: str) -> list[_TokenSpan]:
    # This deterministic approximation treats Chinese characters, technical words,
    # and punctuation as units. The configured sizes are intentionally approximate.
    return [_TokenSpan(match.start(), match.end()) for match in _TOKEN.finditer(text)]


def _split_large_section(text: str, max_tokens: int, overlap_tokens: int) -> Iterable[str]:
    spans = _token_spans(text)
    if len(spans) <= max_tokens:
        yield text.strip()
        return
    step = max_tokens - overlap_tokens
    for token_start in range(0, len(spans), step):
        token_end = min(token_start + max_tokens, len(spans))
        char_start = 0 if token_start == 0 else spans[token_start].start
        char_end = len(text) if token_end == len(spans) else spans[token_end - 1].end
        chunk = text[char_start:char_end].strip()
        if chunk:
            yield chunk
        if token_end == len(spans):
            break


def chunk_markdown(
    *,
    project_id: str,
    source_path: str,
    markdown: str,
    metadata: Mapping[str, str],
    defaults: ProjectKnowledgeDefaults,
    max_tokens: int = 1000,
    overlap_tokens: int = 100,
) -> list[KnowledgeChunk]:
    """Split one Markdown file by heading, then by approximate token windows."""

    if max_tokens <= 0 or overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("chunk sizes require 0 <= overlap_tokens < max_tokens")
    fallback_title = metadata.get("title") or Path(source_path).stem.replace("-", " ")
    title = metadata.get("title") or next(
        (match.group(2).strip() for line in markdown.splitlines() if (match := _HEADING.match(line))),
        fallback_title,
    )
    document_id = metadata.get("document_id") or f"{project_id}:{Path(source_path).with_suffix('').as_posix()}"
    values = {
        "document_type": metadata.get("document_type", defaults.document_type),
        "service": metadata.get("service", defaults.service),
        "environment": metadata.get("environment", defaults.environment),
        "version": str(metadata.get("version", defaults.version)),
        "review_status": metadata.get("review_status", defaults.review_status),
    }
    chunks: list[KnowledgeChunk] = []
    for section in _sections(markdown, title):
        for part_number, content in enumerate(_split_large_section(section.body, max_tokens, overlap_tokens), 1):
            section_path = " > ".join(section.path)
            identity = "\0".join(
                (project_id, document_id, values["version"], source_path, section_path, str(part_number), content)
            )
            chunks.append(
                KnowledgeChunk(
                    chunk_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                    project_id=project_id,
                    document_id=document_id,
                    title=title,
                    section_path=section_path,
                    content=content,
                    source_path=source_path,
                    **values,
                )
            )
    return chunks
