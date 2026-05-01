from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .knowledge import load_synced_courses
from .storage import ROOT, env_with_dotenv, read_json, slugify, utc_now, write_json
from .typst import compile_typst_pdf, write_study_guide_typst


SCHEMA_PATH = ROOT / "config" / "study_doc.schema.json"
EXAM_STUDY_GUIDE_SCHEMA_PATH = ROOT / "config" / "exam_study_guide.schema.json"
MAX_EXCERPT_CHARS = 2000
STUDY_GUIDE_SOURCE_LIMIT = 20
DOCUMENT_MODES = {
    "auto",
    "exam_study_guide",
    "topic_summary",
    "cheat_sheet",
    "assignment_brief",
    "generic_document",
}
SOURCE_SELECTION_PRIORITIES = [
    "DYN2_Formelsammlung",
    "Zusammenfassung",
    "AD_1_",
    "AD_2_",
    "AD_WH_",
    "AD_3_",
    "AD_4_",
    "AD_5_",
    "AltePrfg",
]


def generate_study_document(
    prompt: str,
    *,
    request_dir: Path | None = None,
    output_format: str = "markdown+pdf",
    style: str = "academic_study_guide",
    mode: str = "auto",
) -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = ROOT / "output" / "study-docs" / f"{timestamp}_{slugify(prompt, 'study-doc')[:80]}"
    run_dir.mkdir(parents=True, exist_ok=True)

    env = env_with_dotenv()
    doc_kind = _document_kind(prompt, style, mode=mode or env.get("STUDY_DOC_DEFAULT_MODE", "auto"))
    selected_course, ambiguous_courses = _select_course(prompt)
    output_language = _resolve_output_language(prompt, selected_course, env)
    citation_style = env.get("STUDY_DOC_CITATION_STYLE", "endnotes").strip().casefold() or "endnotes"
    terms = _terms_from_prompt(prompt)
    excerpts = _select_source_excerpts(
        prompt=prompt,
        terms=terms,
        selected_course=selected_course,
        limit=STUDY_GUIDE_SOURCE_LIMIT if doc_kind == "exam_study_guide" else 8,
        role_balance=doc_kind == "exam_study_guide",
    )
    auto_download_attempted = False
    if not excerpts and selected_course:
        auto_download_attempted = _auto_download_selected_course_materials(selected_course)
        if auto_download_attempted:
            excerpts = _select_source_excerpts(
                prompt=prompt,
                terms=terms,
                selected_course=selected_course,
                limit=STUDY_GUIDE_SOURCE_LIMIT if doc_kind == "exam_study_guide" else 8,
                role_balance=doc_kind == "exam_study_guide",
            )
    excerpts = _with_source_ids(excerpts) if excerpts else excerpts

    request_payload = {
        "created_at": utc_now(),
        "prompt": prompt,
        "status": "in-progress",
        "document_kind": doc_kind,
        "output_format": output_format,
        "style": style,
        "mode": doc_kind,
        "output_language": output_language,
        "citation_style": citation_style,
        "selected_course": selected_course,
        "auto_download_attempted": auto_download_attempted,
        "request_dir": str(request_dir.relative_to(ROOT)) if request_dir and request_dir.is_relative_to(ROOT) else str(request_dir) if request_dir else None,
    }
    write_json(run_dir / "request.json", request_payload)

    if ambiguous_courses:
        _write_ambiguous_courses(run_dir, prompt, ambiguous_courses)
        if request_dir:
            _write_request_link(request_dir, prompt, run_dir, "needs-more-context")
        return run_dir

    write_json(run_dir / "sources.json", {"generated_at": utc_now(), "sources": excerpts})

    packet = _build_packet(
        prompt=prompt,
        doc_kind=doc_kind,
        output_format=output_format,
        style=style,
        selected_course=selected_course,
        source_excerpts=excerpts,
        output_language=output_language,
        citation_style=citation_style,
    )
    packet_path = run_dir / "packet.json"
    response_path = run_dir / "generator-response.json"
    transcript_path = run_dir / "generator-transcript.json"
    write_json(packet_path, packet)

    generator_result = _run_local_document_generator(
        packet,
        response_path=response_path,
        transcript_path=transcript_path,
    )

    if generator_result.get("ok") and isinstance(generator_result.get("parsed"), dict):
        payload = _normalize_payload(generator_result["parsed"], prompt, doc_kind, selected_course, excerpts, output_language=output_language)
    else:
        generator_required = _truthy(env.get("STUDY_DOC_GENERATOR_REQUIRED", "false"))
        if generator_required:
            _write_generator_failure(run_dir, prompt, generator_result)
            _mark_request(run_dir, "failed", generator_result=generator_result)
            if request_dir:
                _write_request_link(request_dir, prompt, run_dir, "failed")
            return run_dir
        payload = _fallback_payload(prompt, doc_kind, selected_course, excerpts, reason=generator_result.get("reason"), output_language=output_language)

    validation_errors = _validate_payload(payload, excerpts, doc_kind=doc_kind)
    if validation_errors:
        write_json(run_dir / "study-guide.raw.json", payload)
        _write_validation_failure(run_dir, prompt, validation_errors)
        write_json(
            run_dir / "render-result.json",
            {
                "ok": False,
                "reason": "study-document-validation-failed",
                "errors": validation_errors,
                "pdf_attempted": False,
            },
        )
        _mark_request(run_dir, "failed", validation_errors=validation_errors)
        if request_dir:
            _write_request_link(request_dir, prompt, run_dir, "failed")
        return run_dir

    write_json(run_dir / "study-guide.json", payload)
    _write_markdown(payload, run_dir / "study-guide.md", doc_kind=doc_kind)

    render_result: dict[str, Any] = {"ok": True, "reason": "render-skipped", "pdf_attempted": False}
    wants_typst = output_format in {"markdown+pdf", "typst", "pdf"}
    if wants_typst:
        typst_path = write_study_guide_typst(payload, run_dir / "study-guide.typ", doc_kind=doc_kind)
        render_result = {
            "ok": True,
            "reason": "typst-written",
            "source": str(typst_path.relative_to(ROOT)),
            "pdf_attempted": False,
        }
        if output_format in {"markdown+pdf", "pdf"}:
            render_result = compile_typst_pdf(typst_path, run_dir / "study-guide.pdf")
    write_json(run_dir / "render-result.json", render_result)

    _mark_request(run_dir, "completed", render_result=render_result)
    _write_stable_outputs(payload, run_dir, render_result)
    if request_dir:
        _write_request_link(request_dir, prompt, run_dir, "completed")
    return run_dir


def _document_kind(prompt: str, style: str, *, mode: str = "auto") -> str:
    normalized_mode = (mode or "auto").replace("-", "_").casefold()
    if normalized_mode in {"study_guide", "exam_guide", "exam_study_guide"}:
        return "exam_study_guide"
    if normalized_mode in {"summary", "topic_summary"}:
        return "topic_summary"
    if normalized_mode in {"cheat_sheet", "formula_sheet"}:
        return "cheat_sheet"
    if normalized_mode in {"assignment", "assignment_brief"}:
        return "assignment_brief"
    if normalized_mode in {"generic", "generic_document"}:
        return "generic_document"
    prompt_lower = prompt.casefold()
    if any(term in prompt_lower for term in ["cheat sheet", "formula sheet", "formelsammlung", "spickzettel"]):
        return "cheat_sheet"
    if any(term in prompt_lower for term in ["assignment", "abgabe", "homework", "lab report", "bericht"]):
        return "assignment_brief"
    if any(term in prompt_lower for term in ["study guide", "exam", "prüfung", "pruefung", "test lernen", "lernplan", "prepare", "prüfungsvorbereitung", "pruefungsvorbereitung"]):
        return "exam_study_guide"
    if "summary" in prompt_lower or "summarize" in prompt_lower or "zusammenfassung" in prompt_lower or "overview" in prompt_lower:
        return "topic_summary"
    if style.replace("-", "_") == "academic_study_guide":
        return "exam_study_guide"
    return "generic_document"


