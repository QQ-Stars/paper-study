"""Deterministic source chunking and context-plan construction.

The first public seam in this module is ``chunk_markdown``.  It accepts only
the already materialized, SHA-verified SourceDocument markdown; it never opens
PDFs, calls a provider, or writes a database.  The application/repository
adapter added later can persist the returned ``ChunkSet`` in one transaction.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Iterable

from backend.app.domain.context import (
    ChunkSet,
    ChunkingSpec,
    ContextBatch,
    ContextPlan,
    ContextRequest,
    chunk_key_for,
)
from backend.app.domain.errors import (
    ChunkAtomicBlockTooLargeError,
    ChunkCoverageInvalidError,
    ChunkingVersionMismatchError,
    ContextBudgetInvalidError,
    ContextCoverageInvalidError,
    SourceChunksNotReadyError,
    SourceNotReadyError,
    StaleSourceError,
)
from backend.app.domain.entities import ArtifactKind, DocumentChunk, SourceDocumentStatus


_WORD_OR_NUMBER = re.compile(r"[\w\d]+", re.UNICODE)
_ATX_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_PAGE_MARKER = re.compile(r"^\s*(?:<!--\s*)?\[\s*page\s+(\d+)\s*\](?:\s*-->)?\s*$", re.I)
_INLINE_MATH = re.compile(
    r"(?<!\\)\$(?!\$)(?:\\.|[^$\r\n])+?(?<!\\)\$|\\\((?:\\.|[^\r\n])+?\\\)"
)
_ESCAPED_MARKDOWN_DELIMITER = re.compile(r"\\[\\|$`*_{}\[\]()]" )
_SUMMARY_EXCLUDED_HEADINGS = frozenset(
    {
        "references",
        "bibliography",
        "acknowledgements",
        "acknowledgments",
        "参考文献",
        "致谢",
    }
)
_EXPLAIN_PRIORITY_HEADINGS = frozenset(
    {
        "abstract",
        "summary",
        "introduction",
        "background",
        "methods",
        "method",
        "approach",
        "experiments",
        "experiment",
        "evaluation",
        "results",
        "discussion",
        "conclusion",
        "摘要",
        "概要",
        "引言",
        "背景",
        "方法",
        "方法概述",
        "实验",
        "评估",
        "结果",
        "讨论",
        "结论",
    }
)
_HEADING_NUMBER_PREFIX = re.compile(r"^\s*(?:\d+(?:\.\d+)*[.)]?|[ivxlcdm]+[.)])\s+", re.I)
_CLASSIFICATION_HEADINGS = {
    "abstract": frozenset({"abstract", "summary", "摘要"}),
    "methods": frozenset(
        {
            "method",
            "methods",
            "method overview",
            "methods overview",
            "approach",
            "方法",
            "方法概述",
        }
    ),
    "conclusion": frozenset({"conclusion", "conclusions", "结论"}),
}
_CLASSIFICATION_CATEGORY_CAPS = {
    "front": 600,
    "abstract": 800,
    "methods": 1200,
    "conclusion": 800,
}
_EXPLAINER_MAP_BATCH_TOKEN_CAP = 1600


def unicode_word_token_count(value: str) -> int:
    """Count runs of Unicode letters/numbers plus standalone symbols."""

    if not isinstance(value, str):
        raise ValueError("content must be text")
    count = 0
    index = 0
    while index < len(value):
        match = _WORD_OR_NUMBER.match(value, index)
        if match:
            count += 1
            index = match.end()
            continue
        if not value[index].isspace():
            count += 1
        index += 1
    return count


def _plain_text_boundaries(content: str, token_cap: int) -> tuple[int, ...]:
    """Return gapless slice ends using sentence, then token, boundaries."""

    token_spans: list[tuple[int, int]] = []
    index = 0
    while index < len(content):
        match = _WORD_OR_NUMBER.match(content, index)
        if match:
            token_spans.append((index, match.end()))
            index = match.end()
            continue
        if not content[index].isspace():
            token_spans.append((index, index + 1))
        index += 1
    if len(token_spans) <= token_cap:
        return (len(content),)

    boundaries: list[int] = []
    token_index = 0
    while len(token_spans) - token_index > token_cap:
        cap_index = token_index + token_cap
        boundary_index = cap_index
        for candidate in range(cap_index - 1, token_index - 1, -1):
            start, end = token_spans[candidate]
            if content[start:end] in {".", "?", "!", "。", "？", "！"}:
                boundary_index = candidate + 1
                break
        boundary = token_spans[boundary_index - 1][1]
        boundaries.append(boundary)
        token_index = boundary_index
    boundaries.append(len(content))
    return tuple(boundaries)


def _heading_json(path: tuple[str, ...]) -> str | None:
    if not path:
        return None
    import json

    return json.dumps(path, ensure_ascii=False, separators=(",", ":"))


def _heading_path(chunk: DocumentChunk) -> tuple[str, ...]:
    if chunk.heading_path is None:
        return ()
    values = json.loads(chunk.heading_path)
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("CONTEXT_HEADING_PATH_INVALID: heading path must be a string array")
    return tuple(value.strip().casefold() for value in values)


def _explain_section_key(chunk: DocumentChunk) -> str | None:
    headings = _heading_path(chunk)
    if _SUMMARY_EXCLUDED_HEADINGS.intersection(headings):
        return None
    for heading in reversed(headings):
        normalized = _HEADING_NUMBER_PREFIX.sub("", heading)
        if normalized in _EXPLAIN_PRIORITY_HEADINGS:
            return heading
    return None


def _classification_category(chunk: DocumentChunk) -> str | None:
    for heading in reversed(_heading_path(chunk)):
        normalized = _HEADING_NUMBER_PREFIX.sub("", heading)
        for category, aliases in _CLASSIFICATION_HEADINGS.items():
            if normalized in aliases:
                return category
    return None


def _split_lines(markdown: str) -> list[tuple[int, int, str]]:
    lines: list[tuple[int, int, str]] = []
    start = 0
    for raw in markdown.splitlines(keepends=True):
        end = start + len(raw)
        lines.append((start, end, raw))
        start = end
    if start < len(markdown) or not lines:
        lines.append((start, len(markdown), markdown[start:]))
    return lines


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _is_math_start(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("$$") or stripped.startswith("\\[")


def _math_closes(line: str, opener: str, *, opening_line: bool = False) -> bool:
    stripped = line.strip()
    if opener == "$$":
        if opening_line:
            return stripped.count("$$") >= 2
        return stripped.endswith("$$")
    return stripped.endswith("\\]")


def _has_same_line_math_tail(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith("$$"):
        close_at = stripped.find("$$", 2)
        close_width = 2
    elif stripped.startswith("\\["):
        close_at = stripped.find("\\]", 2)
        close_width = 2
    else:
        return False
    return close_at >= 0 and bool(stripped[close_at + close_width :].strip())


def _paragraph_boundary(lines: list[tuple[int, int, str]], start_index: int, token_cap: int) -> int:
    """Choose the furthest complete line at or below the plain token cap."""

    total = 0
    boundary = start_index
    for index in range(start_index, len(lines)):
        line = lines[index][2]
        if index > start_index and (
            not line.strip()
            or _FENCE.match(line)
            or _is_table_line(line)
            or _is_math_start(line)
            or _ATX_HEADING.match(line.rstrip("\r\n"))
        ):
            break
        line_tokens = unicode_word_token_count(line)
        if boundary > start_index and total + line_tokens > token_cap:
            break
        total += line_tokens
        boundary = index + 1
        if total >= token_cap:
            break
    if boundary == start_index:
        boundary = min(start_index + 1, len(lines))
    # Prefer a blank-line semantic boundary when one is available.
    for index in range(boundary - 1, start_index, -1):
        if not lines[index - 1][2].strip():
            return index
    return boundary


def chunk_markdown(
    *,
    source_document_id: str,
    source_content_sha256: str,
    markdown: str,
    spec: ChunkingSpec | None = None,
    now: datetime | None = None,
) -> ChunkSet:
    """Materialize deterministic, gapless chunks from canonical markdown."""

    if not isinstance(markdown, str) or not markdown:
        raise ChunkCoverageInvalidError()
    spec = spec or ChunkingSpec()
    if hashlib.sha256(markdown.encode("utf-8")).hexdigest() != source_content_sha256:
        raise ChunkCoverageInvalidError()
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    lines = _split_lines(markdown)
    chunks: list[DocumentChunk] = []
    heading_stack: list[str] = []
    page_start: int | None = None
    index = 0
    while index < len(lines):
        content_prefix_index = index
        while index < len(lines) and not lines[index][2].strip():
            index += 1
        if index >= len(lines):
            # Every trailing separator is normally absorbed by the preceding
            # chunk.  A source containing only whitespace is invalid upstream.
            raise ChunkCoverageInvalidError()
        start, end, line = lines[index]
        heading = _ATX_HEADING.match(line.rstrip("\r\n"))
        if heading:
            level = len(heading.group(1))
            heading_stack[:] = heading_stack[: level - 1]
            heading_stack.append(heading.group(2).strip())
        page_marker = _PAGE_MARKER.match(line.rstrip("\r\n"))
        if page_marker:
            page_start = int(page_marker.group(1))

        kind = "text"
        end_index = index + 1
        fence = _FENCE.match(line)
        if fence:
            kind = "verbatim"
            marker = fence.group(1)[0]
            marker_len = len(fence.group(1))
            found_close = False
            for candidate in range(index + 1, len(lines)):
                candidate_text = lines[candidate][2].lstrip()
                if candidate_text.startswith(marker * marker_len):
                    end_index = candidate + 1
                    found_close = True
                    break
            if not found_close:
                end_index = len(lines)
        elif _is_math_start(line) and _has_same_line_math_tail(line):
            # A display-math span followed by prose on the same physical line
            # is structured input: the formula must be protected while the
            # trailing prose remains eligible for translation.
            kind = "structured"
        elif _is_math_start(line):
            kind = "verbatim"
            opener = "$$" if line.strip().startswith("$$") else "\\["
            found_close = _math_closes(line, opener, opening_line=True)
            if not found_close:
                for candidate in range(index + 1, len(lines)):
                    if _math_closes(lines[candidate][2], opener):
                        end_index = candidate + 1
                        found_close = True
                        break
            if not found_close:
                end_index = len(lines)
        elif _is_table_line(line):
            kind = "structured"
            while end_index < len(lines) and _is_table_line(lines[end_index][2]):
                end_index += 1
        else:
            # Group a paragraph/heading run, then split only at semantic line
            # boundaries if it exceeds the ordinary token target.
            end_index = index + 1 if not line.strip() else _paragraph_boundary(lines, index, spec.target_tokens)

        # Keep semantic separators with an adjacent content-bearing chunk so
        # every translation checkpoint can satisfy the non-empty payload
        # contract without dropping a byte of source coverage.
        while end_index < len(lines) and not lines[end_index][2].strip():
            end_index += 1

        content_start = lines[content_prefix_index][0]
        content_end = lines[end_index - 1][1]
        content = markdown[content_start:content_end]
        if kind == "text" and (
            _INLINE_MATH.search(content)
            or _ESCAPED_MARKDOWN_DELIMITER.search(content)
        ):
            kind = "structured"
        token_count = unicode_word_token_count(content)
        if kind in {"verbatim", "structured"} and token_count > spec.atomic_hard_cap_tokens:
            raise ChunkAtomicBlockTooLargeError()
        relative_ends = (
            _plain_text_boundaries(content, spec.target_tokens)
            if kind == "text"
            else (len(content),)
        )
        relative_start = 0
        for relative_end in relative_ends:
            chunk_content = content[relative_start:relative_end]
            chunk_start = content_start + relative_start
            chunk_end = content_start + relative_end
            chunk_token_count = unicode_word_token_count(chunk_content)
            if kind == "text" and chunk_token_count > spec.hard_cap_tokens:
                raise ChunkCoverageInvalidError()
            content_sha = hashlib.sha256(chunk_content.encode("utf-8")).hexdigest()
            chunk_key = chunk_key_for(
                source_document_id=source_document_id,
                source_content_sha256=source_content_sha256,
                chunking_version=spec.chunking_version,
                sequence=len(chunks),
                char_start=chunk_start,
                char_end=chunk_end,
                content_sha256=content_sha,
            )
            chunks.append(
                DocumentChunk(
                    id="chunk_" + chunk_key[:32],
                    source_document_id=source_document_id,
                    sequence=len(chunks),
                    heading_path=_heading_json(tuple(heading_stack)),
                    page_start=page_start,
                    page_end=page_start,
                    content=chunk_content,
                    content_sha256=content_sha,
                    token_count=chunk_token_count,
                    status="ready",
                    content_kind=kind,
                    chunk_key=chunk_key,
                    chunking_version=spec.chunking_version,
                    source_content_sha256=source_content_sha256,
                    char_start=chunk_start,
                    char_end=chunk_end,
                    created_at=timestamp,
                    updated_at=timestamp,
                    stale_at=None,
                )
            )
            relative_start = relative_end
        index = end_index
    result = ChunkSet(
        source_document_id=source_document_id,
        source_content_sha256=source_content_sha256,
        chunks=tuple(chunks),
        source_markdown=markdown,
        spec=spec,
    )
    if result.covered_content_sha256 != source_content_sha256:
        raise ChunkCoverageInvalidError()
    return result


class ContextBuilder:
    """Application seam for explicit chunk materialization and read-only plans."""

    def __init__(self, unit_of_work_factory) -> None:
        self._work_factory = unit_of_work_factory

    async def materialize_chunks(
        self,
        source_document_id: str,
        spec: ChunkingSpec,
        *,
        now: datetime,
    ) -> ChunkSet:
        async with self._work_factory() as work:
            source = await work.sources.get(source_document_id)
        if (
            source is None
            or source.status is not SourceDocumentStatus.READY
            or source.markdown is None
            or source.content_sha256 is None
        ):
            raise SourceNotReadyError()
        generated = chunk_markdown(
            source_document_id=source.id,
            source_content_sha256=source.content_sha256,
            markdown=source.markdown,
            spec=spec,
            now=now,
        )
        async with self._work_factory() as work:
            current = await work.sources.get(source_document_id)
            if (
                current is None
                or current.status is not SourceDocumentStatus.READY
                or current.content_sha256 != source.content_sha256
                or current.markdown != source.markdown
            ):
                raise StaleSourceError()
            await work.chunks.insert_set(generated)
            await work.chunks.stale_other_versions(
                source_document_id,
                spec.chunking_version,
                now=now,
            )
            await work.commit()
        return generated

    async def build(
        self,
        source_document_id: str,
        request: ContextRequest,
    ) -> ContextPlan:
        """Build an auditable context plan without materializing or mutating rows."""

        if not isinstance(request, ContextRequest):
            raise ValueError("CONTEXT_REQUEST_INVALID: request must be ContextRequest")
        if request.source_document_id != source_document_id:
            raise ValueError("CONTEXT_REQUEST_INVALID: source identity mismatch")
        async with self._work_factory() as work:
            source = await work.sources.get(source_document_id)
            chunks = await work.chunks.list_for_source(
                source_document_id,
                status="ready",
            )
        if (
            source is None
            or source.status is not SourceDocumentStatus.READY
            or source.markdown is None
            or source.content_sha256 is None
        ):
            raise SourceNotReadyError()
        if not chunks:
            raise SourceChunksNotReadyError()
        if {
            chunk.chunking_version for chunk in chunks
        } != {request.chunking_version}:
            raise ChunkingVersionMismatchError()
        if any(
            chunk.source_content_sha256 != source.content_sha256
            for chunk in chunks
        ):
            raise StaleSourceError()
        try:
            chunk_set = ChunkSet(
                source_document_id=source.id,
                source_content_sha256=source.content_sha256,
                chunks=tuple(chunks),
                source_markdown=source.markdown,
                spec=ChunkingSpec(chunking_version=request.chunking_version),
            )
        except ValueError as error:
            raise ContextCoverageInvalidError() from error
        batch_level = "leaf"
        batch_group_sequences: tuple[int | None, ...] | None = None
        eligible_chunks: tuple[DocumentChunk, ...]
        excluded_reason_by_id: dict[str, str] = {}
        if request.consumer is ArtifactKind.TRANSLATION:
            batch_chunks = tuple((chunk,) for chunk in chunk_set.chunks)
            eligible_chunks = chunk_set.chunks
        elif request.consumer == "embedding":
            if request.budget_tokens is None:
                raise ContextBudgetInvalidError()
            grouped_chunks: list[tuple[DocumentChunk, ...]] = []
            current_chunks: list[DocumentChunk] = []
            current_tokens = 0
            for chunk in chunk_set.chunks:
                if chunk.token_count > request.budget_tokens:
                    raise ContextBudgetInvalidError()
                if current_chunks and current_tokens + chunk.token_count > request.budget_tokens:
                    grouped_chunks.append(tuple(current_chunks))
                    current_chunks = []
                    current_tokens = 0
                current_chunks.append(chunk)
                current_tokens += chunk.token_count
            if current_chunks:
                grouped_chunks.append(tuple(current_chunks))
            batch_chunks = tuple(grouped_chunks)
            eligible_chunks = chunk_set.chunks
        elif request.consumer is ArtifactKind.SUMMARY:
            eligible_chunks = tuple(
                chunk
                for chunk in chunk_set.chunks
                if not _SUMMARY_EXCLUDED_HEADINGS.intersection(_heading_path(chunk))
            )
            if not eligible_chunks:
                raise ContextCoverageInvalidError()
            batch_chunks = tuple((chunk,) for chunk in eligible_chunks)
            batch_level = "map"
        elif request.consumer is ArtifactKind.EXPLAINER:
            section_groups: list[tuple[DocumentChunk, ...]] = []
            current_section: list[DocumentChunk] = []
            current_key: str | None = None
            for chunk in chunk_set.chunks:
                section_key = _explain_section_key(chunk)
                if section_key is None:
                    if current_section:
                        section_groups.append(tuple(current_section))
                        current_section = []
                        current_key = None
                    continue
                if current_section and section_key != current_key:
                    section_groups.append(tuple(current_section))
                    current_section = []
                current_section.append(chunk)
                current_key = section_key
            if current_section:
                section_groups.append(tuple(current_section))
            if not section_groups:
                raise ContextCoverageInvalidError()
            eligible_chunks = tuple(chunk for group in section_groups for chunk in group)
            packed_sections: list[tuple[DocumentChunk, ...]] = []
            packed_group_sequences: list[int] = []
            for group_sequence, section in enumerate(section_groups):
                current_batch: list[DocumentChunk] = []
                current_tokens = 0
                for chunk in section:
                    if (
                        current_batch
                        and current_tokens + chunk.token_count
                        > _EXPLAINER_MAP_BATCH_TOKEN_CAP
                    ):
                        packed_sections.append(tuple(current_batch))
                        packed_group_sequences.append(group_sequence)
                        current_batch = []
                        current_tokens = 0
                    current_batch.append(chunk)
                    current_tokens += chunk.token_count
                if current_batch:
                    packed_sections.append(tuple(current_batch))
                    packed_group_sequences.append(group_sequence)
            batch_chunks = tuple(packed_sections)
            batch_group_sequences = tuple(packed_group_sequences)
            batch_level = "section"
        elif request.consumer is ArtifactKind.CLASSIFICATION:
            category_chunks: dict[str, list[DocumentChunk]] = {
                category: [] for category in _CLASSIFICATION_CATEGORY_CAPS
            }
            category_chunks["front"].append(chunk_set.chunks[0])
            for chunk in chunk_set.chunks:
                if chunk.id == chunk_set.chunks[0].id:
                    continue
                category = _classification_category(chunk)
                if category is not None:
                    category_chunks[category].append(chunk)
            eligible_ids = {
                chunk.id for candidates in category_chunks.values() for chunk in candidates
            }
            eligible_chunks = tuple(
                chunk for chunk in chunk_set.chunks if chunk.id in eligible_ids
            )
            selected_groups: list[tuple[DocumentChunk, ...]] = []
            selected_ids: set[str] = set()
            for category, cap in _CLASSIFICATION_CATEGORY_CAPS.items():
                selected: list[DocumentChunk] = []
                used_tokens = 0
                exhausted = False
                for chunk in category_chunks[category]:
                    if exhausted or used_tokens + chunk.token_count > cap:
                        excluded_reason_by_id[chunk.id] = (
                            f"classification {category} budget exceeded"
                        )
                        exhausted = True
                        continue
                    selected.append(chunk)
                    selected_ids.add(chunk.id)
                    used_tokens += chunk.token_count
                if selected:
                    selected_groups.append(tuple(selected))
            for chunk in chunk_set.chunks:
                if chunk.id not in selected_ids and chunk.id not in excluded_reason_by_id:
                    excluded_reason_by_id[chunk.id] = "not a classification category"
            if not selected_groups:
                raise ContextCoverageInvalidError()
            batch_chunks = tuple(selected_groups)
            batch_level = "category"
        elif request.consumer is ArtifactKind.METADATA:
            has_page_metadata = any(
                chunk.page_start is not None or chunk.page_end is not None
                for chunk in chunk_set.chunks
            )
            if has_page_metadata:
                eligible_chunks = tuple(
                    chunk for chunk in chunk_set.chunks if chunk.page_start == 1
                )
            else:
                eligible_chunks = (chunk_set.chunks[0],)
            if not eligible_chunks:
                raise ContextCoverageInvalidError()
            selected: list[DocumentChunk] = []
            used_tokens = 0
            budget_exhausted = False
            for chunk in eligible_chunks:
                if budget_exhausted or used_tokens + chunk.token_count > 1600:
                    excluded_reason_by_id[chunk.id] = "metadata budget exceeded"
                    budget_exhausted = True
                    continue
                selected.append(chunk)
                used_tokens += chunk.token_count
            if not selected:
                raise ContextBudgetInvalidError()
            selected_ids = {chunk.id for chunk in selected}
            for chunk in chunk_set.chunks:
                if chunk.id not in selected_ids and chunk.id not in excluded_reason_by_id:
                    excluded_reason_by_id[chunk.id] = "not first-page metadata"
            batch_chunks = (tuple(selected),)
            batch_level = "category"
        else:
            raise ValueError("CONTEXT_POLICY_UNSUPPORTED: consumer policy is not implemented")
        batches = tuple(
            ContextBatch(
                sequence=sequence,
                chunk_ids=tuple(chunk.id for chunk in chunks),
                chunks=chunks,
                token_count=sum(chunk.token_count for chunk in chunks),
                level=batch_level,
                covered_ranges=tuple(
                    (chunk.char_start, chunk.char_end)
                    for chunk in chunks
                    if chunk.char_start is not None and chunk.char_end is not None
                ),
                group_sequence=(
                    batch_group_sequences[sequence]
                    if batch_group_sequences is not None
                    else None
                ),
            )
            for sequence, chunks in enumerate(batch_chunks)
        )
        return ContextPlan(
            source_document_id=source.id,
            source_content_sha256=source.content_sha256,
            chunking_version=request.chunking_version,
            all_chunk_ids=chunk_set.chunk_ids,
            eligible_chunk_ids=tuple(chunk.id for chunk in eligible_chunks),
            selected_chunk_ids=tuple(chunk.id for chunks in batch_chunks for chunk in chunks),
            batches=batches,
            consumer=request.consumer,
            excluded_reasons=tuple(
                (chunk.id, excluded_reason_by_id.get(chunk.id, "excluded heading"))
                for chunk in chunk_set.chunks
                if chunk.id not in {item.id for chunks in batch_chunks for item in chunks}
            ),
        )


__all__ = ["ContextBuilder", "chunk_markdown", "unicode_word_token_count"]
