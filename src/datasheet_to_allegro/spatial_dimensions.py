"""Bind high-confidence OCR values to dimension symbols by table coordinates."""
import re


def symbol_values(tokens: list[dict], symbol: str) -> tuple[list[float], str, float] | None:
    candidates = {}
    for label in tokens:
        if label.get("text", "").strip() != symbol or label.get("confidence", 0) < .93:
            continue
        box = label.get("polygon", [])
        if len(box) != 4:
            continue
        x = max(p[0] for p in box)
        y0, y1 = min(p[1] for p in box), max(p[1] for p in box)
        cy = (y0 + y1) / 2
        found = []
        confidence = label.get('confidence',0)
        uncertain = False
        for token in tokens:
            raw = token.get("text", "").strip()
            points = token.get("polygon", [])
            if len(points) != 4 or not re.fullmatch(r"\d+\.\d+", raw):
                continue
            nx, ny = min(p[0] for p in points), sum(p[1] for p in points) / 4
            if nx > x and abs(ny - cy) < (y1 - y0) * .55:
                confidence = min(confidence,token.get('confidence',0))
                uncertain |= token.get('confidence',0) < .95
                found.append((nx, float(raw)))
        values = [v for _, v in sorted(found)]
        if not uncertain and 1 <= len(values) <= 3 and values == sorted(values) and all(0 < v < 50 for v in values):
            candidates[tuple(values)] = (f"OCR symbol {symbol} at {box}; values {values} in the same table row",confidence)
    if len(candidates) != 1:
        return None
    values, (raw,confidence) = next(iter(candidates.items()))
    return list(values), raw, confidence


def grid_pitch(tokens: list[dict], rows: int, columns: int) -> tuple[float, str] | None:
    """Cross-check equal X/Y pitch with two independent edge spans and ball map."""
    dx, dy = symbol_values(tokens, "D1"), symbol_values(tokens, "E1")
    if not dx or not dy or len(dx[0]) != 1 or len(dy[0]) != 1 or min(rows, columns) < 2:
        return None
    px, py = dx[0][0] / (columns - 1), dy[0][0] / (rows - 1)
    if abs(px - py) > .0001 or not .05 <= px <= 2:
        return None
    return round(px, 6), f"{dx[1]}; {dy[1]}; D1/({columns}-1) = E1/({rows}-1) = {px:g} mm"


def labeled_axis_pitches(text: str):
    """Only explicit axis labels bind asymmetric pitch; e/e1 alone cannot."""
    found={}
    for match in re.finditer(r'\b(?:pitch\s*([XY])|([XY])\s*(?:axis\s*)?pitch)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*mm\b',text,re.I):
        axis=(match[1] or match[2]).upper()
        found.setdefault(axis,[]).append((float(match[3]),match[0]))
    return {axis:values[0] for axis,values in found.items() if len({v for v,_ in values})==1}
