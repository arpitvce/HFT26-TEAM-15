"""Deterministic, line-preserving source chunk construction for retrieval."""

from __future__ import annotations

from typing import Any


DEFAULT_CHUNK_LINES = 80
MAX_CHUNK_LINES = 120


def build_search_documents(
    source: str,
    symbols: list[dict[str, Any]],
    file_path: str,
    language: str,
    content_hash: str,
    max_lines: int = DEFAULT_CHUNK_LINES,
) -> list[dict[str, Any]]:
    """Partition source into deterministic, non-overlapping chunks with exact line ranges.

    Chunk text is sliced directly from the decoded source lines. A chunk boundary may
    move to a nearby indexed symbol end, but never beyond the configured hard limit.
    This preserves every source line exactly once and avoids inferred line offsets.
    """
    if not 1 <= max_lines <= MAX_CHUNK_LINES:
        raise ValueError(f"max_lines must be between 1 and {MAX_CHUNK_LINES}")
    if not source:
        return []
    segments = source.split("\n")
    lines = [segment + "\n" for segment in segments[:-1]]
    if segments[-1] or not source.endswith("\n"):
        lines.append(segments[-1])
    if not lines:
        return []

    total_lines = len(lines)
    valid_symbols = [
        item for item in symbols
        if 1 <= int(item.get("start_line", 0)) <= int(item.get("end_line", 0)) <= total_lines
    ]
    # Symbol starts and ends define exact source partitions. In nested code this
    # isolates method bodies from their class headers while retaining every line.
    boundaries = {0, total_lines}
    for item in valid_symbols:
        boundaries.add(int(item["start_line"]) - 1)
        boundaries.add(int(item["end_line"]))
    sorted_boundaries = sorted(boundaries)
    documents: list[dict[str, Any]] = []
    chunk_index = 0
    for left, right in zip(sorted_boundaries, sorted_boundaries[1:]):
        region_start = left + 1
        region_end = right
        containing_symbols = [
            item for item in valid_symbols
            if int(item["start_line"]) <= region_start and int(item["end_line"]) >= region_end
        ]
        primary = min(
            containing_symbols,
            key=lambda item: (
                int(item["end_line"]) - int(item["start_line"]),
                item.get("symbol_type", ""),
                item.get("symbol_name", ""),
            ),
            default=None,
        )
        overlapping = [
            item for item in valid_symbols
            if int(item["start_line"]) <= region_end and int(item["end_line"]) >= region_start
        ]
        names = sorted({
            str(name)
            for item in overlapping
            for name in (item.get("symbol_name"), item.get("qualified_name"))
            if name
        }, key=lambda name: (name.casefold(), name))
        start_line = region_start
        while start_line <= region_end:
            end_line = min(region_end, start_line + max_lines - 1)
            documents.append({
                "chunk_index": chunk_index,
                "file_path": file_path,
                "language": language,
                "content_hash": content_hash,
                "start_line": start_line,
                "end_line": end_line,
                "content": "".join(lines[start_line - 1:end_line]),
                "symbol_name": primary.get("symbol_name") if primary else None,
                "symbol_type": primary.get("symbol_type") if primary else None,
                "symbol_names": " ".join(names),
            })
            chunk_index += 1
            start_line = end_line + 1

    return documents
