from __future__ import annotations

import re
from typing import Any


def rank_courses(prompt: str, courses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    terms = _terms_from_prompt(prompt)
    prompt_lower = prompt.casefold()
    explicit_course_ids = set(re.findall(r"\b\d{5,6}\b", prompt_lower))
    wants_labor = bool(re.search(r"\b(labor|lab|etlb)\b", prompt_lower))
    wants_et1 = _prompt_requests_numbered_course(prompt_lower, "elektrotechnik", "1") or bool(re.search(r"\bet\s*1\b|\bet1\b", prompt_lower))
    wants_et2 = _prompt_requests_numbered_course(prompt_lower, "elektrotechnik", "2") or bool(re.search(r"\bet\s*2\b|\bet2\b", prompt_lower))
    wants_bmr_year_1 = bool(re.search(r"\bbmr[-\s]?vz[-\s]?1\b", prompt_lower))
    wants_bmr_year_2 = bool(re.search(r"\bbmr[-\s]?vz[-\s]?2\b", prompt_lower))
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
        course_id = str(course.get("id") or "")
        if course_id and course_id in explicit_course_ids:
            score += 80
        for canonical, values in aliases.items():
            if canonical in terms or any(value in prompt_lower for value in values):
                if any(value in haystack for value in values):
                    score += 8
        is_labor_course = bool(re.search(r"\b(etlb|labor|lab)\b", haystack))
        if is_labor_course and not wants_labor:
            score -= 8
        if wants_labor and is_labor_course:
            score += 10
        if wants_et1 or wants_bmr_year_1:
            if re.search(r"\bet1\b|elektrotechnik\s+1\b|bmr-vz-1\b", haystack):
                score += 30
            if re.search(r"\bet2\b|elektrotechnik\s+2\b|bmr-vz-2\b", haystack):
                score -= 15
        if wants_et2 or wants_bmr_year_2:
            if re.search(r"\bet2\b|elektrotechnik\s+2\b|bmr-vz-2\b", haystack):
                score += 30
            if re.search(r"\bet1\b|elektrotechnik\s+1\b|bmr-vz-1\b", haystack):
                score -= 15
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


def _prompt_requests_numbered_course(prompt_lower: str, subject: str, number: str) -> bool:
    subject_pattern = re.escape(subject.casefold())
    return bool(
        re.search(rf"\b{subject_pattern}\s+{re.escape(number)}\b", prompt_lower)
        or re.search(rf"\b{re.escape(number)}\s+{subject_pattern}\b", prompt_lower)
    )


def _terms_from_prompt(prompt: str) -> set[str]:
    words = re.findall(r"[a-zA-ZäöüÄÖÜß0-9]+", prompt.casefold())
    stop = {
        "the",
        "and",
        "for",
        "mit",
        "und",
        "der",
        "die",
        "das",
        "eine",
        "einen",
        "ein",
        "für",
        "fur",
        "von",
        "zu",
        "als",
        "pdf",
        "bitte",
        "erstelle",
        "generate",
        "make",
    }
    return {word for word in words if len(word) >= 2 and word not in stop}
