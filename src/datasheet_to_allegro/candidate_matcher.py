from __future__ import annotations

import json
import re
from pathlib import Path


PASSIVE_CASES = ("01005", "0201", "0402", "0603", "0805", "1206", "2012")


def identify_passive_identity(text: str) -> dict[str, object]:
    lower = text.lower()
    kinds: list[str] = []
    if re.search(r"\bresistor\b|\bresistance\b", lower):
        kinds.append("R")
    if re.search(r"\bcapacitor\b|\bcapacitance\b|\bmlcc\b", lower):
        kinds.append("C")
    if re.search(r"\binductor\b|\binductance\b|\bchoke\b", lower):
        kinds.append("L")
    cases = [case for case in PASSIVE_CASES if re.search(rf"(?<!\d){re.escape(case)}(?!\d)", lower)]
    return {"component_kinds": kinds, "case_codes": cases}


def match_passive_catalog(text: str, catalog_path: str | Path) -> list[dict[str, object]]:
    identity = identify_passive_identity(text)
    kinds = set(identity["component_kinds"])
    cases = set(identity["case_codes"])
    if len(kinds) != 1 or not cases:
        return []
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    matches: list[dict[str, object]] = []
    for item in catalog.get("footprints", []):
        name = item["symbol_name"].lower()
        if item.get("component_kind") not in kinds or not any(case in name for case in cases):
            continue
        matches.append({
            "symbol_name": item["symbol_name"],
            "component_kind": item["component_kind"],
            "family": item["family"],
            "padstacks": [pad["file"] for pad in item.get("padstacks", [])],
            "score": 0.72,
            "status": "candidate_only",
            "blocking_checks": [
                "Datasheet端头尺寸与公差尚未逐项匹配",
                "Manufacturer Recommended Land Pattern尚未确认",
                "EE尚未确认完整订购料号",
                "Layout尚未确认焊盘、阻焊与锡膏规则",
            ],
        })
    return matches