def _select_course(prompt: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    courses = load_synced_courses(refresh_if_missing=True)
    ranked = _rank_courses(prompt, courses)
    if not ranked:
        return None, []
    if len(ranked) > 1 and ranked[0]["score"] == ranked[1]["score"] and ranked[0]["score"] < 8:
        return None, [dict(course) for course in ranked[:5]]
    selected = dict(ranked[0])
    selected.pop("score", None)
    return selected, []


def _rank_courses(prompt: str, courses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    terms = _terms_from_prompt(prompt)
    prompt_lower = prompt.casefold()
    aliases = {
        "math": ["math", "mathe", "mathematik", "maes", "engineering science"],
        "electrical": ["elektrotechnik", "electrical"],
        "dynamics": ["dynamik", "dynamic"],
        "english": ["english", "eng"],
    }
    ranked: list[dict[str, Any]] = []
    for course in courses:
        title = str(course.get("title") or "")
        haystack = title.casefold()
        score = sum(4 for term in terms if term in haystack)
        for canonical, values in aliases.items():
            if canonical in terms or any(value in prompt_lower for value in values):
                if any(value in haystack for value in values):
                    score += 8
        if any(marker in prompt_lower for marker in ["dynamik 1", "dyn1", "phdyn", "physikalische grundlagen der dynamik"]):
            if "phdyn" in haystack or "physikalische grundlagen der dynamik" in haystack:
                score += 20
            if "dyn2" in haystack or "anwendungen der dynamik" in haystack:
                score -= 6
        if any(marker in prompt_lower for marker in ["dynamik 2", "dyn2", "anwendungen der dynamik"]):
            if "dyn2" in haystack or "anwendungen der dynamik" in haystack:
                score += 20
        if score:
            ranked.append({**course, "score": score})
    ranked.sort(key=lambda item: (-item["score"], str(item.get("title") or "")))
    return ranked


def _select_source_excerpts(
    *,
    prompt: str,
    terms: set[str],
    selected_course: dict[str, Any] | None,
    limit: int,
    role_balance: bool = False,
) -> list[dict[str, Any]]:
    index = read_json(ROOT / "state" / "document_index.json", default={})
    documents = index.get("documents", []) if isinstance(index, dict) else []
    if not documents:
        return []

    selected_slug = slugify(str(selected_course.get("title") or "")) if selected_course else ""
    if selected_slug:
        course_documents = [
            document
            for document in documents
            if selected_slug in slugify(str(document.get("path") or ""))
        ]
        if not course_documents:
            return []
        documents = course_documents

    prompt_lower = prompt.casefold()
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for doc_index, document in enumerate(documents):
        path = str(document.get("path") or "")
        name = str(document.get("name") or Path(path).name or "source")
        course_boost = 8 if selected_slug and selected_slug in slugify(path) else 0
        name_boost = sum(2 for term in terms if term in name.casefold())
        priority_boost = _source_priority(name) if selected_slug or any(term in prompt_lower for term in ["dyn", "dynamik", "dynamic"]) else 0
        if "current" in prompt_lower or "aktuell" in prompt_lower:
            name_boost += 1
        for page in document.get("pages", []):
            text = str(page.get("text") or "")
            normalized = text.casefold()
            score = course_boost + name_boost + priority_boost + sum(1 for term in terms if term in normalized)
            if score <= 0:
                continue
            citation = _citation_for_document(document, page)
            role = _classify_source_role({**document, "title": name, "path": path})
            scored.append(
                (
                    score,
                    -doc_index,
                    {
                        "title": citation["title"],
                        "kind": citation["kind"],
                        "path": citation["path"],
                        "url": citation["url"],
                        "page": citation["page"],
                        "section": citation["section"],
                        "role": role,
                        "score": score,
                        "text": _trim_text(text, MAX_EXCERPT_CHARS),
                    },
                )
            )
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return _diverse_source_selection(scored, limit, role_balance=role_balance)


def _auto_download_selected_course_materials(selected_course: dict[str, Any]) -> bool:
    try:
        from .documents import download_course_materials

        env = env_with_dotenv()
        limit = int(env.get("STUDY_DOC_AUTO_DOWNLOAD_LIMIT", "80"))
        download_course_materials(selected_course, download_limit=limit)
        return True
    except Exception:
        return False


def _source_priority(name: str) -> int:
    for index, marker in enumerate(SOURCE_SELECTION_PRIORITIES):
        if marker.casefold() in name.casefold():
            return max(1, len(SOURCE_SELECTION_PRIORITIES) - index)
    if re.match(r"^[1-5]_", name):
        return 1
    return 0


def _diverse_source_selection(scored: list[tuple[int, int, dict[str, Any]]], limit: int, *, role_balance: bool = False) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_keys: set[tuple[str | None, int | None]] = set()
    per_title: dict[str, int] = {}

    def add(excerpt: dict[str, Any], *, max_per_title: int) -> None:
        key = (excerpt.get("path"), excerpt.get("page"))
        if key in seen_keys or len(selected) >= limit:
            return
        title = str(excerpt.get("title") or "")
        if per_title.get(title, 0) >= max_per_title:
            return
        selected.append(excerpt)
        seen_keys.add(key)
        per_title[title] = per_title.get(title, 0) + 1

    if not role_balance:
        return [item[2] for item in scored[:limit]]

    for marker in SOURCE_SELECTION_PRIORITIES:
        for _, _, excerpt in scored:
            if marker.casefold() in str(excerpt.get("title") or "").casefold():
                add(excerpt, max_per_title=2)
                break

    for role in ["formula_sheet", "summary", "lecture_slides", "worked_example", "old_exam", "assignment"]:
        for _, _, excerpt in scored:
            if excerpt.get("role") == role:
                add(excerpt, max_per_title=3)
                break

    for _, _, excerpt in scored:
        add(excerpt, max_per_title=4)
    return selected


def _citation_for_document(document: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    path = str(document.get("path") or "")
    suffix = str(document.get("suffix") or Path(path).suffix).lower()
    return {
        "title": str(document.get("name") or Path(path).name or "source"),
        "kind": "pdf" if suffix == ".pdf" else "local_file",
        "url": document.get("url"),
        "path": path or None,
        "page": page.get("page") if isinstance(page.get("page"), int) else None,
        "section": page.get("section"),
    }


def _classify_source_role(source: dict[str, Any]) -> str:
    name = f"{source.get('name') or source.get('title') or ''} {source.get('path') or ''}".casefold()
    if "formelsammlung" in name:
        return "formula_sheet"
    if "zusammenfassung" in name:
        return "summary"
    basename = Path(str(source.get("path") or source.get("name") or "")).name
    if basename.startswith("AD_"):
        return "lecture_slides"
    if any(marker.casefold() in name for marker in ["beispiel", "_ss", "_vk", "_sw", "_mtm", "_sp"]):
        return "worked_example"
    if any(marker in name for marker in ["prfg", "prüfung", "pruefung", "alte"]):
        return "old_exam"
    if any(marker in name for marker in ["assignment", "abgabe"]):
        return "assignment"
    return "other"


def _build_packet(
    *,
    prompt: str,
    doc_kind: str,
    output_format: str,
    style: str,
    selected_course: dict[str, Any] | None,
    source_excerpts: list[dict[str, Any]],
    output_language: str,
    citation_style: str,
) -> dict[str, Any]:
    source_excerpts = _with_source_ids(source_excerpts)
    source_map = [_source_map_entry(excerpt) for excerpt in source_excerpts]
    if doc_kind == "exam_study_guide":
        task = "generate_learning_focused_exam_study_guide"
        rules = [
            "Your job is to create a learning-first exam preparation guide. The main body must help the student understand what to learn, why it matters, how to solve typical tasks, and how to practice.",
            f"Write the final document in {'German' if output_language == 'de' else 'English' if output_language == 'en' else output_language}.",
            "Use sources to determine topics, formulas, examples, and priorities.",
            "Explain concepts in student-friendly language.",
            "Include formulas only when useful and explain when to use them.",
            "Write formula fields in Typst-friendly plain math notation: use variable names like omega, lambda, phi, rho for Greek letters; use _ for subscripts, ^ for powers, ' for derivatives, / for fractions, semicolons to separate multiple equations, and do not wrap formulas in Markdown, LaTeX delimiters, or code fences.",
            "Prefer a useful, well-structured output over citation completeness.",
            "If sources are thin, use them as topic hints and keep the document practical.",
        ]
    else:
        task = "generate_study_document"
        rules = [
            "Use the provided source excerpts as topic hints and priority signals.",
            "Do not include source citations in the final user-facing document.",
            "Prefer compact, well-structured, useful output over strict auditability.",
            "Do not browse Moodle or the web.",
            f"Write the final document in {'German' if output_language == 'de' else 'English' if output_language == 'en' else output_language}.",
        ]
    return {
        "created_at": utc_now(),
        "task": task,
        "prompt": prompt,
        "document_kind": doc_kind,
        "mode": doc_kind,
        "output_format": output_format,
        "style": style,
        "output_language": output_language,
        "citation_style": citation_style,
        "selected_course": selected_course,
        "rules": rules,
        "source_map": source_map,
        "source_excerpts": source_excerpts,
    }


def _run_local_document_generator(packet: dict[str, Any], *, response_path: Path, transcript_path: Path) -> dict[str, Any]:
    """Generate document payload in-process.

    The interactive main agent is responsible for high-quality synthesis when it
    calls this workflow. The CLI fallback stays deterministic and source-aware
    without spawning a nested Codex process.
    """
    prompt = str(packet.get("prompt") or "")
    doc_kind = str(packet.get("document_kind") or packet.get("mode") or "generic_document")
    selected_course = packet.get("selected_course") if isinstance(packet.get("selected_course"), dict) else None
    excerpts = packet.get("source_excerpts") if isinstance(packet.get("source_excerpts"), list) else []
    output_language = str(packet.get("output_language") or "de")
    payload = _fallback_payload(
        prompt,
        doc_kind,
        selected_course,
        excerpts,
        reason="local-document-generator",
        output_language=output_language,
    )
    payload["generated_by"] = "main_agent_local_document_generator"
    write_json(response_path, payload)
    transcript_path.write_text(
        json.dumps(
            {
                "command": "in-process local document generator",
                "returncode": 0,
                "stdout": "",
                "stderr": "",
                "response_path": str(response_path),
                "reason": "local-document-generator",
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"ok": True, "reason": "local-document-generator", "parsed": payload}


def _with_source_ids(excerpts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**excerpt, "source_id": f"S{index}"} for index, excerpt in enumerate(excerpts, start=1)]


def _source_map_entry(excerpt: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": excerpt.get("source_id"),
        "title": excerpt.get("title"),
        "path": excerpt.get("path"),
        "url": excerpt.get("url"),
        "page": excerpt.get("page"),
        "section": excerpt.get("section"),
        "role": excerpt.get("role"),
    }


def _run_generator(packet_path: Path, response_path: Path, transcript_path: Path, *, doc_kind: str) -> dict[str, Any]:
    env = env_with_dotenv()
    configured = env.get("STUDY_DOC_GENERATOR_COMMAND", "").strip()
    if configured.casefold() in {"0", "false", "off", "none", "disabled"}:
        transcript_path.write_text(
            json.dumps(
                {
                    "command": configured,
                    "returncode": None,
                    "stdout": "",
                    "stderr": "",
                    "response_path": str(response_path),
                    "reason": "study-doc-generator-disabled",
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return {"ok": False, "reason": "study-doc-generator-disabled"}

    timeout = int(env.get("STUDY_DOC_TIMEOUT_SECONDS", "180"))
    diagnostics = _generator_diagnostics(packet_path, doc_kind=doc_kind, timeout=timeout, configured=bool(configured), env=env)
    try:
        if configured:
            result = _run_custom_generator(configured, packet_path, response_path, env, timeout, doc_kind=doc_kind)
        else:
            result = _run_codex_generator(packet_path, response_path, env, timeout, doc_kind=doc_kind)
    except FileNotFoundError as exc:
        return {"ok": False, "reason": "study-doc-generator-command-not-found", "stderr": str(exc)}
    except subprocess.TimeoutExpired as exc:
        stdout = _process_text(exc.stdout)
        stderr = _process_text(exc.stderr)
        transcript_path.write_text(
            json.dumps(
                {
                    "command": getattr(exc, "cmd", None),
                    "returncode": None,
                    "stdout": stdout,
                    "stderr": stderr,
                    "response_path": str(response_path),
                    "reason": "study-doc-generator-timeout",
                    "timeout_seconds": timeout,
                    "diagnostics": diagnostics,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return {
            "ok": False,
            "reason": "study-doc-generator-timeout",
            "stdout": stdout,
            "stderr": stderr,
            "diagnostics": diagnostics,
        }

    transcript = {
        "command": result.get("command"),
        "returncode": result.get("returncode"),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "response_path": str(response_path),
        "diagnostics": diagnostics,
    }
    transcript_path.write_text(json.dumps(transcript, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    response_text = ""
    if response_path.exists():
        response_text = response_path.read_text(encoding="utf-8").strip()
    if not response_text:
        response_text = result.get("stdout", "").strip()

    parsed = _parse_json_response(response_text)
    if result.get("returncode") != 0:
        return {
            "ok": False,
            "reason": "study-doc-generator-nonzero-exit",
            "returncode": result.get("returncode"),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "parsed": parsed,
        }
    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "reason": "study-doc-generator-invalid-json",
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
        }
    write_json(response_path, parsed)
    return {"ok": True, "reason": "study-doc-generator-response", "parsed": parsed}


def _process_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _generator_diagnostics(packet_path: Path, *, doc_kind: str, timeout: int, configured: bool, env: dict[str, str]) -> dict[str, Any]:
    schema_path = _schema_path_for_doc_kind(doc_kind)
    return {
        "doc_kind": doc_kind,
        "timeout_seconds": timeout,
        "configured_custom_generator": configured,
        "packet_bytes": packet_path.stat().st_size if packet_path.exists() else None,
        "schema_bytes": schema_path.stat().st_size if schema_path.exists() else None,
        "codex_model": env.get("STUDY_DOC_CODEX_MODEL") or None,
        "codex_reasoning_effort": env.get("STUDY_DOC_CODEX_REASONING_EFFORT", "medium"),
    }


def _run_custom_generator(
    command_template: str,
    packet_path: Path,
    response_path: Path,
    env: dict[str, str],
    timeout: int,
    doc_kind: str,
) -> dict[str, Any]:
    schema_path = _schema_path_for_doc_kind(doc_kind)
    command = command_template.format(
        packet=str(packet_path),
        output=str(response_path),
        schema=str(schema_path),
        root=str(ROOT),
    )
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env={
            **env,
            "STUDY_DOC_PACKET_PATH": str(packet_path),
            "STUDY_DOC_OUTPUT_PATH": str(response_path),
            "STUDY_DOC_SCHEMA_PATH": str(schema_path),
        },
        shell=True,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _run_codex_generator(packet_path: Path, response_path: Path, env: dict[str, str], timeout: int, *, doc_kind: str) -> dict[str, Any]:
    codex = shutil.which("codex")
    if not codex:
        return {
            "command": None,
            "returncode": 127,
            "stdout": "",
            "stderr": "codex executable not found and STUDY_DOC_GENERATOR_COMMAND is not configured",
        }
    schema_path = _schema_path_for_doc_kind(doc_kind)
    packet_text = packet_path.read_text(encoding="utf-8")
    prompt = f"""You are generating a citation-backed Study Buddy document.

Return JSON only matching this schema:
{schema_path}

Use only the packet JSON below. Follow the packet rules exactly. Do not browse, do not inspect files, and do not run shell commands. Return JSON only.

Packet JSON:
```json
{packet_text}
```
"""
    command = [
        codex,
        "exec",
        "--cd",
        str(ROOT),
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "-c",
        f"model_reasoning_effort=\"{env.get('STUDY_DOC_CODEX_REASONING_EFFORT', 'medium')}\"",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(response_path),
    ]
    codex_model = env.get("STUDY_DOC_CODEX_MODEL", "").strip()
    if codex_model:
        command[2:2] = ["--model", codex_model]
    completed = subprocess.run(
        command,
        input=prompt,
        cwd=ROOT,
        env={
            **env,
            "STUDY_DOC_PACKET_PATH": str(packet_path),
            "STUDY_DOC_OUTPUT_PATH": str(response_path),
            "STUDY_DOC_SCHEMA_PATH": str(schema_path),
        },
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _schema_path_for_doc_kind(doc_kind: str) -> Path:
    return EXAM_STUDY_GUIDE_SCHEMA_PATH if doc_kind == "exam_study_guide" else SCHEMA_PATH


def _normalize_payload(
    payload: dict[str, Any],
    prompt: str,
    doc_kind: str,
    selected_course: dict[str, Any] | None,
    excerpts: list[dict[str, Any]],
    *,
    output_language: str,
) -> dict[str, Any]:
    if doc_kind == "exam_study_guide":
        return _normalize_exam_study_guide_payload(payload, prompt, selected_course, excerpts, output_language)
    source_notes = [_citation_from_excerpt(excerpt) for excerpt in excerpts]
    normalized = {
        "title": str(payload.get("title") or _default_title(prompt, doc_kind)),
        "subtitle": payload.get("subtitle"),
        "course": payload.get("course") or (selected_course or {}).get("title"),
        "topic": str(payload.get("topic") or _topic_label(prompt)),
        "audience": str(payload.get("audience") or "FH Technikum Wien student"),
        "generated_at": str(payload.get("generated_at") or utc_now()),
        "learning_objectives": _string_list(payload.get("learning_objectives")),
        "key_concepts": _normalize_key_concepts(payload.get("key_concepts"), source_notes),
        "sections": _normalize_sections(payload.get("sections"), source_notes),
        "practice_checkpoints": _normalize_checkpoints(payload.get("practice_checkpoints"), source_notes),
        "common_pitfalls": _normalize_pitfalls(payload.get("common_pitfalls"), source_notes),
        "source_notes": _normalize_citations(payload.get("source_notes"), source_notes) or source_notes,
        "risk_flags": _string_list(payload.get("risk_flags")),
    }
    return normalized


def _normalize_exam_study_guide_payload(
    payload: dict[str, Any],
    prompt: str,
    selected_course: dict[str, Any] | None,
    excerpts: list[dict[str, Any]],
    output_language: str,
) -> dict[str, Any]:
    source_map = _normalize_source_map(payload.get("source_map"), excerpts)
    valid_ids = {item["id"] for item in source_map}
    source_notes = [_citation_from_source_map_item(item) for item in source_map]
    return {
        "title": str(payload.get("title") or _default_title(prompt, "exam_study_guide")),
        "course": payload.get("course") or (selected_course or {}).get("title"),
        "topic": str(payload.get("topic") or _topic_label(prompt)),
        "audience": str(payload.get("audience") or "FH Technikum Wien student preparing for an exam"),
        "language": str(payload.get("language") or output_language),
        "generated_at": str(payload.get("generated_at") or utc_now()),
        "exam_focus": _normalize_exam_focus(payload.get("exam_focus"), prompt),
        "topic_map": _normalize_source_id_items(
            payload.get("topic_map"),
            valid_ids,
            required=["name", "priority", "what_it_is", "what_you_must_be_able_to_do", "subtopics", "typical_exam_tasks", "learning_checks", "source_ids"],
            defaults={"priority": "medium"},
        ),
        "learning_path": _normalize_learning_path(payload.get("learning_path"), output_language),
        "formula_cards": _normalize_source_id_items(
            payload.get("formula_cards"),
            valid_ids,
            required=["name", "formula", "variables", "when_to_use", "warning", "source_ids"],
            defaults={"warning": None},
        ),
        "problem_solving_methods": _normalize_source_id_items(
            payload.get("problem_solving_methods"),
            valid_ids,
            required=["name", "applies_to", "steps", "common_traps", "source_ids"],
        ),
        "practice_plan": _normalize_source_id_items(
            payload.get("practice_plan"),
            valid_ids,
            required=["block", "tasks", "success_criteria", "source_ids"],
        ),
        "self_check": _normalize_source_id_items(
            payload.get("self_check"),
            valid_ids,
            required=["question", "expected_answer", "topic", "source_ids"],
        ),
        "common_mistakes": _normalize_source_id_items(
            payload.get("common_mistakes"),
            valid_ids,
            required=["mistake", "why_it_happens", "how_to_avoid", "source_ids"],
        ),
        "source_map": source_map,
        "source_notes": _normalize_citations(payload.get("source_notes"), source_notes) or source_notes,
        "warnings": _string_list(payload.get("warnings")),
        "risk_flags": _string_list(payload.get("risk_flags")),
    }


def _normalize_source_map(value: Any, excerpts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fallback = [_source_map_entry(excerpt) for excerpt in excerpts]
    items = value if isinstance(value, list) else []
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("id") or f"S{index}")
        normalized.append(
            {
                "id": source_id,
                "title": str(item.get("title") or "Untitled source"),
                "path": item.get("path"),
                "url": item.get("url"),
                "page": item.get("page") if isinstance(item.get("page"), int) else None,
                "section": item.get("section"),
                "role": item.get("role") or "other",
            }
        )
    return normalized or fallback


def _citation_from_source_map_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": item.get("title") or "Untitled source",
        "kind": "pdf" if str(item.get("title") or "").lower().endswith(".pdf") else "local_file",
        "url": item.get("url"),
        "path": item.get("path"),
        "page": item.get("page"),
        "section": item.get("section"),
    }


def _normalize_exam_focus(value: Any, prompt: str) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    return {
        "exam_scope_summary": str(item.get("exam_scope_summary") or f"Vorbereitung auf { _topic_label(prompt) }."),
        "how_to_use_this_guide": _string_list(item.get("how_to_use_this_guide")),
        "highest_priority_topics": _string_list(item.get("highest_priority_topics")),
        "assumed_prior_knowledge": _string_list(item.get("assumed_prior_knowledge")),
    }


def _normalize_learning_path(value: Any, output_language: str) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else []
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "phase": str(item.get("phase") or ""),
                "duration": str(item.get("duration") or ""),
                "goal": str(item.get("goal") or ""),
                "tasks": _string_list(item.get("tasks")),
                "output": str(item.get("output") or ""),
            }
        )
    if normalized:
        return normalized
    if output_language == "de":
        return [
            {"phase": "Orientierung", "duration": "Tag 1-2", "goal": "Prüfungsstoff und Formelsammlung überblicken.", "tasks": ["Themenliste erstellen.", "Formeln den Themen zuordnen."], "output": "Priorisierte Stoffliste."},
            {"phase": "Grundlagen", "duration": "Tag 3-6", "goal": "Kinematik und Massengeometrie sicher aufbauen.", "tasks": ["Beispiele ohne Lösung ansetzen.", "Fehlerliste führen."], "output": "Gelöste Basisbeispiele."},
            {"phase": "Dynamik", "duration": "Tag 7-10", "goal": "Schwerpunktsatz, Drallsatz und Freischnitte trainieren.", "tasks": ["Gemischte Aufgaben rechnen.", "Gleichungsansätze vergleichen."], "output": "Sichere Lösungsstrategie."},
            {"phase": "Vertiefung", "duration": "Tag 11-12", "goal": "Schwingungen und Schwachstellen schließen.", "tasks": ["Schwachstellen wiederholen.", "Formelkarten abfragen."], "output": "Reduzierte Fehlerliste."},
            {"phase": "Prüfungssimulation", "duration": "Tag 13-14", "goal": "Alte Prüfung unter Zeitdruck rechnen.", "tasks": ["Probeklausur schreiben.", "Fehler gezielt nacharbeiten."], "output": "Finale Wiederholungsliste."},
        ]
    return [
        {"phase": "Orientation", "duration": "Days 1-2", "goal": "Map exam scope and formula sheet.", "tasks": ["Create topic list.", "Map formulas to topics."], "output": "Prioritized scope list."},
        {"phase": "Foundations", "duration": "Days 3-6", "goal": "Practice kinematics and mass geometry.", "tasks": ["Attempt examples without solutions.", "Track errors."], "output": "Solved base examples."},
        {"phase": "Dynamics", "duration": "Days 7-10", "goal": "Train center-of-mass and angular momentum balances.", "tasks": ["Solve mixed tasks.", "Compare equation setup."], "output": "Reliable solving strategy."},
        {"phase": "Review", "duration": "Days 11-12", "goal": "Close oscillation and weak-topic gaps.", "tasks": ["Review weak topics.", "Quiz formula cards."], "output": "Reduced error list."},
        {"phase": "Simulation", "duration": "Days 13-14", "goal": "Solve an old exam under time pressure.", "tasks": ["Run mock exam.", "Review mistakes."], "output": "Final review list."},
    ]


def _normalize_source_id_items(value: Any, valid_ids: set[str], *, required: list[str], defaults: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    defaults = defaults or {}
    items = value if isinstance(value, list) else []
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out: dict[str, Any] = {}
        for key in required:
            if key == "source_ids":
                ids = [source_id for source_id in _string_list(item.get("source_ids")) if source_id in valid_ids]
                out[key] = ids
            elif key in {"what_you_must_be_able_to_do", "subtopics", "typical_exam_tasks", "learning_checks", "variables", "applies_to", "steps", "common_traps", "tasks", "success_criteria"}:
                out[key] = _string_list(item.get(key))
            elif key == "warning":
                out[key] = item.get(key) if item.get(key) is None else str(item.get(key))
            else:
                out[key] = str(item.get(key) if item.get(key) is not None else defaults.get(key, ""))
        normalized.append(out)
    return normalized


def _fallback_payload(
    prompt: str,
    doc_kind: str,
    selected_course: dict[str, Any] | None,
    excerpts: list[dict[str, Any]],
    *,
    reason: Any = None,
    output_language: str = "de",
) -> dict[str, Any]:
    if doc_kind == "cheat_sheet":
        builtin = _builtin_cheat_sheet_payload(prompt, selected_course, excerpts, output_language=output_language)
        if builtin:
            return builtin
    if doc_kind == "exam_study_guide":
        return _fallback_exam_study_guide_payload(prompt, selected_course, excerpts, reason=reason, output_language=output_language)
    citations = [_citation_from_excerpt(excerpt) for excerpt in excerpts]
    topic = _topic_label(prompt)
    topic_sections = _fallback_topic_sections(excerpts) if any(term in topic.casefold() for term in ["dyn", "dynamik"]) else []
    sections = topic_sections or _fallback_excerpt_sections(excerpts[:4])
    key_concepts = [
        {
            "term": section["heading"],
            "explanation": section["summary"],
            "citations": section["citations"],
        }
        for section in sections[:8]
    ]

    return {
        "title": _default_title(prompt, doc_kind),
        "subtitle": "Deterministic draft generated by the local document pipeline.",
        "course": (selected_course or {}).get("title"),
        "topic": topic,
        "audience": "FH Technikum Wien student",
        "generated_at": utc_now(),
        "learning_objectives": _fallback_learning_objectives(topic, doc_kind),
        "key_concepts": key_concepts,
        "sections": sections,
        "practice_checkpoints": [
            {
                "question": f"What is the main idea in {section['heading']}?",
                "answer": "Explain the concept in your own words and verify the formula, assumptions, and units before using it.",
                "citations": section["citations"],
            }
            for section in sections[:6]
        ],
        "common_pitfalls": [
            {
                "pitfall": "Formeln ohne Einsatzbedingung verwenden.",
                "correction": "Notiere zu jeder Formel kurz, wann sie gilt und welche Modellannahmen dahinterstehen.",
                "citations": citations[:1],
            },
            {
                "pitfall": "Zu viel Stoff in ein kompaktes Dokument pressen.",
                "correction": "Priorisiere Definitionen, Formeln, Vorgehensregeln und typische Fehler; lange Herleitungen auslagern.",
                "citations": citations[:1],
            }
        ],
        "source_notes": citations,
        "risk_flags": [],
    }


def _builtin_cheat_sheet_payload(
    prompt: str,
    selected_course: dict[str, Any] | None,
    excerpts: list[dict[str, Any]],
    *,
    output_language: str,
) -> dict[str, Any] | None:
    prompt_lower = prompt.casefold()
    course_title = str((selected_course or {}).get("title") or "")
    haystack = f"{prompt_lower} {course_title.casefold()}"
    if not any(marker in haystack for marker in ["dynamik", "dynamic", "phdyn", "dyn1", "dyn2"]):
        return None

    is_dyn2 = "dyn2" in haystack or "anwendungen der dynamik" in haystack
    title = "Formelsammlung Dynamik 2" if is_dyn2 else "Formelsammlung Dynamik 1"
    topic = "Anwendungen der Dynamik" if is_dyn2 else "Physikalische Grundlagen der Dynamik"
    sections = [
        (
            "Grundgroessen",
            [
                "r: Lage, v = dr/dt, a = dv/dt; omega = dphi/dt, alpha = domega/dt.",
                "SI: N = kg m/s2, J = N m, W = J/s; g = 9.81 m/s2.",
                "Bogen: s = r phi; v_t = r omega; a_t = r alpha; a_n = v2/r = r omega2.",
            ],
        ),
        (
            "Punktkinematik",
            [
                "Konstante Beschleunigung: v = v0 + a t; s = s0 + v0 t + 1/2 a t2; v2 = v02 + 2 a Delta s.",
                "Freier Fall/Wurf: y = y0 + v0y t - 1/2 g t2; vy = v0y - g t; x = v0x t.",
                "Natuerlich: v = v e_t; a = dv/dt e_t + v2/rho e_n.",
            ],
        ),
        (
            "Koordinaten",
            [
                "Kartesisch: v = x' e_x + y' e_y + z' e_z; a = x'' e_x + y'' e_y + z'' e_z.",
                "Polar/Zylinder: v = r' e_r + r phi' e_phi + z' e_z.",
                "a = (r'' - r phi'2) e_r + (r phi'' + 2 r' phi') e_phi + z'' e_z.",
            ],
        ),
        (
            "Newton & Freischnitt",
            [
                "Summe F = m a; Gewicht G = m g; Feder F = c x; Daempfer F = d x'.",
                "Reibung: Haft F_H <= mu_H N; Gleit F_G = mu_G N.",
                "Hang: G_parallel = m g sin(alpha); G_normal = m g cos(alpha).",
                "Immer: Systemgrenze, positive Richtungen, Zwangsbedingungen und aeussere Kraefte festlegen.",
            ],
        ),
        (
            "Arbeit, Energie, Leistung",
            [
                "W = Integral F dot dr; bei konstanter Kraft W = F s cos(theta).",
                "T = 1/2 m v2; V_g = m g h; V_f = 1/2 c x2.",
                "Arbeitssatz: T2 - T1 = W; konservativ: T + V = const.",
                "P = dW/dt = F dot v; rotatorisch P = M omega.",
            ],
        ),
        (
            "Impuls & Stoss",
            [
                "p = m v; Impulssatz: dp/dt = Summe F; Stoss J = Integral F dt = Delta p.",
                "Ohne aeussere Stosskraefte: Summe m_i v_i = const.",
                "Gerader zentraler Stoss: m1 u1 + m2 u2 = m1 v1 + m2 v2.",
                "Stosszahl e = (v2 - v1)/(u1 - u2); elastisch e = 1, vollplastisch e = 0.",
            ],
        ),
        (
            "Rotation starrer Koerper",
            [
                "Feste Achse: Summe M_A = I_A alpha; L_A = I_A omega.",
                "T_rot = 1/2 I omega2; ebene Bewegung: T = 1/2 m v_S2 + 1/2 I_S omega2.",
                "Reines Rollen: v_S = R omega, a_S = R alpha; Momentanpol: T = 1/2 I_P omega2.",
            ],
        ),
        (
            "Traegheitsmomente",
            [
                "Punktmasse I = m r2; Steiner: I_A = I_S + m d2.",
                "Stab Mitte: I = 1/12 m l2; Stab Ende: I = 1/3 m l2.",
                "Vollzylinder/Scheibe: I = 1/2 m R2; Ring: I = m R2.",
                "Vollkugel: I = 2/5 m R2; Hohlkugel: I = 2/3 m R2.",
            ],
        ),
        (
            "Schwerpunkt & Drall",
            [
                "Schwerpunkt: r_S = Summe(m_i r_i) / Summe(m_i); m a_S = Summe F_ext.",
                "Moment um Schwerpunkt: Summe M_S = I_S alpha.",
                "Um festen Punkt A: Summe M_A = I_A alpha.",
                "Allgemein eben: Translation des Schwerpunkts plus Rotation um Schwerpunkt kombinieren.",
            ],
        ),
        (
            "Schwingungen",
            [
                "Feder-Masse: m x'' + c x = 0; omega0 = sqrt(c/m); T = 2 pi / omega0.",
                "Loesung ungedaempft: x = A cos(omega0 t) + B sin(omega0 t).",
                "Gedaempft: m x'' + d x' + c x = 0; delta = d/(2m); omega_d = sqrt(omega02 - delta2).",
                "Erzwungen: m x'' + d x' + c x = F0 sin(Omega t).",
            ],
        ),
        (
            "Pendel & Torsion",
            [
                "Mathematisches Pendel klein: phi'' + g/l phi = 0; T = 2 pi sqrt(l/g).",
                "Physikalisches Pendel: I_A phi'' + m g s phi = 0; T = 2 pi sqrt(I_A/(m g s)).",
                "Torsion: I phi'' + c_phi phi = 0; omega0 = sqrt(c_phi/I).",
            ],
        ),
        (
            "Standardvorgehen",
            [
                "1. Skizze und Freischnitt. 2. Koordinaten und Vorzeichen. 3. Kinematik/Zwang.",
                "4. Kraft-, Momenten-, Energie- oder Impulssatz waehlen. 5. Einheiten und Grenzfaelle pruefen.",
                "Bei Reibung zuerst Haftannahme pruefen; wenn verletzt, Gleitreibung verwenden.",
            ],
        ),
    ]

    if is_dyn2:
        sections.extend(
            [
                (
                    "Starrkoerperkinematik",
                    [
                        "v_P = v_A + omega x r_PA.",
                        "a_P = a_A + alpha x r_PA + omega x (omega x r_PA).",
                        "Ebene: a_P = a_A + alpha x r_PA - omega2 r_PA.",
                        "Momentanpol: Punkt mit momentaner Geschwindigkeit null; bei reinem Rollen der Kontaktpunkt.",
                    ],
                ),
                (
                    "Drallsatz erweitert",
                    [
                        "D_A = I_A omega; dD_A/dt + m r_SA x a_A = Summe M_A.",
                        "Zusatzterm faellt weg fuer A = S, a_A = 0 oder r_SA parallel a_A.",
                        "Euler-Hauptachsen: I1 omega1' - (I2-I3) omega2 omega3 = M1; zyklisch fortsetzen.",
                    ],
                ),
            ]
        )

    return {
        "title": title,
        "subtitle": "Kompaktfassung ohne Quellenapparat.",
        "course": course_title or None,
        "topic": topic,
        "audience": "FH Technikum Wien student",
        "generated_at": utc_now(),
        "learning_objectives": [
            "Wichtige Formeln schnell finden.",
            "Einsatzbedingungen und typische Modellannahmen erkennen.",
            "Dynamikaufgaben strukturiert ansetzen.",
        ],
        "key_concepts": [
            {"term": heading, "explanation": "; ".join(details[:2]), "citations": []}
            for heading, details in sections[:6]
        ],
        "sections": [
            {"heading": heading, "summary": details[0], "details": details[1:], "worked_examples": [], "citations": []}
            for heading, details in sections
        ],
        "practice_checkpoints": [],
        "common_pitfalls": [
            {
                "pitfall": "Vorzeichen und Bezugspunkte wechseln.",
                "correction": "Koordinatensystem, Momentenpunkt und positive Drehrichtung vor dem Rechnen fixieren.",
                "citations": [],
            },
            {
                "pitfall": "Zwangsbedingungen erst nach der Dynamik formulieren.",
                "correction": "Seil-, Roll- und Gelenkbedingungen direkt nach der Kinematik ableiten.",
                "citations": [],
            },
        ],
        "source_notes": [],
        "risk_flags": [],
    }


def _fallback_learning_objectives(topic: str, doc_kind: str) -> list[str]:
    if doc_kind == "cheat_sheet":
        return [
            f"Collect the most relevant formulas and definitions for {topic}.",
            "Connect each formula to the cited source before using it.",
            "Use this as a compact reference, not as a full explanation.",
        ]
    if doc_kind == "topic_summary":
        return [
            f"Identify the central ideas in {topic}.",
            "Review the cited source pages for definitions and examples.",
            "Separate directly sourced facts from any remaining open questions.",
        ]
    return [
        f"Identify the source sections that support {topic}.",
        "Review the cited definitions, rules, and examples before using this as final study material.",
        "Trace every claim back to the listed Moodle or PDF source.",
    ]


def _fallback_exam_study_guide_payload(
    prompt: str,
    selected_course: dict[str, Any] | None,
    excerpts: list[dict[str, Any]],
    *,
    reason: Any = None,
    output_language: str = "de",
) -> dict[str, Any]:
    source_map = [_source_map_entry(excerpt) for excerpt in excerpts]
    source_notes = [_citation_from_source_map_item(item) for item in source_map]
    ids_by_role: dict[str, list[str]] = {}
    for item in source_map:
        ids_by_role.setdefault(str(item.get("role") or "other"), []).append(str(item.get("id")))

    if output_language == "de":
        return {
            "title": _default_title(prompt, "exam_study_guide"),
            "course": (selected_course or {}).get("title"),
            "topic": _topic_label(prompt),
            "audience": "FH Technikum Wien Student:in in der Prüfungsvorbereitung",
            "language": output_language,
            "generated_at": utc_now(),
            "exam_focus": {
                "exam_scope_summary": "Diese Vorlage ordnet die verfügbaren Kursquellen in prüfungsnahe Lernblöcke. Sie ist weniger detailliert als eine vollständige KI-generierte Lernhilfe.",
                "how_to_use_this_guide": ["Arbeite die Themen in Reihenfolge durch.", "Rechne zu jedem Thema mindestens ein Beispiel ohne Lösung.", "Nutze die Quellenliste am Ende zur Kontrolle."],
                "highest_priority_topics": ["Kinematik", "Massengeometrie", "Schwerpunktsatz", "Drallsatz", "Schwingungen"],
                "assumed_prior_knowledge": ["Vektorrechnung", "Ableitungen", "Freischnitt", "Grundlagen der Mechanik"],
            },
            "topic_map": _fallback_exam_topics(source_map, output_language),
            "learning_path": _normalize_learning_path([], output_language),
            "formula_cards": _fallback_formula_cards(source_map, output_language),
            "problem_solving_methods": _fallback_problem_methods(source_map, output_language),
            "practice_plan": [
                {"block": "Beispiele und alte Prüfung", "tasks": ["Rechne pro Thema mindestens ein passendes Beispiel.", "Bearbeite eine alte Prüfung unter Zeitdruck."], "success_criteria": ["Du kannst den Lösungsansatz ohne Blick in die Lösung erklären.", "Du erkennst, welche Gleichung zu welchem Aufgabentyp gehört."], "source_ids": _first_source_ids(source_map, roles=["worked_example", "old_exam", "summary"])},
            ],
            "self_check": [
                {"question": "Kannst du für jede Aufgabe zuerst Koordinatensystem, Freischnitt und gesuchte Größe festlegen?", "expected_answer": "Ja: Vor dem Rechnen stehen Skizze, bekannte Größen, Unbekannte und passender Satz/Formel fest.", "topic": "Lösungsstrategie", "source_ids": _first_source_ids(source_map)},
            ],
            "common_mistakes": [
                {"mistake": "Formeln auswendig lernen, ohne den Einsatzfall zu kennen.", "why_it_happens": "Die Formelsammlung wirkt vollständig, ersetzt aber nicht die Entscheidung, welche Modellannahmen gelten.", "how_to_avoid": "Zu jeder Formel ein Beispiel und einen typischen Trigger notieren.", "source_ids": _first_source_ids(source_map, roles=["formula_sheet", "worked_example"])},
            ],
            "source_map": source_map,
            "source_notes": source_notes,
            "warnings": [f"Generator fallback used: {reason}"] if reason else [],
            "risk_flags": [] if excerpts else [str(reason or "no-source-excerpts")],
        }

    return {
        "title": _default_title(prompt, "exam_study_guide"),
        "course": (selected_course or {}).get("title"),
        "topic": _topic_label(prompt),
        "audience": "FH Technikum Wien student preparing for an exam",
        "language": output_language,
        "generated_at": utc_now(),
        "exam_focus": {
            "exam_scope_summary": "This template organizes available course sources into exam-oriented learning blocks. It is less detailed than a full AI-generated guide.",
            "how_to_use_this_guide": ["Work through topics in order.", "Solve at least one example per topic without the solution.", "Use the source appendix for verification."],
            "highest_priority_topics": ["Kinematics", "Mass geometry", "Center-of-mass theorem", "Angular momentum", "Oscillations"],
            "assumed_prior_knowledge": ["Vector algebra", "Derivatives", "Free-body diagrams", "Mechanics basics"],
        },
        "topic_map": _fallback_exam_topics(source_map, output_language),
        "learning_path": _normalize_learning_path([], output_language),
        "formula_cards": _fallback_formula_cards(source_map, output_language),
        "problem_solving_methods": _fallback_problem_methods(source_map, output_language),
        "practice_plan": [
            {"block": "Examples and old exam", "tasks": ["Solve at least one matching example per topic.", "Do one old exam under time pressure."], "success_criteria": ["You can explain the setup without the solution.", "You recognize which equation belongs to which task type."], "source_ids": _first_source_ids(source_map, roles=["worked_example", "old_exam", "summary"])},
        ],
        "self_check": [
            {"question": "Can you define coordinate system, free-body diagram, and target quantity before calculating?", "expected_answer": "Yes: sketch, knowns, unknowns, and governing relation are clear before computation.", "topic": "Solving strategy", "source_ids": _first_source_ids(source_map)},
        ],
        "common_mistakes": [
            {"mistake": "Memorizing formulas without knowing when to use them.", "why_it_happens": "A formula sheet is complete but does not decide the model assumptions for you.", "how_to_avoid": "Attach one example and one task trigger to every important formula.", "source_ids": _first_source_ids(source_map, roles=["formula_sheet", "worked_example"])},
        ],
        "source_map": source_map,
        "source_notes": source_notes,
        "warnings": [f"Generator fallback used: {reason}"] if reason else [],
        "risk_flags": [] if excerpts else [str(reason or "no-source-excerpts")],
    }


def _fallback_excerpt_sections(excerpts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections = []
    for excerpt in excerpts:
        citation = _citation_from_excerpt(excerpt)
        summary = _first_sentences(str(excerpt.get("text") or ""), 2)
        detail_text = _trim_text(_first_sentences(str(excerpt.get("text") or ""), 5), 900)
        sections.append(
            {
                "heading": f"{excerpt.get('title')} page {excerpt.get('page')}",
                "summary": summary or "The selected source page contains relevant material for the requested topic.",
                "details": [detail_text] if detail_text else [],
                "worked_examples": [],
                "citations": [citation],
            }
        )
    return sections


def _fallback_topic_sections(excerpts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    topic_specs = [
        (
            "Punktkinematik",
            ["punktkinematik", "kartesisch", "zylinderkoordinaten", "natürliche koordinaten", "schieferwurf"],
            "Review position, velocity, and acceleration in Cartesian, cylindrical, and natural coordinates; practice tangential/normal acceleration and time-free equations.",
            [
                "Write r, v, and a in the coordinate system that matches the path.",
                "Use tangential acceleration for speed changes and normal acceleration for curvature.",
                "Practice the projectile, braking-distance, sleeve, and helix examples if available.",
            ],
        ),
        (
            "Vektorkinematik starrer Körper",
            ["vektorkinematik", "starrkörper", "momentanpol", "kopplung", "scheibe", "stab"],
            "Review moving-frame vector derivatives and the rigid-body velocity and acceleration relations for coupled planar mechanisms.",
            [
                "Set up vector equations for the relevant points before solving components.",
                "Use the instantaneous center to check velocity directions and angular-speed relations.",
                "Separate tangential and centripetal acceleration terms.",
            ],
        ),
        (
            "Massengeometrie und Trägheitsmomente",
            ["massengeometrie", "massenträgheitsmoment", "trägheitsmoment", "massenmittelpunkt", "pappus", "guldin", "steiner"],
            "Review center of mass, Pappus-Guldin rules, inertia integrals, inertia tensor notation, and parallel-axis shifts.",
            [
                "Identify the reference axis or plane before choosing an inertia formula.",
                "Use symmetry to simplify center-of-mass and deviation-moment calculations.",
                "Practice line, area, volume, rod, cylinder, and flywheel examples if available.",
            ],
        ),
        (
            "Schwerpunktsatz und Impuls",
            ["schwerpunktsatz", "impuls", "kraft", "masse", "beschleunigung", "freischneiden"],
            "Review how external forces determine center-of-mass acceleration and how impulse statements relate to the same balance.",
            [
                "Start every kinetics task with a clean free-body diagram.",
                "Keep internal forces out of the total-system balance unless you cut a subsystem free.",
                "Track signs, friction directions, and pulley constraints explicitly.",
            ],
        ),
        (
            "Drallsatz und Drehimpuls",
            ["drallsatz", "drall", "drehimpuls", "drehmoment", "euler", "kreisel"],
            "Review angular momentum balances, moment reference points, and the rigid-body specialization with inertia tensor and angular velocity.",
            [
                "Choose the moment point deliberately; note when acceleration terms vanish.",
                "Use the same sign convention for angular acceleration, torque, and rolling constraints.",
                "Practice rolling, brake, rod, cone-track, and sphere-on-cylinder examples if available.",
            ],
        ),
        (
            "Schwingungen",
            ["schwing", "feder", "dämpf", "resonanz", "pendel", "metronom"],
            "Review the free, damped, and forced oscillator cases and connect parameters to mass, stiffness, damping, and forcing.",
            [
                "Classify the case first: undamped free, damped free, or forced damped.",
                "Compute the natural frequency and damping parameter before solving constants.",
                "Practice pendulum, beam, spring-roll-mass, flywheel-spring, and swing examples if available.",
            ],
        ),
        (
            "Exam Practice",
            ["alteprfg", "prüfung", "prfg", "exam"],
            "Use the old exam material for timed mixed practice after the first pass through the formula sheet and worked examples.",
            [
                "Do old-exam tasks under time pressure only after reviewing the topic blocks.",
                "After each task, write the trigger that identifies the governing equation.",
            ],
        ),
    ]

    sections = []
    for heading, keywords, summary, details in topic_specs:
        matches = _matching_excerpts(excerpts, keywords)
        if not matches:
            continue
        citations = [_citation_from_excerpt(excerpt) for excerpt in matches[:3]]
        sections.append(
            {
                "heading": heading,
                "summary": summary,
                "details": details,
                "worked_examples": [],
                "citations": citations,
            }
        )
    return sections


def _matching_excerpts(excerpts: list[dict[str, Any]], keywords: list[str]) -> list[dict[str, Any]]:
    matches = []
    for excerpt in excerpts:
        haystack = f"{excerpt.get('title') or ''}\n{excerpt.get('path') or ''}\n{excerpt.get('text') or ''}".casefold()
        if any(keyword.casefold() in haystack for keyword in keywords):
            matches.append(excerpt)
    return matches


def _first_source_ids(source_map: list[dict[str, Any]], *, roles: list[str] | None = None, limit: int = 3) -> list[str]:
    selected = []
    for item in source_map:
        if roles and str(item.get("role") or "") not in roles:
            continue
        source_id = str(item.get("id") or "")
        if source_id:
            selected.append(source_id)
        if len(selected) >= limit:
            break
    if selected:
        return selected
    return [str(item.get("id")) for item in source_map[:limit] if item.get("id")]


def _fallback_exam_topics(source_map: list[dict[str, Any]], output_language: str) -> list[dict[str, Any]]:
    source_ids = _first_source_ids(source_map)
    if output_language == "de":
        specs = [
            ("Punktkinematik", "high", "Bewegung von Massepunkten beschreiben.", ["r, v und a in passenden Koordinaten aufstellen.", "Tangential- und Normalanteile unterscheiden."]),
            ("Vektorkinematik starrer Körper", "high", "Geschwindigkeit und Beschleunigung gekoppelter starrer Körper bestimmen.", ["Punktbeziehungen mit Winkelgeschwindigkeit aufstellen.", "Momentanpol als Kontrolle nutzen."]),
            ("Massengeometrie und Massenträgheitsmomente", "medium", "Schwerpunkte und Trägheitsmomente als Vorbereitung für Dynamikgleichungen bestimmen.", ["Steiner anwenden.", "Symmetrien erkennen."]),
            ("Schwerpunktsatz / Impuls", "high", "Äußere Kräfte mit Schwerpunktbeschleunigung verbinden.", ["Freischnitt sauber zeichnen.", "Zwangsbedingungen verwenden."]),
            ("Drallsatz / Drehimpuls", "high", "Drehmomente und Drehimpuls für rotierende Systeme bilanzieren.", ["Momentenpunkt wählen.", "Vorzeichen konsistent halten."]),
            ("Schwingungen", "medium", "Freie, gedämpfte und erzwungene Schwingungen klassifizieren.", ["Parameter identifizieren.", "Lösungstyp auswählen."]),
        ]
    else:
        specs = [
            ("Point kinematics", "high", "Describe motion of point masses.", ["Set up r, v, and a in suitable coordinates.", "Separate tangential and normal components."]),
            ("Rigid-body vector kinematics", "high", "Determine velocity and acceleration in linked rigid bodies.", ["Set up point relations with angular velocity.", "Use instantaneous center as a check."]),
            ("Mass geometry and moments of inertia", "medium", "Compute centers and inertia values for dynamics equations.", ["Apply the parallel-axis theorem.", "Recognize symmetry."]),
            ("Center-of-mass theorem / impulse", "high", "Connect external forces to center-of-mass acceleration.", ["Draw free-body diagrams.", "Use constraints."]),
            ("Angular momentum theorem", "high", "Balance torques and angular momentum for rotating systems.", ["Choose moment point.", "Keep signs consistent."]),
            ("Oscillations", "medium", "Classify free, damped, and forced oscillations.", ["Identify parameters.", "Select solution type."]),
        ]
    return [
        {
            "name": name,
            "priority": priority,
            "what_it_is": what,
            "what_you_must_be_able_to_do": todos,
            "subtopics": [],
            "typical_exam_tasks": [],
            "learning_checks": [],
            "source_ids": source_ids,
        }
        for name, priority, what, todos in specs
    ]


def _fallback_formula_cards(source_map: list[dict[str, Any]], output_language: str) -> list[dict[str, Any]]:
    source_ids = _first_source_ids(source_map, roles=["formula_sheet", "summary"])
    if output_language == "de":
        return [
            {"name": "Schwerpunktsatz", "formula": "m a_S = Summe F_ext", "variables": ["m", "a_S", "F_ext"], "when_to_use": "Wenn die Translation des Schwerpunkts gesucht ist.", "warning": "Nur äußere Kräfte in die Gesamtbilanz aufnehmen.", "source_ids": source_ids},
            {"name": "Starrkörperkinematik", "formula": "v_P = v_A + omega x r_PA", "variables": ["v_P", "v_A", "omega", "r_PA"], "when_to_use": "Wenn Geschwindigkeiten zweier Punkte eines starren Körpers verknüpft werden.", "warning": "Richtung des Winkelgeschwindigkeitsvektors beachten.", "source_ids": source_ids},
        ]
    return [
        {"name": "Center-of-mass theorem", "formula": "m a_S = sum F_ext", "variables": ["m", "a_S", "F_ext"], "when_to_use": "Use when center-of-mass translation is needed.", "warning": "Use only external forces for the total-system balance.", "source_ids": source_ids},
        {"name": "Rigid-body kinematics", "formula": "v_P = v_A + omega x r_PA", "variables": ["v_P", "v_A", "omega", "r_PA"], "when_to_use": "Use to connect velocities of two points on a rigid body.", "warning": "Check angular velocity direction.", "source_ids": source_ids},
    ]


def _fallback_problem_methods(source_map: list[dict[str, Any]], output_language: str) -> list[dict[str, Any]]:
    source_ids = _first_source_ids(source_map, roles=["summary", "lecture_slides", "worked_example"])
    if output_language == "de":
        return [
            {"name": "Dynamikaufgabe lösen", "applies_to": ["Schwerpunktsatz", "Drallsatz", "gekoppelte Systeme"], "steps": ["Skizze und Freischnitt erstellen.", "Koordinaten und Vorzeichen festlegen.", "Kinematische Zwangsbedingungen formulieren.", "Passenden Satz anwenden.", "Einheiten und Grenzfälle prüfen."], "common_traps": ["Innere Kräfte doppelt zählen.", "Momentenpunkt unklar wählen.", "Vorzeichen wechseln."], "source_ids": source_ids}
        ]
    return [
        {"name": "Solve a dynamics task", "applies_to": ["Center-of-mass theorem", "Angular momentum", "linked systems"], "steps": ["Draw sketch and free-body diagram.", "Define coordinates and signs.", "Write kinematic constraints.", "Apply the governing theorem.", "Check units and limiting cases."], "common_traps": ["Double-counting internal forces.", "Choosing unclear moment point.", "Changing sign conventions."], "source_ids": source_ids}
    ]


def _validate_payload(payload: dict[str, Any], excerpts: list[dict[str, Any]], *, doc_kind: str) -> list[str]:
    if doc_kind == "exam_study_guide":
        return _validate_exam_study_guide_payload(payload)
    errors: list[str] = []
    for key in ["title", "topic", "generated_at"]:
        if not str(payload.get(key) or "").strip():
            errors.append(f"{key}-missing")
    return sorted(set(errors))


def _validate_exam_study_guide_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ["title", "topic", "language", "generated_at"]:
        if not str(payload.get(key) or "").strip():
            errors.append(f"{key}-missing")

    required_collections = [
        "topic_map",
        "formula_cards",
        "problem_solving_methods",
    ]
    for key in required_collections:
        items = payload.get(key)
        if not isinstance(items, list) or not items:
            errors.append(f"{key}-missing")
    return sorted(set(errors))


def _blocking_exam_risk_flags(risk_flags: list[str]) -> list[str]:
    blocking_markers = [
        "no usable source",
        "no usable sources",
        "no source",
        "no sources",
        "keine quelle",
        "keine quellen",
        "nicht ausreichend belegt",
        "not sufficiently sourced",
        "invented citation",
        "invented citations",
        "erfundene quelle",
        "erfundene quellen",
        "unsupported answer",
        "unsupported final answer",
        "do not use as final answer",
    ]
    blocking: list[str] = []
    for risk in risk_flags:
        risk_lower = risk.casefold()
        if any(marker in risk_lower for marker in blocking_markers):
            blocking.append(risk)
    return blocking


def _write_markdown(payload: dict[str, Any], target: Path, *, doc_kind: str) -> None:
    if doc_kind == "exam_study_guide":
        _write_exam_study_guide_markdown(payload, target)
        return
    lines = [
        f"# {payload.get('title') or 'Study Guide'}",
        "",
        f"Course: {payload.get('course') or 'Unspecified'}",
        f"Topic: {payload.get('topic') or 'Unspecified'}",
        f"Generated: {payload.get('generated_at') or ''}",
        "",
        "## Learning Objectives",
        "",
    ]
    lines.extend(f"- {item}" for item in _string_list(payload.get("learning_objectives")))
    lines.extend(["", "## Key Concepts", ""])
    for concept in payload.get("key_concepts", []):
        if not isinstance(concept, dict):
            continue
        lines.extend(
            [
                f"### {concept.get('term') or 'Concept'}",
                "",
                str(concept.get("explanation") or ""),
                "",
            ]
        )
    lines.extend(["## Core Explanation", ""])
    for section in payload.get("sections", []):
        if not isinstance(section, dict):
            continue
        lines.extend([f"### {section.get('heading') or 'Section'}", "", str(section.get("summary") or ""), ""])
        for detail in _string_list(section.get("details")):
            lines.append(f"- {detail}")
        if section.get("details"):
            lines.append("")
        examples = section.get("worked_examples") if isinstance(section.get("worked_examples"), list) else []
        if examples:
            lines.extend(["#### Worked Examples", ""])
            for example in examples:
                if not isinstance(example, dict):
                    continue
                lines.extend(
                    [
                        f"Problem: {example.get('problem') or ''}",
                        "",
                        f"Solution: {example.get('solution') or ''}",
                        "",
                    ]
                )
        lines.append("")
    lines.extend(["## Practice Checkpoints", ""])
    for checkpoint in payload.get("practice_checkpoints", []):
        if not isinstance(checkpoint, dict):
            continue
        lines.extend(
            [
                f"Question: {checkpoint.get('question') or ''}",
                "",
                f"Answer: {checkpoint.get('answer') or ''}",
                "",
            ]
        )
    lines.extend(["## Common Pitfalls", ""])
    for pitfall in payload.get("common_pitfalls", []):
        if not isinstance(pitfall, dict):
            continue
        lines.extend(
            [
                f"### {pitfall.get('pitfall') or 'Pitfall'}",
                "",
                str(pitfall.get("correction") or ""),
                "",
            ]
        )
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _write_exam_study_guide_markdown(payload: dict[str, Any], target: Path) -> None:
    source_lookup = {item.get("id"): item for item in payload.get("source_map", []) if isinstance(item, dict)}
    focus = payload.get("exam_focus") if isinstance(payload.get("exam_focus"), dict) else {}
    lines = [
        f"# {payload.get('title') or 'Study Guide'}",
        "",
        f"Kurs: {payload.get('course') or 'Unbekannt'}",
        f"Thema: {payload.get('topic') or 'Unbekannt'}",
        f"Sprache: {payload.get('language') or ''}",
        f"Generiert: {payload.get('generated_at') or ''}",
        "",
        "## Was du für die Prüfung können solltest",
        "",
        str(focus.get("exam_scope_summary") or ""),
        "",
    ]
    priorities = _string_list(focus.get("highest_priority_topics"))
    if priorities:
        lines.extend(["Priorität:", *[f"- {item}" for item in priorities], ""])
    how_to = _string_list(focus.get("how_to_use_this_guide"))
    if how_to:
        lines.extend(["So nutzt du diesen Guide:", *[f"- {item}" for item in how_to], ""])

    lines.extend(["## Lernplan", ""])
    for item in payload.get("learning_path", []):
        if not isinstance(item, dict):
            continue
        lines.extend([f"### {item.get('phase')} ({item.get('duration')})", "", str(item.get("goal") or ""), ""])
        lines.extend(f"- {task}" for task in _string_list(item.get("tasks")))
        if item.get("output"):
            lines.append(f"- Ergebnis: {item.get('output')}")
        lines.append("")

    lines.extend(["## Themenübersicht", ""])
    for item in payload.get("topic_map", []):
        if not isinstance(item, dict):
            continue
        lines.extend([f"### {item.get('name')} ({item.get('priority')})", "", str(item.get("what_it_is") or ""), ""])
        lines.extend(["Du musst können:", *[f"- {entry}" for entry in _string_list(item.get("what_you_must_be_able_to_do"))], ""])
        typical = _string_list(item.get("typical_exam_tasks"))
        if typical:
            lines.extend(["Typische Prüfungsaufgaben:", *[f"- {entry}" for entry in typical], ""])
        lines.append("")

    lines.extend(["## Formeln und wann du sie verwendest", ""])
    for item in payload.get("formula_cards", []):
        if not isinstance(item, dict):
            continue
        lines.extend([f"### {item.get('name')}", "", f"`{item.get('formula') or ''}`", ""])
        variables = _string_list(item.get("variables"))
        if variables:
            lines.append(f"Variablen: {', '.join(variables)}")
        lines.append(f"Wann verwenden: {item.get('when_to_use') or ''}")
        if item.get("warning"):
            lines.append(f"Achtung: {item.get('warning')}")
        lines.append("")

    lines.extend(["## Aufgaben-Strategien", ""])
    for item in payload.get("problem_solving_methods", []):
        if not isinstance(item, dict):
            continue
        lines.extend([f"### {item.get('name')}", ""])
        lines.extend(f"- {step}" for step in _string_list(item.get("steps")))
        traps = _string_list(item.get("common_traps"))
        if traps:
            lines.extend(["", "Häufige Fallen:", *[f"- {trap}" for trap in traps]])
        lines.append("")

    lines.extend(["## Übungsplan", ""])
    for item in payload.get("practice_plan", []):
        if not isinstance(item, dict):
            continue
        lines.extend([f"### {item.get('block')}", ""])
        lines.extend(f"- {task}" for task in _string_list(item.get("tasks")))
        criteria = _string_list(item.get("success_criteria"))
        if criteria:
            lines.extend(["", "Erfolgskriterien:", *[f"- {criterion}" for criterion in criteria]])
        lines.append("")

    lines.extend(["## Selbsttest", ""])
    for item in payload.get("self_check", []):
        if not isinstance(item, dict):
            continue
        lines.extend([f"- **{item.get('topic') or 'Frage'}:** {item.get('question')}", f"  - Erwartung: {item.get('expected_answer')}"])
    lines.append("")

    lines.extend(["## Häufige Fehler", ""])
    for item in payload.get("common_mistakes", []):
        if not isinstance(item, dict):
            continue
        lines.extend([f"### {item.get('mistake')}", "", f"Warum: {item.get('why_it_happens')}", "", f"Vermeidung: {item.get('how_to_avoid')}", ""])

    warnings = _string_list(payload.get("warnings"))
    if warnings:
        lines.extend(["## Hinweise", "", *[f"- {warning}" for warning in warnings], ""])
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _source_id_line(value: Any) -> str:
    ids = _string_list(value)
    return f"Quellen: {', '.join(f'[{source_id}]' for source_id in ids)}" if ids else "Quellen: nicht ausreichend belegt"


def _write_no_sources(run_dir: Path, prompt: str, selected_course: dict[str, Any] | None) -> None:
    payload = {
        "created_at": utc_now(),
        "prompt": prompt,
        "status": "needs-more-context",
        "reason": "No indexed source excerpts matched the requested study document.",
        "selected_course": selected_course,
        "suggestions": [
            "scripts/moodle_download_materials.sh --course-limit 3 --download-limit 10",
            "python3 -m uni_agent.orchestrator documents",
        ],
    }
    write_json(run_dir / "request.json", payload)
    lines = [
        "# Study Document Request",
        "",
        "Status: needs more context",
        "",
        payload["reason"],
        "",
        "## Next Options",
        "",
        *[f"- `{suggestion}`" for suggestion in payload["suggestions"]],
    ]
    (run_dir / "study-guide.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(run_dir / "render-result.json", {"ok": False, "reason": "no-source-excerpts", "pdf_attempted": False})


def _write_ambiguous_courses(run_dir: Path, prompt: str, courses: list[dict[str, Any]]) -> None:
    payload = {
        "created_at": utc_now(),
        "prompt": prompt,
        "status": "needs-more-context",
        "reason": "Multiple Moodle courses matched the study document prompt.",
        "course_candidates": courses,
        "suggestions": [
            "Mention one exact course name/code in the study document prompt.",
            *[f"{course.get('title')} -> {course.get('url')}" for course in courses],
        ],
    }
    write_json(run_dir / "request.json", payload)
    write_json(run_dir / "sources.json", {"generated_at": utc_now(), "sources": []})
    write_json(run_dir / "render-result.json", {"ok": False, "reason": "ambiguous-course", "pdf_attempted": False})
    lines = [
        "# Study Document Request",
        "",
        "Status: needs more context",
        "",
        payload["reason"],
        "",
        "## Course Candidates",
        "",
        *[f"- `{course.get('title')}` -> {course.get('url')}" for course in courses],
    ]
    (run_dir / "study-guide.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_validation_failure(run_dir: Path, prompt: str, errors: list[str]) -> None:
    lines = [
        "# Study Document Request",
        "",
        f"Prompt: {prompt}",
        "",
        "Status: failed",
        "",
        "The generated study document was rejected before PDF rendering.",
        "",
        "## Validation Errors",
        "",
        *[f"- {error}" for error in errors],
    ]
    (run_dir / "study-guide.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_generator_failure(run_dir: Path, prompt: str, generator_result: dict[str, Any]) -> None:
    reason = generator_result.get("reason") or "unknown-generator-failure"
    diagnostics = generator_result.get("diagnostics") if isinstance(generator_result.get("diagnostics"), dict) else {}
    lines = [
        "# Study Document Request",
        "",
        f"Prompt: {prompt}",
        "",
        "Status: failed",
        "",
        "The study document generator did not produce a high-quality validated document, and strict generator mode is enabled.",
        "",
        "## Reason",
        "",
        f"- {reason}",
        "",
    ]
    if diagnostics:
        lines.extend(
            [
                "## Diagnostics",
                "",
                *[f"- `{key}`: {value}" for key, value in diagnostics.items()],
                "",
            ]
        )
    lines.extend(
        [
            "## Next Steps",
            "",
            "- Check `generator-transcript.json` in this run directory.",
            "- Increase `STUDY_DOC_TIMEOUT_SECONDS` if the model timed out.",
            "- Set `STUDY_DOC_GENERATOR_REQUIRED=false` to allow the deterministic source-backed fallback.",
        ]
    )
    (run_dir / "study-guide.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(run_dir / "render-result.json", {"ok": False, "reason": reason, "pdf_attempted": False})


def _mark_request(run_dir: Path, status: str, **extra: Any) -> None:
    payload = read_json(run_dir / "request.json", default={})
    payload["status"] = status
    payload["completed_at"] = utc_now()
    payload.update(extra)
    write_json(run_dir / "request.json", payload)


def _write_request_link(request_dir: Path, prompt: str, run_dir: Path, status: str) -> None:
    request_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": utc_now(),
        "prompt": prompt,
        "status": status,
        "action": "study-document",
        "downstream_output": str(run_dir.relative_to(ROOT)),
    }
    write_json(request_dir / "request.json", payload)
    lines = [
        "# Study Buddy Request",
        "",
        f"Prompt: {prompt}",
        "",
        f"Status: {status}",
        "",
        f"Output: `{run_dir.relative_to(ROOT)}`",
    ]
    (request_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _terms_from_prompt(prompt: str) -> set[str]:
    stopwords = {
        "create",
        "generate",
        "study",
        "guide",
        "summary",
        "summarize",
        "topic",
        "notes",
        "pdf",
        "for",
        "the",
        "current",
        "next",
        "quiz",
        "test",
        "exam",
        "exams",
        "prüfung",
        "pruefung",
        "week",
        "weeks",
        "wochen",
        "mach",
        "mir",
        "eine",
        "einen",
        "lernzettel",
        "zusammenfassung",
        "bitte",
        "moodle",
    }
    aliases = {
        "integralrechnung": {"integralrechnung", "integral", "integrals", "definite", "bestimmte"},
        "mathematik": {"math", "mathe", "mathematik", "maes"},
        "dynamik": {"dynamik", "dynamic", "dynamics", "dyn", "dyn2"},
    }
    terms = {
        word.casefold()
        for word in re.findall(r"[a-zA-ZäöüÄÖÜß0-9]{3,}", prompt)
        if word.casefold() not in stopwords
    }
    expanded = set(terms)
    for canonical, values in aliases.items():
        if canonical in terms or any(value in terms for value in values):
            expanded.update(values)
            expanded.add(canonical)
    return expanded


def _truthy(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "on"}


def _resolve_output_language(prompt: str, selected_course: dict[str, Any] | None, env: dict[str, str]) -> str:
    configured = (env.get("STUDY_DOC_LANGUAGE") or "de").strip().casefold()
    if configured in {"de", "en"}:
        return configured
    haystack = f"{prompt} {(selected_course or {}).get('title') or ''}".casefold()
    german_markers = ["für", "prüfung", "zusammenfassung", "dynamik", "mathematik", "anwendungen", "lektoren", "ihre rolle"]
    english_markers = ["english", "summary", "study guide", "assignment"]
    german_score = sum(1 for marker in german_markers if marker in haystack)
    english_score = sum(1 for marker in english_markers if marker in haystack)
    return "de" if german_score >= english_score else "en"


def _write_stable_outputs(payload: dict[str, Any], run_dir: Path, render_result: dict[str, Any]) -> None:
    topic = str(payload.get("topic") or payload.get("title") or "study-guide")
    safe = "DYN2-study-guide" if "dyn2" in topic.casefold() or "dynamik" in topic.casefold() else f"{slugify(topic, 'study-guide')}-study-guide"
    markdown = run_dir / "study-guide.md"
    if markdown.exists():
        shutil.copyfile(markdown, ROOT / "output" / f"{safe}.md")
    target = render_result.get("target")
    if render_result.get("ok") and target:
        pdf = Path(str(target))
        if pdf.exists():
            shutil.copyfile(pdf, ROOT / "output" / f"{safe}.pdf")


def _topic_label(prompt: str) -> str:
    prompt_lower = prompt.casefold()
    if any(term in prompt_lower for term in ["dyn2", "dynamik", "dynamic", "dynamics"]):
        return "DYN2 Anwendungen der Dynamik"
    if "integralrechnung" in prompt_lower:
        return "Integralrechnung"
    if any(term in prompt_lower for term in ["maes", "mathe", "mathematik", "math"]):
        return "Mathematik"
    terms = sorted(_terms_from_prompt(prompt))
    return " ".join(terms[:6]) if terms else "Study Topic"


def _default_title(prompt: str, doc_kind: str) -> str:
    raw_label = _topic_label(prompt)
    label = raw_label if any(char.isupper() for char in raw_label) else raw_label.title()
    if doc_kind == "topic_summary":
        return f"Topic Summary: {label}"
    return f"Study Guide: {label}"


def _normalize_key_concepts(value: Any, source_notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    concepts = value if isinstance(value, list) else []
    return [
        {
            "term": str(item.get("term") or "Concept"),
            "explanation": str(item.get("explanation") or ""),
            "citations": _normalize_citations(item.get("citations"), source_notes),
        }
        for item in concepts
        if isinstance(item, dict)
    ]


def _normalize_sections(value: Any, source_notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections = value if isinstance(value, list) else []
    normalized = []
    for item in sections:
        if not isinstance(item, dict):
            continue
        examples = []
        for example in item.get("worked_examples", []) if isinstance(item.get("worked_examples"), list) else []:
            if not isinstance(example, dict):
                continue
            examples.append(
                {
                    "problem": str(example.get("problem") or ""),
                    "solution": str(example.get("solution") or ""),
                    "citations": _normalize_citations(example.get("citations"), source_notes),
                }
            )
        normalized.append(
            {
                "heading": str(item.get("heading") or "Section"),
                "summary": str(item.get("summary") or ""),
                "details": _string_list(item.get("details")),
                "worked_examples": examples,
                "citations": _normalize_citations(item.get("citations"), source_notes),
            }
        )
    return normalized


def _normalize_checkpoints(value: Any, source_notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checkpoints = value if isinstance(value, list) else []
    return [
        {
            "question": str(item.get("question") or ""),
            "answer": str(item.get("answer") or ""),
            "citations": _normalize_citations(item.get("citations"), source_notes),
        }
        for item in checkpoints
        if isinstance(item, dict)
    ]


def _normalize_pitfalls(value: Any, source_notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pitfalls = value if isinstance(value, list) else []
    return [
        {
            "pitfall": str(item.get("pitfall") or "Pitfall"),
            "correction": str(item.get("correction") or ""),
            "citations": _normalize_citations(item.get("citations"), source_notes),
        }
        for item in pitfalls
        if isinstance(item, dict)
    ]


def _normalize_citations(value: Any, source_notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized = []
    source_by_key = {_citation_key(source): source for source in source_notes}
    for item in value:
        if not isinstance(item, dict):
            continue
        citation = {
            "title": str(item.get("title") or ""),
            "kind": _allowed_kind(item.get("kind")),
            "url": item.get("url"),
            "path": item.get("path"),
            "page": item.get("page") if isinstance(item.get("page"), int) else None,
            "section": item.get("section"),
        }
        normalized.append(source_by_key.get(_citation_key(citation), citation))
    return normalized


def _citation_from_excerpt(excerpt: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(excerpt.get("title") or "source"),
        "kind": _allowed_kind(excerpt.get("kind")),
        "url": excerpt.get("url"),
        "path": excerpt.get("path"),
        "page": excerpt.get("page") if isinstance(excerpt.get("page"), int) else None,
        "section": excerpt.get("section"),
    }


def _allowed_kind(value: Any) -> str:
    kind = str(value or "local_file")
    return kind if kind in {"pdf", "moodle_page", "assignment", "local_file"} else "local_file"


def _citation_key(citation: dict[str, Any]) -> tuple[str, str, int | None]:
    return (
        str(citation.get("title") or "").casefold(),
        str(citation.get("path") or citation.get("url") or "").casefold(),
        citation.get("page") if isinstance(citation.get("page"), int) else None,
    )


def _markdown_sources(citations: Any) -> str:
    items = [item for item in citations if isinstance(item, dict)] if isinstance(citations, list) else []
    if not items:
        return "Sources:\n- Not sufficiently sourced. Do not use as final answer."
    return "Sources:\n" + "\n".join(f"- {_format_citation(item)}" for item in items)


def _format_citation(source: dict[str, Any]) -> str:
    parts = [f"`{source.get('title') or 'Untitled source'}`"]
    if source.get("page") is not None:
        parts.append(f"page {source.get('page')}")
    if source.get("section"):
        parts.append(f"section \"{source.get('section')}\"")
    if source.get("path"):
        parts.append(str(source.get("path")))
    elif source.get("url"):
        parts.append(str(source.get("url")))
    return ", ".join(parts)


def _format_source_map_item(source: dict[str, Any]) -> str:
    parts = [str(source.get("title") or "Untitled source")]
    if source.get("page") is not None:
        parts.append(f"page {source.get('page')}")
    if source.get("section"):
        parts.append(f"section \"{source.get('section')}\"")
    if source.get("role"):
        parts.append(f"role {source.get('role')}")
    if source.get("path"):
        parts.append(str(source.get("path")))
    elif source.get("url"):
        parts.append(str(source.get("url")))
    return ", ".join(parts)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _trim_text(text: str, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "..."


def _first_sentences(text: str, count: int) -> str:
    normalized = _trim_text(text, 1800)
    parts = re.split(r"(?<=[.!?])\s+", normalized)
    return _trim_text(" ".join(parts[:count]), 1200)


def _parse_json_response(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            return None
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None
