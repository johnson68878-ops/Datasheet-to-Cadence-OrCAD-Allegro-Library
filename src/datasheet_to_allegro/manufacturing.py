from __future__ import annotations

import json
from pathlib import Path


def load_manufacturing_profile(path: str | Path) -> dict[str, object]:
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"profile_name", "source_url", "retrieved_date", "rules", "status"}
    missing = sorted(required - profile.keys())
    if missing:
        raise ValueError(f"制造 Profile 缺少字段：{', '.join(missing)}")
    if profile["status"] != "reference_only":
        raise ValueError("外部板厂 Profile 在公司批准前只能标记为 reference_only")
    return profile


def manufacturing_advisories(spec: dict[str, object], profile: dict[str, object]) -> list[dict[str, object]]:
    """Return review advisories; never rewrite footprint geometry automatically."""
    advisories: list[dict[str, object]] = []
    rules = profile.get("rules", {})
    pitch = (spec.get("pitch") or {}).get("value_mm") if isinstance(spec.get("pitch"), dict) else None
    if pitch is not None and rules.get("bga_conventional_pitch_mm") is not None:
        conventional = float(rules["bga_conventional_pitch_mm"])
        if float(pitch) < conventional:
            advisories.append({
                "severity": "warning",
                "code": "BELOW_FAB_CONVENTIONAL_PITCH",
                "message": f"Pitch {pitch} mm 小于该板厂常规值 {conventional} mm，必须走特殊制程确认。",
                "source_url": profile["source_url"],
            })
    return advisories

