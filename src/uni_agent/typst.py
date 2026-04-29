from __future__ import annotations

import shutil
import subprocess
import re
from pathlib import Path
from typing import Any

from .storage import ROOT


TEMPLATE_PATH = ROOT / "templates" / "typst" / "study-guide.typ"


def write_study_guide_typst(payload: dict[str, Any], target: Path, *, doc_kind: str = "generic_document") -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    if doc_kind == "cheat_sheet":
        target.write_text(_render_cheat_sheet(payload), encoding="utf-8")
    elif doc_kind == "exam_study_guide":
        target.write_text(template + "\n" + _render_exam_study_guide(payload), encoding="utf-8")
    else:
        target.write_text(template + "\n" + _render_document(payload), encoding="utf-8")
    return target


def write_exam_study_guide_typst(payload: dict[str, Any], target: Path) -> Path:
    return write_study_guide_typst(payload, target, doc_kind="exam_study_guide")


def write_generic_document_typst(payload: dict[str, Any], target: Path) -> Path:
    return write_study_guide_typst(payload, target, doc_kind="generic_document")


def compile_typst_pdf(source: Path, target: Path) -> dict[str, Any]:
    typst = shutil.which("typst")
    if not typst:
        return {
            "ok": False,
            "reason": "typst-not-found",
            "hint": "Install Typst and rerun with the same generated .typ file.",
            "source": str(source),
            "target": str(target),
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    command = [typst, "compile", str(source), str(target)]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    return {
        "ok": completed.returncode == 0,
        "reason": "compiled" if completed.returncode == 0 else "typst-compile-failed",
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "source": str(source),
        "target": str(target),
    }


def _render_cheat_sheet(payload: dict[str, Any]) -> str:
    title = payload.get("title") or "Formelsammlung"
    topic = payload.get("topic") or ""
    generated = payload.get("generated_at") or ""
    sections = payload.get("sections") if isinstance(payload.get("sections"), list) else []
    concepts = payload.get("key_concepts") if isinstance(payload.get("key_concepts"), list) else []

    blocks: list[str] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = section.get("heading") or "Abschnitt"
        entries = []
        if section.get("summary"):
            entries.append(str(section.get("summary")))
        entries.extend(_as_strings(section.get("details")))
        examples = section.get("worked_examples") if isinstance(section.get("worked_examples"), list) else []
        for example in examples:
            if isinstance(example, dict) and (example.get("problem") or example.get("solution")):
                entries.append(f"{example.get('problem') or ''}: {example.get('solution') or ''}".strip(": "))
        blocks.append(_cheat_block(str(heading), entries))

    if not blocks:
        for concept in concepts:
            if isinstance(concept, dict):
                blocks.append(_cheat_block(str(concept.get("term") or "Konzept"), [str(concept.get("explanation") or "")]))

    return "\n".join(
        [
            '#set page(paper: "a4", flipped: true, margin: 5mm)',
            '#set text(font: "Libertinus Sans", size: 6.45pt)',
            '#set par(leading: 0.23em, spacing: 0.16em)',
            '#let box(title, body) = block(width: 100%, inset: 2.2pt, radius: 2pt, stroke: 0.25pt + luma(205), fill: luma(250))[',
            '  #text(weight: "bold", size: 7.2pt, fill: rgb("#17324d"))[#title]',
            '  #linebreak()',
            '  #v(0.15em)',
            '  #body',
            ']',
            '#align(center)[',
            f'  #text(weight: "bold", size: 10pt)[{_content(title)}]',
            '  #h(1em)',
            f'  #text(size: 6.4pt, fill: rgb("#555555"))[{_content(topic)} | {_content(generated)}]',
            ']',
            '#v(-0.2em)',
            '#grid(columns: (1fr, 1fr, 1fr, 1fr), gutter: 3.5mm, row-gutter: 2.2pt,',
            ",\n".join(blocks),
            ')',
            '',
        ]
    )


def _cheat_block(title: str, entries: list[str]) -> str:
    cleaned = [entry.strip() for entry in entries if entry and entry.strip()]
    if not cleaned:
        cleaned = ["Wichtigste Definitionen, Formeln und Einsatzbedingungen prüfen."]
    body = "\n".join(f"- {_content(entry)}" for entry in cleaned)
    return f"box([{_content(title)}], [\n{body}\n])"


def _render_document(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    title = payload.get("title") or "Study Guide"
    subtitle = payload.get("subtitle")
    course = payload.get("course") or "Unspecified course"
    topic = payload.get("topic") or "Unspecified topic"
    generated = payload.get("generated_at") or ""

    lines.extend(
        [
            f"#align(center)[#text(size: 22pt, weight: \"bold\", fill: rgb(\"#163f4a\"))[{_content(title)}]]",
        ]
    )
    if subtitle:
        lines.append(f"#align(center)[#text(size: 11pt, fill: rgb(\"#555555\"))[{_content(subtitle)}]]")
    lines.extend(
        [
            "#v(6pt)",
            "#align(center)["
            '#text(size: 8.5pt, fill: rgb("#555555"))[Course: ] '
            f"#text(size: 8.5pt)[{_content(course)}] #h(12pt) "
            '#text(size: 8.5pt, fill: rgb("#555555"))[Topic: ] '
            f"#text(size: 8.5pt)[{_content(topic)}] #h(12pt) "
            '#text(size: 8.5pt, fill: rgb("#555555"))[Generated: ] '
            f"#text(size: 8.5pt)[{_content(generated)}]"
            "]",
            "#v(12pt)",
        ]
    )

    objectives = _as_strings(payload.get("learning_objectives"))
    if objectives:
        lines.append("#callout([Learning objectives], [")
        lines.append(_typst_list(objectives))
        lines.append("])")
        lines.append("")

    concepts = payload.get("key_concepts") if isinstance(payload.get("key_concepts"), list) else []
    if concepts:
        lines.append("#heading(level: 1)[Key Concepts]")
        for concept in concepts:
            if not isinstance(concept, dict):
                continue
            term = concept.get("term") or "Concept"
            explanation = concept.get("explanation") or ""
            lines.append(f"#text(weight: \"bold\")[{_content(term)}]")
            lines.append("")
            lines.append(_paragraph(explanation))
            lines.append("")

    sections = payload.get("sections") if isinstance(payload.get("sections"), list) else []
    if sections:
        lines.append("#heading(level: 1)[Core Explanation]")
        for section in sections:
            if not isinstance(section, dict):
                continue
            lines.append(f"#heading(level: 2)[{_content(section.get('heading') or 'Section')}]")
            lines.append(_paragraph(section.get("summary") or ""))
            details = _as_strings(section.get("details"))
            if details:
                lines.append(_typst_list(details))
            examples = section.get("worked_examples") if isinstance(section.get("worked_examples"), list) else []
            for example in examples:
                if not isinstance(example, dict):
                    continue
                lines.append("#callout([Worked example], [")
                lines.append(f"#text(weight: \"bold\")[Problem:] {_content(example.get('problem') or '')}")
                lines.append("")
                lines.append(f"#text(weight: \"bold\")[Solution:] {_content(example.get('solution') or '')}")
                lines.append("")
                lines.append("])")
            lines.append("")

    checkpoints = payload.get("practice_checkpoints") if isinstance(payload.get("practice_checkpoints"), list) else []
    if checkpoints:
        lines.append("#heading(level: 1)[Practice Checkpoints]")
        for index, checkpoint in enumerate(checkpoints, start=1):
            if not isinstance(checkpoint, dict):
                continue
            lines.append("#callout([" + _content(f"Checkpoint {index}") + "], [")
            lines.append(f"#text(weight: \"bold\")[Question:] {_content(checkpoint.get('question') or '')}")
            lines.append("")
            lines.append(f"#text(weight: \"bold\")[Answer:] {_content(checkpoint.get('answer') or '')}")
            lines.append("")
            lines.append("])")
            lines.append("")

    pitfalls = payload.get("common_pitfalls") if isinstance(payload.get("common_pitfalls"), list) else []
    if pitfalls:
        lines.append("#heading(level: 1)[Common Pitfalls]")
        for pitfall in pitfalls:
            if not isinstance(pitfall, dict):
                continue
            lines.append(f"#text(weight: \"bold\")[{_content(pitfall.get('pitfall') or 'Pitfall')}]")
            lines.append("")
            lines.append(_paragraph(pitfall.get("correction") or ""))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_exam_study_guide(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    title = payload.get("title") or "Study Guide"
    course = payload.get("course") or "Unspecified course"
    topic = payload.get("topic") or "Unspecified topic"
    generated = payload.get("generated_at") or ""
    language = payload.get("language") or ""
    focus = payload.get("exam_focus") if isinstance(payload.get("exam_focus"), dict) else {}

    lines.extend(
        [
            f"#align(center)[#text(size: 22pt, weight: \"bold\", fill: rgb(\"#163f4a\"))[{_content(title)}]]",
            "#v(4pt)",
            "#align(center)["
            '#text(size: 8.5pt, fill: rgb("#555555"))[Kurs: ] '
            f"#text(size: 8.5pt)[{_content(course)}] #h(12pt) "
            '#text(size: 8.5pt, fill: rgb("#555555"))[Thema: ] '
            f"#text(size: 8.5pt)[{_content(topic)}] #h(12pt) "
            '#text(size: 8.5pt, fill: rgb("#555555"))[Sprache: ] '
            f"#text(size: 8.5pt)[{_content(language)}] #h(12pt) "
            '#text(size: 8.5pt, fill: rgb("#555555"))[Generiert: ] '
            f"#text(size: 8.5pt)[{_content(generated)}]"
            "]",
            "#v(12pt)",
        ]
    )

    lines.append("#callout([Was du für die Prüfung können solltest], [")
    lines.append(_paragraph(focus.get("exam_scope_summary") or ""))
    priorities = _as_strings(focus.get("highest_priority_topics"))
    if priorities:
        lines.append("#text(weight: \"bold\")[Priorität:]")
        lines.append(_typst_list(priorities))
    how_to = _as_strings(focus.get("how_to_use_this_guide"))
    if how_to:
        lines.append("#text(weight: \"bold\")[So nutzt du diesen Guide:]")
        lines.append(_typst_list(how_to))
    lines.append("])")
    lines.append("")

    _render_learning_path(lines, payload.get("learning_path"))
    _render_topic_map(lines, payload.get("topic_map"))
    _render_formula_cards(lines, payload.get("formula_cards"))
    _render_problem_methods(lines, payload.get("problem_solving_methods"))
    _render_practice_plan(lines, payload.get("practice_plan"))
    _render_self_check(lines, payload.get("self_check"))
    _render_common_mistakes(lines, payload.get("common_mistakes"))

    warnings = _as_strings(payload.get("warnings"))
    if warnings:
        lines.append("#heading(level: 1)[Hinweise]")
        lines.append(_typst_list(warnings))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_learning_path(lines: list[str], value: Any) -> None:
    items = value if isinstance(value, list) else []
    if not items:
        return
    lines.append("#heading(level: 1)[Lernplan]")
    for item in items:
        if not isinstance(item, dict):
            continue
        lines.append(f"#heading(level: 2)[{_content(item.get('phase') or '')} #text(size: 9pt, fill: rgb(\"#555555\"))[{_content(item.get('duration') or '')}]]")
        lines.append(_paragraph(item.get("goal") or ""))
        lines.append(_typst_list(_as_strings(item.get("tasks"))))
        if item.get("output"):
            lines.append(f"#text(weight: \"bold\")[Ergebnis:] {_content(item.get('output'))}")
        lines.append("")


def _render_topic_map(lines: list[str], value: Any) -> None:
    items = value if isinstance(value, list) else []
    if not items:
        return
    lines.append("#heading(level: 1)[Themenübersicht]")
    for item in items:
        if not isinstance(item, dict):
            continue
        lines.append(f"#heading(level: 2)[{_content(item.get('name') or '')} #text(size: 9pt, fill: rgb(\"#555555\"))[{_content(item.get('priority') or '')}]]")
        lines.append(_paragraph(item.get("what_it_is") or ""))
        lines.append("#text(weight: \"bold\")[Du musst können:]")
        lines.append(_typst_list(_as_strings(item.get("what_you_must_be_able_to_do"))))
        tasks = _as_strings(item.get("typical_exam_tasks"))
        if tasks:
            lines.append("#text(weight: \"bold\")[Typische Prüfungsaufgaben:]")
            lines.append(_typst_list(tasks))
        checks = _as_strings(item.get("learning_checks"))
        if checks:
            lines.append("#text(weight: \"bold\")[Lernchecks:]")
            lines.append(_typst_list(checks))
        lines.append("")


def _render_formula_cards(lines: list[str], value: Any) -> None:
    items = value if isinstance(value, list) else []
    if not items:
        return
    lines.append("#heading(level: 1)[Formeln und wann du sie verwendest]")
    for item in items:
        if not isinstance(item, dict):
            continue
        lines.append("#callout([" + _content(item.get("name") or "Formel") + "], [")
        lines.append("#text(weight: \"bold\")[Formel:]")
        lines.append(_formula_block(item.get("formula") or ""))
        variables = _as_strings(item.get("variables"))
        if variables:
            lines.append(f"#text(weight: \"bold\")[Variablen:] {_content(', '.join(variables))}")
        lines.append(f"#text(weight: \"bold\")[Wann verwenden:] {_content(item.get('when_to_use') or '')}")
        if item.get("warning"):
            lines.append(f"#text(weight: \"bold\")[Achtung:] {_content(item.get('warning'))}")
        lines.append("])")
        lines.append("")


def _render_problem_methods(lines: list[str], value: Any) -> None:
    items = value if isinstance(value, list) else []
    if not items:
        return
    lines.append("#heading(level: 1)[Aufgaben-Strategien]")
    for item in items:
        if not isinstance(item, dict):
            continue
        lines.append(f"#heading(level: 2)[{_content(item.get('name') or '')}]")
        applies = _as_strings(item.get("applies_to"))
        if applies:
            lines.append(f"#text(weight: \"bold\")[Gilt für:] {_content(', '.join(applies))}")
        lines.append(_typst_list(_as_strings(item.get("steps"))))
        traps = _as_strings(item.get("common_traps"))
        if traps:
            lines.append("#text(weight: \"bold\")[Häufige Fallen:]")
            lines.append(_typst_list(traps))
        lines.append("")


def _render_practice_plan(lines: list[str], value: Any) -> None:
    items = value if isinstance(value, list) else []
    if not items:
        return
    lines.append("#heading(level: 1)[Übungsplan]")
    for item in items:
        if not isinstance(item, dict):
            continue
        lines.append(f"#heading(level: 2)[{_content(item.get('block') or '')}]")
        lines.append(_typst_list(_as_strings(item.get("tasks"))))
        criteria = _as_strings(item.get("success_criteria"))
        if criteria:
            lines.append("#text(weight: \"bold\")[Erfolgskriterien:]")
            lines.append(_typst_list(criteria))
        lines.append("")


def _render_self_check(lines: list[str], value: Any) -> None:
    items = value if isinstance(value, list) else []
    if not items:
        return
    lines.append("#heading(level: 1)[Selbsttest]")
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        lines.append("#callout([" + _content(f"Frage {index}: {item.get('topic') or ''}") + "], [")
        lines.append(f"#text(weight: \"bold\")[Frage:] {_content(item.get('question') or '')}")
        lines.append("")
        lines.append(f"#text(weight: \"bold\")[Erwartung:] {_content(item.get('expected_answer') or '')}")
        lines.append("")
        lines.append("])")
        lines.append("")


def _render_common_mistakes(lines: list[str], value: Any) -> None:
    items = value if isinstance(value, list) else []
    if not items:
        return
    lines.append("#heading(level: 1)[Häufige Fehler]")
    for item in items:
        if not isinstance(item, dict):
            continue
        lines.append(f"#heading(level: 2)[{_content(item.get('mistake') or '')}]")
        lines.append(f"#text(weight: \"bold\")[Warum:] {_content(item.get('why_it_happens') or '')}")
        lines.append("")
        lines.append(f"#text(weight: \"bold\")[Vermeidung:] {_content(item.get('how_to_avoid') or '')}")
        lines.append("")
        lines.append("")


def _paragraph(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return f"[{_content(text)}]"


def _formula_block(value: Any) -> str:
    formulas = _split_formulas(str(value or ""))
    if not formulas:
        return _paragraph("Not sufficiently sourced. Do not use as final answer.")
    rendered = "\n".join(f"  ${_math_content(formula)}$" for formula in formulas)
    return f"#formula-box[\n{rendered}\n]"


def _split_formulas(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s*;\s*", value.strip()) if part.strip()]


def _math_content(value: str) -> str:
    text = value.strip()
    replacements = {
        "lambda": "lambda",
        "Lambda": "Lambda",
        "omega": "omega",
        "Omega": "Omega",
        "phi": "phi",
        "Phi": "Phi",
        "rho": "rho",
        "Summe": "sum",
        "summe": "sum",
    }
    for source, target in replacements.items():
        text = re.sub(rf"\b{re.escape(source)}\b", target, text)
    text = re.sub(r"\bd([A-Za-z])\b", r"d \1", text)
    text = re.sub(r"\bd([A-Z])", r"d \1", text)
    text = re.sub(r"\b(omega|[A-Za-z]_[A-Za-z0-9]+)\s+x\s+(?=([A-Za-z]_[A-Za-z0-9]+|\())", r"\1 times ", text)
    text = re.sub(r"_([A-Za-z0-9]+)", _math_subscript, text)
    text = text.replace("'", "'")
    return text.replace("$", r"\$")


def _math_subscript(match: re.Match[str]) -> str:
    value = match.group(1)
    if value in {"alpha", "beta", "gamma", "delta", "epsilon", "lambda", "mu", "nu", "omega", "Omega", "phi", "Phi", "rho", "theta"}:
        return f"_({value})"
    if len(value) > 1 and not value.isdigit():
        return f'_(\"{value}\")'
    return f"_({value})"


def _typst_list(items: list[str]) -> str:
    if not items:
        return ""
    rendered = ",\n".join(f"  [{_content(item)}]" for item in items)
    return f"#list(\n{rendered},\n)"


def _source_line(citations: Any) -> str:
    citation_items = [item for item in citations if isinstance(item, dict)] if isinstance(citations, list) else []
    if not citation_items:
        return "#text(size: 8pt, fill: rgb(\"#8a1f1f\"))[Sources: Not sufficiently sourced. Do not use as final answer.]"
    joined = "; ".join(_format_citation(item) for item in citation_items)
    return f"#text(size: 8pt, fill: rgb(\"#555555\"))[Sources: {_content(joined)}]"


def _source_ids_line(value: Any) -> str:
    ids = _as_strings(value)
    if not ids:
        return "#text(size: 8pt, fill: rgb(\"#8a1f1f\"))[Quellen: nicht ausreichend belegt]"
    joined = ", ".join(f"[{source_id}]" for source_id in ids)
    return f"#text(size: 8pt, fill: rgb(\"#555555\"))[Quellen: {_content(joined)}]"


def _format_citation(source: dict[str, Any]) -> str:
    parts = [str(source.get("title") or "Untitled source")]
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


def _as_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _content(value: Any) -> str:
    text = str(value or "")
    replacements = {
        "\\": "\\\\",
        "[": "\\[",
        "]": "\\]",
        "#": "\\#",
        "$": "\\$",
        "%": "\\%",
        "&": "\\&",
        "_": "\\_",
        "{": "\\{",
        "}": "\\}",
        "^": "\\^",
    }
    return "".join(replacements.get(char, char) for char in text)
