"""OCR execution policy shared by package-page and table-region readers.

This module selects only installed languages and keeps engine availability
separate from semantic trust.  A single engine may recover text for review,
but it never upgrades evidence to cross-checked status.
"""
from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path


CACHE_SCHEMA = "v6_language_engine_evidence"
_CJK = re.compile(r"[\u3400-\u9fff]")
_TRADITIONAL_HINT = re.compile(r"[臺灣體與為圖號焊盤鋼網檢驗證]")


def cache_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Community" / "FootprintStudio" / "ocr_cache"
    return Path.home() / ".cache" / "Community" / "FootprintStudio" / "ocr_cache"


@lru_cache(maxsize=8)
def installed_languages(executable: str) -> frozenset[str]:
    try:
        run = subprocess.run(
            [executable, "--list-langs"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    if run.returncode:
        return frozenset()
    return frozenset(
        line.strip() for line in run.stdout.splitlines()
        if re.fullmatch(r"[A-Za-z0-9_]+", line.strip())
        and line.strip().lower() != "list"
    )


def select_languages(page_text: str, second_engine_text: str, installed: set[str] | frozenset[str]) -> str | None:
    """Select an installed language set from page evidence, never assumptions."""
    if not installed:
        return None
    combined = f"{page_text}\n{second_engine_text}"
    selected: list[str] = []
    if "eng" in installed:
        selected.append("eng")
    if _CJK.search(combined):
        preferred = "chi_tra" if _TRADITIONAL_HINT.search(combined) else "chi_sim"
        fallback = "chi_sim" if preferred == "chi_tra" else "chi_tra"
        if preferred in installed:
            selected.append(preferred)
        elif fallback in installed:
            selected.append(fallback)
    if not selected:
        for fallback in ("eng", "chi_sim", "chi_tra"):
            if fallback in installed:
                selected.append(fallback)
                break
    return "+".join(dict.fromkeys(selected)) or None


def rapidocr_confidence(tokens: list[dict]) -> float:
    values = [float(token.get("confidence", 0)) for token in tokens if token.get("text", "").strip()]
    return min(0.80, sum(values) / len(values)) if values else 0.0
