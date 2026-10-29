"""Cross-check physical mux tables against independently drawn array maps.

No device names, page ranges, or inferred missing balls are used. Raw names
remain evidence; normalization only removes layout whitespace and applies
Unicode compatibility normalization. Ambiguous spelling remains a conflict.
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from .mux_tables import clean, mux_schema

ROWS = set("ABCDEFGHJKLMNPRTUVWY")
ABSENT = {"", "-", "NC", "N/A"}


def normalized_name(raw: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", raw)).upper()


def package_label(raw: str) -> str:
    label = re.sub(r"pin|ball|pad|number|no\.?", "", raw, flags=re.I)
    label = re.sub(r"[^A-Z0-9]", "", label.upper())
    return "CSP" if label == "WLCSP" else label


def collect_mux_pins(records, variant: str) -> dict:
    """Stitch table continuations, while surfacing every rejected data row."""
    selected = package_label(variant)
    pins, issues, audit = {}, [], []
    active = None
    absent_variant = False
    previous_schema = None
    previous_page = -2
    previous_anchor = None
    table_pages = []
    for record in records:
        anchor_match = re.search(r"Table\s+(\d+)\s*[:.]\s*[^\n]*(?:Pin List|Function Table)", record.text, re.I)
        anchor = anchor_match.group(1) if anchor_match else None
        matched_page = False
        for table_index, table in enumerate(record.tables):
            if not table:
                continue
            schema = mux_schema(table)
            start = 1
            if schema is None:
                # A headerless fragment is valid only immediately after the
                # preceding table, with the same explicit caption and width.
                if not (previous_schema and anchor and anchor == previous_anchor
                        and record.number == previous_page + 1
                        and all(len(row) == previous_schema["width"] for row in table)):
                    continue
                schema, start = previous_schema, 0
                audit.append({"event": "headerless_continuation", "page": record.number})
            else:
                schema = {**schema, "width": len(table[0])}
                if previous_schema and schema == previous_schema and record.number == previous_page + 1 and anchor == previous_anchor:
                    audit.append({"event": "repeated_header_removed", "page": record.number, "raw": table[0]})
                else:
                    active = None
                    absent_variant = False
                    audit.append({"event": "table_start", "page": record.number, "raw": table[0]})
            columns = [(i, h) for i, h in schema["physical"] if package_label(h) == selected]
            if len(columns) != 1:
                continue
            column = columns[0][0]
            matched_page = True
            for row_index, row in enumerate(table[start:], start):
                if mux_schema([row]):
                    audit.append({"event": "repeated_header_removed", "page": record.number, "row": row_index})
                    continue
                def cell(index):
                    return clean(row[index]) if index is not None and index < len(row) else ""
                if not any(clean(c) for c in row):
                    continue
                location = {"page": record.number, "row": row_index, "table_index": table_index}
                if len(row) != schema["width"]:
                    issues.append({"code": "COLUMN_COUNT", **location, "raw": row})
                    active = None
                    continue
                new_pin = any(cell(i) for i, _ in schema["physical"])
                if new_pin:
                    number = cell(column).upper()
                    active = None
                    absent_variant = number in ABSENT
                    if number in ABSENT:
                        continue
                    if not re.fullmatch(r"[A-Z]{1,3}[1-9]\d{0,3}", number):
                        issues.append({"code": "INVALID_PHYSICAL_PIN", **location, "raw": row})
                        continue
                    if number in pins:
                        issues.append({"code": "DUPLICATE_PHYSICAL_PIN", "pin": number, **location})
                        continue
                    active = {"number": number, "gpio": cell(schema["gpio"]),
                              "page": record.number, "functions": [], "source_rows": []}
                    pins[number] = active
                raw_name = cell(schema["name"])
                if raw_name in {"", "-"}:
                    continue
                if active is None:
                    # Empty package cells after an explicitly absent variant
                    # belong to that absent variant, not to the prior pin.
                    if not absent_variant:
                        issues.append({"code": "ORPHAN_CONTINUATION", **location, "raw": row})
                    continue
                select = cell(schema["select"])
                function = {"select": select, "name": raw_name,
                            "description": cell(schema["description"]), "pin_type": cell(schema["type"]),
                            **location}
                if any(f["select"] == select and f["name"] != raw_name for f in active["functions"]):
                    issues.append({"code": "FUNCTION_SELECT_CONFLICT", "pin": active["number"], **location, "raw": row})
                active["functions"].append(function)
                active["source_rows"].append({**location, "raw": row})
                normalized = normalized_name(raw_name)
                if normalized != raw_name.upper():
                    audit.append({"event": "name_normalization", "pin": active["number"],
                                  **location, "raw": raw_name, "normalized": normalized})
            previous_schema, previous_page, previous_anchor = schema, record.number, anchor
        if matched_page:
            table_pages.append(record.number)
        elif record.number > previous_page:
            active = None
            absent_variant = False
    for number, pin in pins.items():
        if not pin["functions"]:
            issues.append({"code": "MISSING_FUNCTION", "pin": number, "page": pin["page"]})
    return {"pins": pins, "issues": issues, "audit": audit, "table_pages": table_pages}


def collect_array_maps(records, variant: str) -> list[dict]:
    family = package_label(variant)
    maps = []
    for record in records:
        # Bind the family to the figure caption, not to other package mentions.
        captions = [line for line in record.text.splitlines()
                    if re.search(r"Figure\s+\d+.*(?:Pin Configuration|Pin Map|Ball Map)", line, re.I)
                    and re.search(r"Top\s*View", line, re.I)]
        captions = [c for c in captions if re.search(r"\b(?:WLCSP|CSP)\b" if family == "CSP" else rf"\b{re.escape(family)}\b", c, re.I)]
        if not captions:
            continue
        absent_nc = bool(re.search(r"no connects.*not populated", clean(record.text), re.I))
        for table in record.tables:
            columns, header_index = [], -1
            for index, row in enumerate(table[:4]):
                found = [(i, int(clean(c))) for i, c in enumerate(row) if re.fullmatch(r"\d{1,2}", clean(c))]
                if len(found) >= 3 and [n for _, n in found] == list(range(1, len(found) + 1)):
                    columns, header_index = found, index
                    break
            if not columns:
                continue
            cells, rows, issues = {}, [], []
            for row in table[header_index + 1:]:
                label = next((clean(c) for c in row[:columns[0][0]] if clean(c) in ROWS), None)
                if not label:
                    continue
                if label in rows:
                    issues.append({"code": "DUPLICATE_MAP_ROW", "row": label, "page": record.number})
                    continue
                rows.append(label)
                for index, number in columns:
                    raw = clean(row[index]) if index < len(row) else ""
                    name = normalized_name(raw)
                    kind = "unpopulated" if name == "NC" and absent_nc else (name if name in {"NC", "DNU", "DNP"} else "connected")
                    if not raw:
                        issues.append({"code": "EMPTY_MAP_CELL", "pin": f"{label}{number}", "page": record.number})
                    cells[f"{label}{number}"] = {"name": name, "raw_name": raw, "kind": kind,
                                                    "page": record.number, "figure": captions[0]}
            if rows:
                maps.append({"cells": cells, "rows": rows, "columns": len(columns),
                             "page": record.number, "issues": issues})
    return maps


def reconcile_pin_sources(records, variant: str, expected_count: int, source_sha256: str = "") -> dict | None:
    tables = collect_mux_pins(records, variant)
    if not tables["pins"]:
        return None
    maps = collect_array_maps(records, variant)
    maps = [m for m in maps if sum(c["kind"] != "unpopulated" for c in m["cells"].values()) == expected_count]
    issues = list(tables["issues"])
    revisions = sorted(set(re.findall(r"\bDS-[A-Z0-9]+-[A-Za-z0-9.]+", "\n".join(r.text for r in records))))
    report = {"variant": variant, "expected_count": expected_count, "source_sha256": source_sha256,
              "document_revisions": revisions, "table_count": len(tables["pins"]),
              "table_pages": tables["table_pages"], "audit": tables["audit"], "issues": issues, "pins": []}
    if len(maps) != 1:
        issues.append({"code": "MAP_NOT_UNIQUE", "candidate_count": len(maps)})
        report["status"] = "review_required"
        return report
    diagram = maps[0]
    issues.extend(diagram["issues"])
    report.update({"map_page": diagram["page"], "map_rows": diagram["rows"], "map_columns": diagram["columns"],
                   "map_count": sum(c["kind"] != "unpopulated" for c in diagram["cells"].values()),
                   "unpopulated": [n for n, c in diagram["cells"].items() if c["kind"] == "unpopulated"]})
    for number, cell in diagram["cells"].items():
        pin = tables["pins"].get(number)
        names = {normalized_name(f["name"]) for f in pin["functions"]} if pin else set()
        status = "match"
        if cell["kind"] == "unpopulated":
            status = "unpopulated"
            if pin:
                status = "populated_table_vs_empty_map"
        elif pin is None:
            status = "map_only"
        elif cell["name"] not in names:
            status = "name_conflict"
        if status not in {"match", "unpopulated"}:
            issues.append({"code": status.upper(), "pin": number, "map_name": cell["raw_name"],
                           "map_page": cell["page"], "table_page": pin["page"] if pin else None,
                           "table_names": sorted(names)})
        report["pins"].append({"number": number, "map_name": cell["raw_name"], "map_kind": cell["kind"],
                               "map_page": cell["page"], "table_page": pin["page"] if pin else None,
                               "gpio": pin["gpio"] if pin else "", "functions": pin["functions"] if pin else [],
                               "source_rows": pin["source_rows"] if pin else [], "status": status})
    for number in tables["pins"].keys() - diagram["cells"].keys():
        issues.append({"code": "TABLE_ONLY", "pin": number})
    report["gpio_count"] = sum(p["gpio"].isdigit() for p in tables["pins"].values())
    family_pattern = r"(?:WLCSP|CSP)" if package_label(variant) == "CSP" else re.escape(variant)
    gpio_declarations = [{"page": r.number, "count": int(m[1]), "raw": m[0]}
                         for r in records for m in re.finditer(rf"\b{family_pattern}\s+with\s+(\d+)\s+GPIO", r.text, re.I)]
    report["gpio_declarations"] = gpio_declarations
    if any(d["count"] != report["gpio_count"] for d in gpio_declarations):
        issues.append({"code": "GPIO_COUNT_CONFLICT", "observed": report["gpio_count"], "declarations": gpio_declarations})
    report["mandatory_notes"] = [{"page": r.number, "text": m[0]} for r in records
                                  for m in re.finditer(r"Ball\s+[A-Z]+\d+[^.]*must\s+be\s+grounded[^.]*\.", clean(r.text), re.I)]
    report["pin_type_counts"] = dict(Counter(f["pin_type"] for p in tables["pins"].values()
                                               for f in p["functions"] if not p["gpio"].isdigit()))
    report["power_ground_names"] = dict(Counter(normalized_name(f["name"]) for p in tables["pins"].values()
                                                for f in p["functions"] if f["pin_type"].lower() in {"power", "ground"}))
    report["status"] = "pass" if not issues and len(tables["pins"]) == expected_count else "review_required"
    return report


def write_pin_reconciliation(report: dict, directory: Path, geometry=None, spec=None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pin_reconciliation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "pin_review_queue.json").write_text(json.dumps(report["issues"], ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ["number", "map_name", "map_kind", "map_page", "table_page", "gpio", "status",
              "document_revision", "source_sha256", "function_names", "x_mm", "y_mm", "copper_width_mm",
              "copper_length_mm", "dimension_source_pages", "land_rule_status"]
    pads = {p.number: p for p in geometry.pads} if geometry else {}
    dimension_pages = sorted({e.page for m in [spec.pitch, spec.pitch_x, spec.pitch_y, spec.body_width, spec.body_length]
                              if m for e in m.evidence}) if spec else []
    with (directory / "pin_by_pin_evidence.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for pin in report["pins"]:
            pad = pads.get(pin["number"])
            writer.writerow({**{k: pin[k] for k in fields if k in pin},
                             "document_revision": ";".join(report["document_revisions"]),
                             "source_sha256": report["source_sha256"],
                             "function_names": ";".join(f["name"] for f in pin["functions"]),
                             "x_mm": pad.x_mm if pad else "", "y_mm": pad.y_mm if pad else "",
                             "copper_width_mm": pad.width_mm if pad else "", "copper_length_mm": pad.length_mm if pad else "",
                             "dimension_source_pages": ";".join(map(str, dimension_pages)),
                             "land_rule_status": spec.recommended_land_pattern.status if spec else ""})


def require_reconciled_geometry(spec, geometry) -> None:
    """A semantic table omission may be reviewed; uncertain geometry cannot."""
    report = spec.document_scan.get("pin_reconciliation")
    if not report:
        return
    # MAP_ONLY has independent physical/name evidence in the drawing and is
    # retained explicitly in the review queue. No table row is invented.
    errors = [i for i in report["issues"] if i["code"] != "MAP_ONLY"]
    expected = {p["number"] for p in report["pins"] if p["map_kind"] != "unpopulated"}
    actual = [p.number for p in geometry.pads]
    positions = [(round(p.x_mm, 6), round(p.y_mm, 6)) for p in geometry.pads]
    if errors or not expected or expected != set(actual) or len(actual) != len(expected) or len(set(positions)) != len(positions):
        raise ValueError("Pin reconciliation failed: review pin_review_queue.json; unsafe array geometry is not exportable")
    def nominal(measurement):
        return next((getattr(measurement, key) for key in ("value_mm", "nominal_mm", "min_mm", "max_mm")
                     if measurement is not None and getattr(measurement, key) is not None), None)
    px, py = nominal(spec.pitch_x or spec.pitch), nominal(spec.pitch_y or spec.pitch)
    rows, columns = report["map_rows"], report["map_columns"]
    if not px or not py:
        raise ValueError("Pin reconciliation requires evidenced X/Y pitch")
    for pad in geometry.pads:
        match = re.fullmatch(r"([A-Z]+)(\d+)", pad.number)
        if not match or match[1] not in rows:
            raise ValueError(f"Invalid grid address {pad.number}")
        x = (int(match[2]) - 1 - (columns - 1) / 2) * px
        y = ((len(rows) - 1) / 2 - rows.index(match[1])) * py
        if abs(x - pad.x_mm) > 0.001 or abs(y - pad.y_mm) > 0.001:
            raise ValueError(f"Off-grid or mirrored ball {pad.number}: expected {(x, y)}, actual {(pad.x_mm, pad.y_mm)}")
