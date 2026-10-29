from datasheet_to_allegro.models import FootprintSpec, Measurement, RuleStatus, SourceEvidence

from datasheet_to_allegro.naming import cadence_safe_component

from datasheet_to_allegro.render import write_bundle

import json

from pathlib import Path

import pytest

from datasheet_to_allegro.allegro import _padstack_name, generate_geometry_skill

from datasheet_to_allegro.geometry import _circle_to_pad_clearance, build_geometry

from datasheet_to_allegro.industry_defaults import apply_staging_manufacturing_profile

def measurement(value: float) -> Measurement:
    return Measurement(
        value_mm=value,
        original_value=str(value),
        original_unit="mm",
        evidence=[SourceEvidence(page=1, figure_or_table="Test table", raw_text=str(value), extraction_method="pdf_text", confidence=0.99)],
    )

def manufacturer_land(width: float, length: float, **extra: float) -> RuleStatus:
    value = {"pad_width_mm": width, "pad_length_mm": length, **extra}
    return RuleStatus(
        status="manufacturer_recommended",
        value=value,
        evidence=[SourceEvidence(
            page=2,
            figure_or_table="Manufacturer Recommended Land Pattern",
            raw_text=f"recommended land {width} x {length} mm",
            extraction_method="pdf_text",
            confidence=0.99,
            document_kind="recommended_land_pattern",
            evidence_scope="land_pattern",
        )],
        source_tier="device_datasheet",
    )

def qfn_spec() -> FootprintSpec:
    return FootprintSpec(
        part_number="TEST-QFN16",
        manufacturer="Test",
        package_type="QFN",
        source_file="test.pdf",
        pin_count=16,
        side_pin_counts=[4,4,4,4],
        view_convention="component_top",
        pitch=measurement(0.65),
        body_width=measurement(4.0),
        body_length=measurement(4.0),
        package_height=measurement(0.9),
        terminal_width_min=measurement(0.30),
        terminal_length_min=measurement(0.65),
        pin_1_position="Top view upper-left",
        pin_numbering="Top view counter-clockwise",
        recommended_land_pattern=manufacturer_land(0.32, 0.70),
        solder_mask_expansion=RuleStatus(status="company_rule", value={"expansion_mm": 0.05}),
        paste_mask_rule=RuleStatus(status="company_rule", value={"scale": 1.0}),
        courtyard=RuleStatus(status="company_rule", value={"clearance_mm": 0.25}),
        assembly_outline=RuleStatus(status="calculated", value={"shape": "body_rectangle"}),
    )
