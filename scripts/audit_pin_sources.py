"""Audit a datasheet's mux tables and array figures without fixed page numbers."""
import argparse
import hashlib
import json
import re
from pathlib import Path

import pdfplumber

from datasheet_to_allegro.pin_reconciliation import reconcile_pin_sources, write_pin_reconciliation
from datasheet_to_allegro.universal_extractor import PageRecord


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    records, declarations = [], {}
    with pdfplumber.open(args.pdf) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            for family, count in re.findall(r"\b(BGA|WLCSP|CSP)\s*\(\s*(\d+)\s+(?:pins|balls)\b", text, re.I):
                declarations.setdefault(family.upper(), set()).add(int(count))
            interesting = bool(re.search(r"pin\s+list.*function\s+table|pin\s+configuration.*(?:diagram|view)", text, re.I))
            records.append(PageRecord(number, text, page.extract_tables() if interesting else []))
            if interesting and re.search(r"Figure\s+\d+.*Pin Configuration|Pin Connections", text, re.I):
                page.to_image(resolution=130).save(args.output / f"source_page_{number}.png")
            page.close()
    digest = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
    for family, counts in declarations.items():
        if len(counts) != 1:
            print("Conflicting declarations", family, sorted(counts), flush=True)
            continue
        report = reconcile_pin_sources(records, family, next(iter(counts)), digest)
        if report:
            write_pin_reconciliation(report, args.output / family)
            print(json.dumps({k: report.get(k) for k in ["variant", "status", "table_count", "map_count", "gpio_count", "pin_type_counts", "issues"]}, ensure_ascii=False), flush=True)
    (args.output / "source_text.json").write_text(json.dumps({"source": str(args.pdf.resolve()), "sha256": digest,
        "pages": [{"page": r.number, "text": r.text} for r in records]}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
