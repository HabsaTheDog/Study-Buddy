from __future__ import annotations

import re


GREEK_SYMBOLS = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "Delta": "Δ",
    "epsilon": "ε",
    "lambda": "λ",
    "Lambda": "Λ",
    "mu": "μ",
    "nu": "ν",
    "omega": "ω",
    "Omega": "Ω",
    "phi": "φ",
    "Phi": "Φ",
    "rho": "ρ",
    "theta": "θ",
    "Theta": "Θ",
    "pi": "π",
}

SUPERSCRIPTS = str.maketrans("0123456789+-=()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾")
SUBSCRIPTS = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")


def display_math_text(value: object) -> str:
    """Convert common ASCII math notation into readable Unicode text."""

    text = str(value or "")
    text = text.replace("<=", "≤").replace(">=", "≥").replace("!=", "≠")
    text = re.sub(r"\bsqrt\s*\(", "√(", text)
    text = re.sub(r"\bIntegral\b|\bintegral\b", "∫", text)
    text = re.sub(r"\bSumme\b|\bsumme\b|\bsum\b", "Σ", text)
    text = re.sub(r"\bDelta\b", "Δ", text)
    text = re.sub(r"\bdot\b", "·", text)
    text = re.sub(r"\b(omega|Omega|alpha|beta|gamma|delta|Delta|lambda|Lambda|mu|nu|phi|Phi|rho|theta|Theta|pi)_([A-Za-z0-9]+)\b", _display_greek_subscript, text)
    text = re.sub(r"\b(omega|Omega|alpha|beta|gamma|delta|Delta|lambda|Lambda|mu|nu|phi|Phi|rho|theta|Theta|pi)(\d*)\b", _display_greek, text)
    text = re.sub(r"\b([ωΩαβγδλΛμνφΦρθΘ])\s+x\s+", r"\1 × ", text)
    text = re.sub(r"\b([A-Za-z])0([A-Za-z]?)\b", lambda m: f"{m.group(1)}₀{m.group(2)}", text)
    text = re.sub(r"\b([A-Za-z])2\b", lambda m: f"{m.group(1)}²", text)
    text = re.sub(r"\b([A-Za-z])3\b", lambda m: f"{m.group(1)}³", text)
    text = re.sub(r"\^([0-9]+)", lambda m: m.group(1).translate(SUPERSCRIPTS), text)
    text = re.sub(r"_([0-9]+)", lambda m: m.group(1).translate(SUBSCRIPTS), text)
    return text


def markdown_formula(value: object) -> str:
    formulas = [part.strip() for part in re.split(r"\s*;\s*", str(value or "").strip()) if part.strip()]
    if not formulas:
        return ""
    return "\n".join(f"$${display_math_text(formula)}$$" for formula in formulas)


def typst_math_content(value: object) -> str:
    """Normalize common Study Buddy ASCII formulas into Typst math syntax."""

    text = str(value or "").strip()
    text = text.translate(str.maketrans({"²": "^2", "³": "^3", "√": "sqrt"}))
    replacements = {
        "Summe": "sum",
        "summe": "sum",
        "Integral": "integral",
        "integral": "integral",
        "Delta": "Delta",
    }
    for source, target in replacements.items():
        text = re.sub(rf"\b{re.escape(source)}\b", target, text)
    text = re.sub(r"\b(omega|Omega|alpha|beta|gamma|delta|lambda|Lambda|mu|nu|phi|Phi|rho|theta|Theta)(0)(2)\b", r"\1_0^2", text)
    text = re.sub(r"\b(omega|Omega|alpha|beta|gamma|delta|lambda|Lambda|mu|nu|phi|Phi|rho|theta|Theta)(0)\b", r"\1_0", text)
    text = re.sub(r"\b(omega|Omega|alpha|beta|gamma|delta|lambda|Lambda|mu|nu|phi|Phi|rho|theta|Theta)(2)\b", r"\1^2", text)
    text = re.sub(r"\b([va])0([xyz])\b", r"\1_(0 \2)", text)
    text = re.sub(r"\b([va])([xyz])\b", r"\1_\2", text)
    text = re.sub(r"\b([A-Za-z])0([A-Za-z]?)\b", r"\1_0\2", text)
    text = re.sub(r"\b([A-Za-z])2\b", r"\1^2", text)
    text = re.sub(r"\b([A-Za-z])3\b", r"\1^3", text)
    text = re.sub(r"\bconst\b", '"const"', text)
    text = re.sub(r"\bd(omega|Omega|alpha|beta|gamma|delta|lambda|mu|nu|phi|rho|theta)\b", r"d \1", text)
    text = re.sub(r"\bd([A-Za-z])\b", r"d \1", text)
    text = re.sub(r"\bd([A-Z])", r"d \1", text)
    text = re.sub(
        r"\b(omega|Omega|alpha|beta|gamma|delta|lambda|mu|nu|phi|rho|theta|[A-Za-z](?:_[A-Za-z0-9]+)?)\s+x\s+(?=([A-Za-z](?:_[A-Za-z0-9]+)?|\())",
        r"\1 times ",
        text,
    )
    text = re.sub(r"_([A-Za-z0-9]+)", _typst_subscript, text)
    return text.replace("$", r"\$")


def _display_greek(match: re.Match[str]) -> str:
    symbol = GREEK_SYMBOLS.get(match.group(1), match.group(1))
    suffix = match.group(2)
    if not suffix:
        return symbol
    if suffix == "0":
        return f"{symbol}₀"
    if suffix == "02":
        return f"{symbol}₀²"
    if suffix == "2":
        return f"{symbol}²"
    return symbol + suffix.translate(SUBSCRIPTS)


def _display_greek_subscript(match: re.Match[str]) -> str:
    symbol = GREEK_SYMBOLS.get(match.group(1), match.group(1))
    suffix = match.group(2)
    if suffix.isdigit():
        return symbol + suffix.translate(SUBSCRIPTS)
    return f"{symbol}_{suffix}"


def _typst_subscript(match: re.Match[str]) -> str:
    value = match.group(1)
    if value in {"alpha", "beta", "gamma", "delta", "epsilon", "lambda", "mu", "nu", "omega", "Omega", "phi", "Phi", "rho", "theta"}:
        return f"_({value})"
    if len(value) > 1 and not value.isdigit():
        return f'_(\"{value}\")'
    return f"_({value})"
