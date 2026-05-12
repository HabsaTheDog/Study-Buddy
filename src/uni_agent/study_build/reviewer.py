from __future__ import annotations

from pathlib import Path
from typing import Any

from ..storage import write_json
from .contracts import DocumentDraft, ResourceBundle, ReviewReport, to_jsonable
from .model_runner import run_optional_model


def review_document(
    *,
    bundle: ResourceBundle,
    draft: DocumentDraft,
    render_result: dict[str, Any],
    run_dir: Path,
    cycle: int,
) -> ReviewReport:
    report = _deterministic_review(bundle=bundle, draft=draft, render_result=render_result)
    packet = {
        "task": "review_study_build_output",
        "role": "orchestrator_reviewer",
        "rules": [
            "Check whether the document satisfies the user's request.",
            "Reject quiz-derived content without explicit quiz permission.",
            "Return repair instructions for the builder.",
        ],
        "bundle": to_jsonable(bundle),
        "draft": to_jsonable(draft),
        "deterministic_review": to_jsonable(report),
    }
    model_result = run_optional_model(
        command_env="STUDY_BUILD_REVIEWER_COMMAND",
        packet=packet,
        packet_path=run_dir / "artifacts" / "reviewer" / f"packet.v{cycle}.json",
        response_path=run_dir / "artifacts" / "reviewer" / f"response.v{cycle}.json",
        transcript_path=run_dir / "artifacts" / "reviewer" / f"transcript.v{cycle}.json",
    )
    if model_result.get("ok") and isinstance(model_result.get("parsed"), dict):
        report = _merge_model_review(report, model_result["parsed"])
    write_json(run_dir / "artifacts" / "reviewer" / f"review.v{cycle}.json", to_jsonable(report))
    _write_review_markdown(run_dir / "REVIEW.md", report)
    return report


def _deterministic_review(*, bundle: ResourceBundle, draft: DocumentDraft, render_result: dict[str, Any]) -> ReviewReport:
    issues: list[dict[str, Any]] = []
    instructions: list[str] = []
    if not draft.sections:
        issues.append({"severity": "error", "code": "sections-missing", "message": "No theory sections were generated."})
        instructions.append("Create theory sections from the selected source chunks.")
    if "quiz_questions" in bundle.intent.required_sections and not draft.quiz_questions:
        issues.append({"severity": "error", "code": "quiz-questions-missing", "message": "User requested quiz-like questions."})
        instructions.append("Add quiz-like self-check questions from the theory sources.")
    if bundle.intent.wants_solutions_at_end and draft.quiz_questions and draft.layout.quiz_solutions_position != "end":
        issues.append({"severity": "error", "code": "solutions-not-at-end", "message": "Solutions must be at the end."})
        instructions.append("Move all quiz answers and explanations to the final section.")
    if not draft.source_map:
        issues.append({"severity": "error", "code": "sources-missing", "message": "No source map was attached."})
        instructions.append("Attach source_map and source_ids to sections.")
    uncited = [section.heading for section in draft.sections if not section.source_ids]
    if uncited:
        issues.append({"severity": "error", "code": "uncited-sections", "message": f"Sections without sources: {', '.join(uncited[:5])}"})
        instructions.append("Add valid source_ids to every theory section.")
    if bundle.intent.wants_quiz_style and not bundle.quiz_permission.get("allowed"):
        if any(source.get("role") == "quiz_style_source" for source in draft.source_map):
            issues.append({"severity": "error", "code": "quiz-source-without-permission", "message": "Quiz source used without explicit permission."})
            instructions.append("Remove quiz sources or request explicit user authorization.")
    if render_result.get("pdf_attempted") and not render_result.get("ok"):
        issues.append({"severity": "error", "code": "pdf-render-failed", "message": str(render_result.get("stderr") or render_result.get("reason"))})
        instructions.append("Repair Typst/renderer output so PDF compilation succeeds.")
    safety = {
        "quiz_permission_status": bundle.quiz_permission.get("status"),
        "quiz_opened": False,
        "final_submission_allowed": False,
    }
    return ReviewReport(
        passed=not any(issue.get("severity") == "error" for issue in issues),
        issues=issues,
        repair_instructions=instructions,
        requirements_trace=bundle.coverage_matrix,
        safety=safety,
    )


def _merge_model_review(base: ReviewReport, payload: dict[str, Any]) -> ReviewReport:
    model_issues = payload.get("issues") if isinstance(payload.get("issues"), list) else []
    model_instructions = payload.get("repair_instructions") if isinstance(payload.get("repair_instructions"), list) else []
    issues = [*base.issues, *[item for item in model_issues if isinstance(item, dict)]]
    instructions = [*base.repair_instructions, *[str(item) for item in model_instructions if str(item).strip()]]
    passed = base.passed and bool(payload.get("passed", True)) and not any(issue.get("severity") == "error" for issue in issues)
    return ReviewReport(passed=passed, issues=issues, repair_instructions=instructions, requirements_trace=base.requirements_trace, safety=base.safety)


def repair_request_from_review(report: ReviewReport) -> dict[str, Any]:
    return {
        "issues": report.issues,
        "instructions": report.repair_instructions,
    }


def _write_review_markdown(path: Path, report: ReviewReport) -> None:
    lines = [f"# Review", "", f"Passed: `{str(report.passed).lower()}`", "", "## Safety", ""]
    for key, value in report.safety.items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Issues", ""])
    if not report.issues:
        lines.append("- Keine blockierenden Probleme gefunden.")
    else:
        for issue in report.issues:
            lines.append(f"- `{issue.get('severity')}` `{issue.get('code')}`: {issue.get('message')}")
    if report.repair_instructions:
        lines.extend(["", "## Repair Instructions", ""])
        for item in report.repair_instructions:
            lines.append(f"- {item}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
