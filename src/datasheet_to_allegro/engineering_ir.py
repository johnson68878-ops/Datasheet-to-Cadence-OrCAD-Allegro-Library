from __future__ import annotations

import hashlib
import re

from .geometry import FootprintGeometry
from .models import (
    DimensionIR,
    FootprintSpec,
    Measurement,
    SourceEvidence,
    VariantEdge,
    VariantGraph,
    VariantNode,
)


def _stable_id(*parts: object) -> str:
    payload = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _evidence(measurement: Measurement | None) -> list[SourceEvidence]:
    return list(measurement.evidence) if measurement else []


def _qualifier(measurement: Measurement | None, default: str = "NONE") -> str:
    if measurement is None:
        return "UNRESOLVED"
    if measurement.min_mm is not None and measurement.max_mm is not None:
        return "RANGE"
    raw = " ".join(item.raw_text.upper() for item in measurement.evidence)
    for token in ("BSC", "TYP", "MIN", "NOM", "MAX"):
        if re.search(rf"\b{token}\b", raw):
            return token
    return default


def build_dimension_ir(spec: FootprintSpec, geometry: FootprintGeometry) -> list[DimensionIR]:
    """Build one package-neutral dimension relation list from a spec.

    The first version records the semantic relation and CAD endpoints.  PDF
    source bounding boxes stay optional until the vector/table layout parser
    can prove them; absence is explicit and never synthesized.
    """

    variant_id = variant_identity(spec)
    half_w = geometry.body_width_mm / 2
    half_h = geometry.body_length_mm / 2
    rows: list[DimensionIR] = []

    def add(
        symbol: str,
        role: str,
        axis: str,
        measurement: Measurement | None,
        start: tuple[float, float] | None,
        end: tuple[float, float] | None,
        *,
        view: str = "component_top",
        qualifier: str | None = None,
        expression: str | None = None,
    ) -> None:
        rows.append(DimensionIR(
            dimension_id=_stable_id(variant_id, symbol, role, axis),
            symbol=symbol,
            object_role=role,
            axis=axis,
            value=measurement,
            qualifier=qualifier or _qualifier(measurement),
            view=view,
            variant_id=variant_id,
            evidence=_evidence(measurement),
            start_mm=start,
            end_mm=end,
            derived_expression=expression,
        ))

    add("D", "body", "x", spec.body_width, (-half_w, -half_h), (half_w, -half_h))
    add("E", "body", "y", spec.body_length, (half_w, -half_h), (half_w, half_h))
    add("A", "body", "none", spec.package_height, None, None, view="side")
    pitch_x = spec.pitch_x or spec.pitch
    pitch_y = spec.pitch_y or spec.pitch
    if spec.package_type.upper() in {"BGA", "WLCSP", "LGA"}:
        add("eX", "pitch", "x", pitch_x, None, None, qualifier="BSC" if pitch_x else "UNRESOLVED")
        add("eY", "pitch", "y", pitch_y, None, None, qualifier="BSC" if pitch_y else "UNRESOLVED")
        if pitch_x and spec.column_count:
            span = (spec.column_count - 1) * _measurement_value(pitch_x)
            add("GRID_X", "grid_span", "x", _derived_measurement(span, pitch_x), (-span / 2, 0), (span / 2, 0), qualifier="DERIVED", expression="(columns-1)*eX")
        if pitch_y and spec.row_names:
            span = (len(spec.row_names) - 1) * _measurement_value(pitch_y)
            add("GRID_Y", "grid_span", "y", _derived_measurement(span, pitch_y), (0, -span / 2), (0, span / 2), qualifier="DERIVED", expression="(rows-1)*eY")
    else:
        add("e", "pitch", "none", spec.pitch, None, None, qualifier="BSC" if spec.pitch else "UNRESOLVED")
    add("b", "terminal", "none", spec.terminal_width_min, None, None)
    add("L", "terminal", "none", spec.terminal_length_min, None, None)
    add("D2", "exposed_pad", "x", spec.exposed_pad_width, None, None)
    add("E2", "exposed_pad", "y", spec.exposed_pad_length, None, None)
    return rows


def _measurement_value(item: Measurement) -> float:
    if item.value_mm is not None:
        return item.value_mm
    if item.nominal_mm is not None:
        return item.nominal_mm
    if item.min_mm is not None and item.max_mm is not None:
        return (item.min_mm + item.max_mm) / 2
    return item.min_mm if item.min_mm is not None else float(item.max_mm or 0)


def _derived_measurement(value: float, source: Measurement) -> Measurement:
    return Measurement(
        value_mm=round(value, 6),
        original_value=f"{value:g}",
        original_unit="mm",
        evidence=list(source.evidence),
    )


def variant_identity(spec: FootprintSpec) -> str:
    return _stable_id(spec.package_type.upper(), spec.package_code or "", spec.pin_count)


def build_variant_graph(specs: list[FootprintSpec]) -> VariantGraph:
    nodes: list[VariantNode] = []
    edges: list[VariantEdge] = []
    conflicts: list[str] = []
    for spec in specs:
        variant_id = variant_identity(spec)
        pages = sorted({ev.page for values in spec.source_evidence.values() for ev in values})
        confidence = min(spec.confidence.values()) if spec.confidence else 0.0
        nodes.append(VariantNode(
            variant_id=variant_id,
            package_type=spec.package_type.upper(),
            package_code=spec.package_code,
            pin_count=spec.pin_count,
            evidence_pages=pages,
            confidence=confidence,
        ))
    for index, left in enumerate(nodes):
        for right in nodes[index + 1:]:
            same_code = bool(left.package_code and right.package_code and left.package_code == right.package_code)
            same_shape = left.package_type == right.package_type and left.pin_count == right.pin_count
            if same_code and not same_shape:
                reason = f"封装代码{left.package_code}对应了不同封装族或Pin数量"
                conflicts.append(reason)
                edges.append(VariantEdge(source=left.variant_id, target=right.variant_id, relation="conflicts", reason=reason))
            elif same_code or same_shape:
                edges.append(VariantEdge(source=left.variant_id, target=right.variant_id, relation="same_variant", reason="封装代码或封装族/Pin数量一致"))
    selected = nodes[0].variant_id if len(nodes) == 1 and not conflicts else None
    return VariantGraph(nodes=nodes, edges=edges, selected_variant_id=selected, conflicts=conflicts)
