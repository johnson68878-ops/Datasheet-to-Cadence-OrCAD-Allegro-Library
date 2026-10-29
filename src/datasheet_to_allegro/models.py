from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class SourceEvidence(BaseModel):
    page: int
    figure_or_table: str
    raw_text: str
    extraction_method: Literal["pdf_text", "table_extract", "ocr", "ocr_cross_checked", "visual_verified"]
    confidence: float = Field(ge=0.0, le=1.0)
    document_kind: Literal[
        "datasheet", "package_drawing", "recommended_land_pattern",
        "manufacturer_application_note", "company_rule", "industry_standard",
        "fab_capability", "reference_design", "community_example",
    ] = "datasheet"
    evidence_scope: Literal[
        "package_outline", "component_terminal", "land_pattern", "pin_map",
        "pin_1", "solder_mask", "paste_mask", "courtyard", "assembly",
        "thermal_via", "other",
    ] = "other"
    source_title: str | None = None
    source_uri: str | None = None
    source_revision: str | None = None


class Measurement(BaseModel):
    value_mm: float | None = None
    min_mm: float | None = None
    nominal_mm: float | None = None
    max_mm: float | None = None
    original_value: str
    original_unit: str
    evidence: list[SourceEvidence]


class RuleStatus(BaseModel):
    status: Literal["manufacturer_recommended", "reference_design", "calculated", "company_rule", "missing"]
    value: Any = None
    evidence: list[SourceEvidence] = Field(default_factory=list)
    note: str | None = None
    source_tier: Literal[
        "device_datasheet", "manufacturer_package_document",
        "manufacturer_application_note", "company_rule", "industry_standard",
        "fab_capability", "reference_design", "community_example", "missing",
    ] = "missing"
    source_uri: str | None = None
    source_revision: str | None = None


class UnresolvedItem(BaseModel):
    code: str
    severity: Literal["warning", "blocking"] = "blocking"
    message: str
    required_action: str


class VerificationResult(BaseModel):
    check_id: str
    status: Literal["pass", "warning", "fail"]
    message: str
    fields: list[str] = Field(default_factory=list)
    evidence_pages: list[int] = Field(default_factory=list)


