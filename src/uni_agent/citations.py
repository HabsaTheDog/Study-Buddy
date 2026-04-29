from __future__ import annotations

from .types import SourceRef


def format_markdown_sources(sources: list[SourceRef]) -> str:
    if not sources:
        return "Sources:\n- Not sufficiently sourced. Do not use as final answer."

    lines = ["Sources:"]
    for source in sources:
        parts = [f"`{source.title}`"]
        if source.page is not None:
            parts.append(f"page {source.page}")
        if source.section:
            parts.append(f'section "{source.section}"')
        if source.url:
            parts.append(source.url)
        lines.append("- " + ", ".join(parts))
    return "\n".join(lines)
