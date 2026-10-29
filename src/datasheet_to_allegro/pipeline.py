from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import FootprintSpec
from .package_vocabulary import identify_package_families


@dataclass(frozen=True)
class DatasheetFingerprint:
    source_file: str
    page_count: int
    title_hint: str
    detected_families: tuple[str, ...]
    text_confidence: float



def inspect_datasheet(pdf_path: str | Path, progress: Callable[[int, int], None] | None = None) -> tuple[DatasheetFingerprint, list[FootprintSpec]]:
    """Run the product-independent, full-document package discovery pipeline."""
    path = Path(pdf_path)
    from .universal_extractor import analyze_universal

    records, specs = analyze_universal(path, progress)
    text = "\n".join(record.text for record in records)
    first_text = "\n".join(record.text for record in records[:3])
    title = next((line.strip() for line in first_text.splitlines() if len(line.strip()) > 8), path.stem)
    nonempty = sum(1 for char in text if not char.isspace())
    fingerprint = DatasheetFingerprint(
        source_file=path.name,
        page_count=len(records),
        title_hint=title[:160],
        detected_families=tuple(identify_package_families(text)),
        text_confidence=round(min(1.0, nonempty / max(1, len(records) * 500)), 3),
    )
    from .engineering_ir import build_variant_graph
    from .validation import validate_professional_spec

    validated = [validate_professional_spec(spec, path) for spec in specs]
    variant_graph = build_variant_graph(validated)
    graph_payload = variant_graph.model_dump(mode="json")
    result = [
        spec.model_copy(update={
            "document_scan": {**spec.document_scan, "variant_graph": graph_payload},
        })
        for spec in validated
    ]
    return fingerprint, result
