from __future__ import annotations

import json
import re

from backend.core.i18n import tr


def build_speech_text(display_text: str, *, form_of_address: str = "senhor", language: str | None = None) -> str:
    """Create a concise, safe-to-speak rendering distinct from visual output."""

    text = display_text.strip()
    if not text:
        return ""
    had_code = bool(re.search(r"```[\s\S]*?```", text))
    had_json = _looks_like_json(text)
    if had_json:
        text = ""
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`\n]{1,300}`", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"^\s*\|.*\|\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-:| ]{3,}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)]\([^\)]+\)", r"\1", text)
    text = re.sub(r"[*_~>]", "", text)
    text = re.sub(r"\n{2,}", ". ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if had_code or had_json:
        title = tr("senhor", "sir", language=language) if form_of_address in {"senhor", "sir"} else form_of_address
        notice = tr(
            f"Deixei {'o código' if had_code else 'os dados estruturados'} na tela, {title}.",
            f"I left {'the code' if had_code else 'the structured data'} on screen, {title}.",
            language=language,
        )
        return f"{text}. {notice}" if text and len(text) < 400 else notice
    return text[:900]


def _looks_like_json(value: str) -> bool:
    stripped = value.strip()
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return False
    try:
        json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return True
