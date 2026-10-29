"""Conservative table evidence for comparison, not automatic library approval.

Keep package columns separate and retain raw names/types. A pin list recovered
from a reference is evidence, not permission to silently rename another device.
"""
from __future__ import annotations

import re

from .pin_lexicon import _clean, _header_role, _pin_numbers, valid_signal_label


def text_quality_flags(text: str) -> list[str]:
    flags = []
    if len(text.strip()) < 80:
        flags.append("sparse_text")
    if sum(ord(c) < 32 and c not in "\n\r\t" for c in text) >= 5:
        flags.append("unmapped_control_glyphs")
    if len(re.findall(r"\(cid:\d+\)", text)) >= 3:
        flags.append("unmapped_cid_glyphs")
    if text.count("\ufffd") >= 3:
        flags.append("replacement_glyphs")
    return flags


def extract_coordinate_pin_evidence(text: str, *, continuation: bool = False) -> list[dict]:
    """Address/X/Y/Net Name tables, including explicitly scoped continuation pages."""
    if not continuation and not re.search(r"Address\s+X\s+Y\s+Net\s+Name", text, re.I):
        return []
    records = []
    pattern = re.compile(r"^\s*([A-Z]{1,3}\d+)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+([A-Za-z0-9_/#().+\[\]\-]+)\s*$")
    for row_index, line in enumerate(text.splitlines()):
        match = pattern.fullmatch(line)
        if match:
            number, x, y, name = match.groups()
            records.append({"variant": "unspecified", "pin_number": number, "pin_name": name,
                            "row": row_index, "x": x, "y": y, "raw_type": "", "description": ""})
    return records


def extract_table_pin_evidence(table: list[list[object]]) -> list[dict[str, object]]:
    if not table:
        return []
    first = [_clean(c) for c in table[0]]
    roles = [_header_role(c) for c in first]
    initial_depth = 1
    pin_parents = {'引脚', '引腳', '管脚', '接脚', '接腳'}
    if (('name' not in roles or 'number' not in roles) and len(table)>1
            and any(header in pin_parents for header in first)):
        # Chinese parent pin heading with explicit number/name children.
        # Child labels alone do not qualify arbitrary numbered data tables.
        second = [_clean(c) for c in table[1]]
        parent = ''
        combined = []
        for top, child in zip(first, second):
            if top:
                parent = top if top in pin_parents else ''
            combined.append(' '.join(x for x in (top or parent,child) if x))
        new_roles = [_header_role(c) for c in combined]
        if 'name' in new_roles and 'number' in new_roles:
            first, roles, initial_depth = combined, new_roles, 2
    if "name" in roles and "number" not in roles:
        for col, header in enumerate(first):
            if header.lower() in {"no.", "no", "number"}:
                roles[col] = "number"
                break
    if "number" not in roles or "name" not in roles:
        return []
    number_column, name_column = roles.index("number"), roles.index("name")
    # Parent "Pad #" can span several child package columns. Do not take the
    # first column by default or concatenate both variants into one pin key.
    number_columns = [number_column]
    for col in range(number_column + 1, name_column):
        if first[col]:
            break
        number_columns.append(col)
    headers = first[:]
    depth = initial_depth
    variants = {col: "unspecified" for col in number_columns}
    if len(table) > 1 and initial_depth == 1:
        second = [_clean(c) for c in table[1]]
        if not any(_pin_numbers(second[c]) for c in number_columns) and (
            any(_header_role(c) for c in second) or (
                len(number_columns) > 1 and all(second[c] for c in number_columns)
            )
        ):
            depth = 2
            headers = [" ".join((headers[c], second[c])).strip() for c in range(len(headers))]
            if len(number_columns) > 1:
                for col in number_columns:
                    variants[col] = second[col]
    mapping = {}
    for col, header in enumerate(headers):
        role = _header_role(header)
        if role:
            mapping.setdefault(role, col)
    # A child "Type" under "Pad characteristics" needs independent handling.
    if depth == 2:
        for col, cell in enumerate(table[1]):
            if _header_role(_clean(cell)) == "type":
                mapping["type"] = col
    records = []
    for row_index, row in enumerate(table[depth:], depth):
        cells = [_clean(c) for c in row]
        # Join a printed continuation only at an explicit identifier separator.
        # Other embedded spaces stay unresolved instead of guessing a name.
        raw_name = str(row[name_column] or "")
        name = _clean(re.sub(r"(?<=[_/#])\s*\n\s*", "", raw_name))
        # Recover a wrapped identifier only when its pieces are independently
        # corroborated by this same pin's description (e.g. default + OSC).
        if "\n" in raw_name and "description" in mapping:
            joined = re.sub(r"\s+", "", raw_name)
            desc = re.sub(r"\s+", "", cells[mapping["description"]])
            parts = re.split(r"[-/]", joined)
            if len(parts) > 1 and all(p and p in desc for p in parts):
                name = joined
        if not name or _header_role(name):
            continue
        # Group labels, footnotes and descriptive text are not signal names.
        if not valid_signal_label(name):
            continue
        for column in number_columns:
            for number in _pin_numbers(cells[column]):
                records.append({"variant": variants[column], "pin_number": number,
                    "pin_name": name, "row": row_index, "number_column": column,
                    "raw_type": cells[mapping["type"]] if "type" in mapping else "",
                    "description": cells[mapping["description"]] if "description" in mapping else ""})
    return records


def compare_pin_evidence(native: list[dict], evidence: list[dict]) -> dict:
    """Exact physical-pin comparison; unresolved labels remain explicit."""
    observed = {}
    for row in evidence:
        observed.setdefault(row["pin_number"], []).append(row)
    native_map = {}
    for row in native:
        native_map.setdefault(row["pin_number"], []).append(row)
    matches, differences, conflicts = [], [], []
    for number in sorted(native_map.keys() & observed.keys()):
        expected = {r["pin_name"] for r in observed[number]}
        actual = {r["pin_name"] for r in native_map[number]}
        if len(expected) > 1:
            conflicts.append({"pin": number, "pdf_names": sorted(expected)})
        record = {"pin": number, "native_names": sorted(actual), "pdf_names": sorted(expected),
                  "pdf_pages": sorted({r.get("page", 0) for r in observed[number]})}
        (matches if actual == expected else differences).append(record)
    return {"native_unique_pins": len(native_map), "pdf_unique_pins": len(observed),
            "exact_name_matches": len(matches), "name_differences": differences,
            "conflicting_pdf_names": conflicts,
            "native_pins_without_pdf_evidence": sorted(native_map.keys() - observed.keys()),
            "pdf_pins_absent_from_native": sorted(observed.keys() - native_map.keys()),
            "status": "comparison_only_requires_engineering_review"}