class BoundingBoxIR(BaseModel):
    """Page-space or CAD-space rectangle with an explicit coordinate system."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float
    coordinate_system: Literal["pdf_points", "component_top_mm", "terminal_bottom_mm", "allegro_mm"]


class DimensionIR(BaseModel):
    """A dimension as an engineering relation, not merely an OCR number."""

    dimension_id: str
    symbol: str
    object_role: Literal[
        "body", "terminal", "pitch", "grid_span", "exposed_pad", "pcb_land",
        "solder_mask", "paste_mask", "courtyard", "assembly", "other",
    ]
    axis: Literal["x", "y", "radial", "diameter", "none"] = "none"
    value: Measurement | None = None
    qualifier: Literal["BSC", "TYP", "MIN", "NOM", "MAX", "RANGE", "DERIVED", "POC", "UNRESOLVED", "NONE"] = "NONE"
    view: Literal["component_top", "terminal_bottom", "side", "section", "unresolved"] = "unresolved"
    variant_id: str
    evidence: list[SourceEvidence] = Field(default_factory=list)
    source_bbox: BoundingBoxIR | None = None
    start_mm: tuple[float, float] | None = None
    end_mm: tuple[float, float] | None = None
    label_anchor_mm: tuple[float, float] | None = None
    derived_expression: str | None = None


class VariantNode(BaseModel):
    variant_id: str
    package_type: str
    package_code: str | None = None
    pin_count: int | None = None
    evidence_pages: list[int] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class VariantEdge(BaseModel):
    source: str
    target: str
    relation: Literal["same_variant", "conflicts", "derived_from", "candidate_for"]
    reason: str


class VariantGraph(BaseModel):
    nodes: list[VariantNode] = Field(default_factory=list)
    edges: list[VariantEdge] = Field(default_factory=list)
    selected_variant_id: str | None = None
    conflicts: list[str] = Field(default_factory=list)


class ActualPadIR(BaseModel):
    number: str
    x_mm: float
    y_mm: float
    width_mm: float
    length_mm: float
    rotation_deg: float = 0.0
    padstack: str | None = None
    mask_apertures: dict[str, list[float] | None] = Field(default_factory=dict)
    copper_polygons: list[list[list[float]]] = Field(default_factory=list)


class ActualLayerObjectIR(BaseModel):
    layer: str
    object_type: str
    count: int
    bbox: BoundingBoxIR | None = None


class AllegroActualIR(BaseModel):
    schema_version: str = "1.0.0"
    source_dra: str
    extracted_in_fresh_session: bool
    allegro_version: str | None = None
    units: Literal["mm"] = "mm"
    pins: list[ActualPadIR] = Field(default_factory=list)
    layers: list[ActualLayerObjectIR] = Field(default_factory=list)
    place_bound_bbox: BoundingBoxIR | None = None
    package_height_max_mm: float | None = None
    drc_count: int | None = None
    extraction_log: str | None = None
    complete: bool = False

    paste_polygons: list[list[list[float]]] = Field(default_factory=list)
    polygon_review_errors: list[str] = Field(default_factory=list)
    texts: list[dict[str, Any]] = Field(default_factory=list)
    layer_geometry: list[dict[str, Any]] = Field(default_factory=list)
    geometry_inventory_layers: list[str] = Field(default_factory=list)


class DiffFinding(BaseModel):
    check_id: str
    status: Literal["pass", "warning", "fail"]
    expected: Any = None
    actual: Any = None
    tolerance_mm: float | None = None
    message: str


class SpecActualDiff(BaseModel):
    schema_version: str = "1.0.0"
    part_number: str
    source_dra: str
    findings: list[DiffFinding] = Field(default_factory=list)
    status: Literal["pass", "warning", "fail"]
    publish_allowed: bool = False


class FootprintSpec(BaseModel):
    schema_version: str = "0.2.0"
    status: Literal["draft", "engineer_approved"] = "draft"
    approved_by: str | None = None
    part_number: str
    compatible_part_numbers: list[str] = Field(default_factory=list)
    manufacturer: str
    package_type: str
    source_file: str
    source_sha256: str | None = None
    source_revision: str | None = None
    document_scan: dict[str, Any] = Field(default_factory=dict)
    package_code: str | None = None
    view_convention: Literal["component_top", "terminal_bottom", "mixed", "unresolved"] = "unresolved"
    unit: Literal["mm"] = "mm"
    pin_count: int
    side_pin_counts: list[int] = Field(default_factory=list)
    row_names: list[str] = Field(default_factory=list)
    column_count: int | None = None
    missing_positions: list[str] = Field(default_factory=list)
    grid_rows: int | None = None
    grid_columns: int | None = None
    pitch: Measurement | None = None
    pitch_x: Measurement | None = None
    pitch_y: Measurement | None = None
    body_width: Measurement | None = None
    body_length: Measurement | None = None
    package_height: Measurement | None = None
    terminal_width_min: Measurement | None = None
    terminal_width_max: Measurement | None = None
    terminal_length_min: Measurement | None = None
    terminal_length_max: Measurement | None = None
    exposed_pad_width: Measurement | None = None
    exposed_pad_length: Measurement | None = None
    pin_1_position: str | None = None
    pin_numbering: str | None = None
    exposed_pad_number: str | None = None
    recommended_land_pattern: RuleStatus
    solder_mask_expansion: RuleStatus
    paste_mask_rule: RuleStatus
    courtyard: RuleStatus
    assembly_outline: RuleStatus
    source_evidence: dict[str, list[SourceEvidence]] = Field(default_factory=dict)
    confidence: dict[str, float] = Field(default_factory=dict)
    rule_source_hierarchy: list[str] = Field(default_factory=lambda: [
        "device_datasheet",
        "manufacturer_package_document",
        "manufacturer_application_note",
        "company_rule",
        "industry_standard",
        "fab_capability",
        "reference_design",
        "community_example",
    ])
    verification_results: list[VerificationResult] = Field(default_factory=list)
    unresolved_items: list[UnresolvedItem] = Field(default_factory=list)

    @property
    def is_generation_blocked(self) -> bool:
        return self.status != "engineer_approved" or any(
            item.severity == "blocking" for item in self.unresolved_items
        )

    @model_validator(mode="after")
    def approved_specs_cannot_have_blockers(self) -> "FootprintSpec":
        if self.status == "engineer_approved" and any(
            item.severity == "blocking" for item in self.unresolved_items
        ):
            raise ValueError("Engineer-approved spec cannot contain blocking unresolved items")
        return self
