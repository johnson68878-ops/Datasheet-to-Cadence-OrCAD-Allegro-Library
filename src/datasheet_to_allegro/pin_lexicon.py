"""Shared pin-table lexical rules. No dependency on exporters or document identity."""
import re

def _clean(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()

def _header_role(value: str) -> str:
    raw = _clean(value).lower()
    compact = re.sub(r"\s+", "", raw)
    if compact in {"pinname", "padname", "signalname", "ballname"}:
        return "name"
    if re.fullmatch(r"(?:i/o|io)(?:\(\d+\))?", compact):
        return "type"
    if any(token in compact for token in ("引脚号", "引腳號", "管脚号", "接脚号", "接腳號", "引脚编号", "引腳編號", "管脚编号")):
        return "number"
    if any(token in compact for token in ("引脚名称", "引腳名稱", "管脚名称", "接脚名称", "接腳名稱", "信号名称", "訊號名稱")):
        return "name"
    if any(token in compact for token in ("电气类型", "電氣類型", "引脚类型", "引腳類型", "类型", "類型")):
        return "type"
    if any(token in compact for token in ("说明", "說明", "描述", "备注", "備註")):
        return "description"
    text = re.sub(r"[^a-z0-9#]+", " ", raw).strip()
    if re.search(r"\b(?:pin|ball|terminal|pad)\s*(?:no|num|number)\b", text) or re.fullmatch(r"(?:pin|ball|terminal|pad)\s*#", text) or text in {
        "pin",
        "pins",
        "balls",
        "terminals",
        "ball",
        "bga",
        "bga pin",
        "terminal",
        "pad",
    }:
        return "number"
    if re.search(r"\b(?:pin|signal|pad)\s*(?:name|function)\b", text) or text.endswith(" name") or text in {
        "name",
        "signal",
        "function",
        "symbol",
        "pad function name",
    }:
        return "name"
    if "electrical type" in text or "pin type" in text or "pad type" in text or text == "type":
        return "type"
    if any(token in text for token in ("description", "details", "remark", "comment")):
        return "description"
    return ""

def _valid_pin_number(value: str) -> bool:
    token = value.strip().upper()
    if not token or len(token) > 12 or token in {"-", "N/A", "NA", "PIN", "BALL", "NUMBER", "NO"}:
        return False
    return bool(re.fullmatch(r"(?:[A-Z]{0,3}\d{1,4}|\d{1,4}[A-Z]{0,2})", token))

def _pin_numbers(value: str) -> list[str]:
    cleaned = re.sub(r"[()\[\]{}*†‡]", "", _clean(value).upper())
    tokens = [token for token in re.split(r"[,;/、，；\s]+", cleaned) if token]
    return tokens if tokens and all(_valid_pin_number(token) for token in tokens) else []


def valid_signal_label(value: str) -> bool:
    """Identifiers plus explicitly printed aliases, not free-form prose."""
    identifier = r"[A-Za-z0-9_/#.+\[\]\-]+"
    atom = rf"{identifier}(?:\s*\({identifier}\))*"
    return bool(re.fullmatch(rf"{atom}(?:\s+or\s+{atom})*", value))
