from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..knowledge import load_synced_courses
from ..storage import ROOT, read_json, slugify
from .course_routing import rank_courses
from .contracts import ResourceDescriptor, ResourceRole, SourceChunk, UserIntent
from .quiz_permission import timed_quiz_warning


MAX_CHUNK_CHARS = 1800
MAX_TOTAL_CHUNKS = 90


def select_course(intent: UserIntent) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    courses = load_synced_courses(refresh_if_missing=True)
    ranked = rank_courses(intent.prompt, courses)
    if not ranked:
        return None, []
    if len(ranked) > 1 and ranked[0]["score"] == ranked[1]["score"]:
        return None, [dict(course) for course in ranked[:5]]
    selected = dict(ranked[0])
    selected.pop("score", None)
    return selected, []


def discover_document_resources(intent: UserIntent, selected_course: dict[str, Any] | None) -> tuple[list[ResourceDescriptor], list[SourceChunk]]:
    index = read_json(ROOT / "state" / "document_index.json", default={})
    documents = index.get("documents", []) if isinstance(index, dict) else []
    selected_slug = slugify(str((selected_course or {}).get("title") or "")) if selected_course else ""
    resources: list[ResourceDescriptor] = []
    chunks: list[SourceChunk] = []
    source_index = 1
    for document in documents:
        path = str(document.get("path") or "")
        if selected_slug and selected_slug not in slugify(path):
            continue
        title = str(document.get("name") or Path(path).name or f"Source {source_index}")
        role = classify_resource_role(title, path)
        source_id = f"S{source_index}"
        status = "selected" if _should_select_role(intent, role) else "available_not_used"
        reason = _selection_reason(intent, role)
        pages = document.get("pages", []) if isinstance(document.get("pages"), list) else []
        resources.append(
            ResourceDescriptor(
                id=source_id,
                title=title,
                role=role,
                status=status,
                path=path or None,
                url=document.get("url"),
                page_count=len(pages) or None,
                reason=reason,
            )
        )
        if status == "selected":
            for page in pages:
                text = _trim_text(str(page.get("text") or ""), MAX_CHUNK_CHARS)
                if not text:
                    continue
                chunks.append(
                    SourceChunk(
                        source_id=source_id,
                        title=title,
                        role=role,
                        path=path or None,
                        page=page.get("page") if isinstance(page.get("page"), int) else None,
                        text=text,
                    )
                )
                if len(chunks) >= MAX_TOTAL_CHUNKS:
                    break
        source_index += 1
        if len(chunks) >= MAX_TOTAL_CHUNKS:
            break
    return resources, chunks


def quiz_resource_descriptor(intent: UserIntent, permission: dict[str, object]) -> list[ResourceDescriptor]:
    if not intent.wants_quiz_style:
        return []
    status = "authorization_required" if permission.get("status") == "needs-user-authorization" else "selected" if permission.get("allowed") else "excluded"
    reason = "User requested Moodle-like theory questions."
    if status == "authorization_required":
        reason += " Quiz pages require explicit user authorization before opening."
    return [
        ResourceDescriptor(
            id="Q1",
            title="Moodle quizzes/tests",
            role="quiz_style_source",
            status=status,
            reason=reason,
            safety_note=timed_quiz_warning(),
        )
    ]


def classify_resource_role(title: str, path: str = "") -> ResourceRole:
    name = f"{title} {path}".casefold()
    if any(term in name for term in ["quiz", "test block", "kontrollfragen"]):
        return "quiz_style_source"
    if any(term in name for term in ["formelsammlung", "formula"]):
        return "formula_sheet"
    if any(term in name for term in ["lösung", "loesung", "solution"]):
        return "solution"
    if any(term in name for term in ["beispiel", "exercise", "fragen"]):
        return "exercise"
    if any(term in name for term in ["datenblatt", "datasheet", "2n3055", "bc546", "nichia"]):
        return "datasheet"
    if any(term in name for term in ["assignment", "abgabe"]):
        return "assignment"
    if Path(title).suffix.lower() == ".pdf" or title:
        return "theory"
    return "other"


def _should_select_role(intent: UserIntent, role: ResourceRole) -> bool:
    if role in {"theory", "formula_sheet"}:
        return True
    if not intent.wants_complete_theory and role in {"exercise", "solution"}:
        return True
    return False


def _selection_reason(intent: UserIntent, role: ResourceRole) -> str:
    if role == "theory":
        return "Selected as theory source for the requested study document."
    if role == "formula_sheet":
        return "Selected as formula/reference source supporting the theory summary."
    if role in {"exercise", "solution"}:
        return "Available but not selected for complete theory unless explicitly needed for practice material."
    if role == "datasheet":
        return "Excluded from main theory summary unless the user asks for component datasheets."
    return "Available course resource."


def _trim_text(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip() + " ..."
