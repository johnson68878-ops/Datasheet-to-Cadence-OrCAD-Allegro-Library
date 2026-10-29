"""Explicit test inputs, never imported by the product or used as PDF answers."""
from datasheet_to_allegro.models import FootprintSpec, Measurement, RuleStatus, SourceEvidence
from datasheet_to_allegro.industry_defaults import apply_staging_manufacturing_profile


def synthetic_spec(family="QFN"):
    if family not in {"QFN", "BGA"}:
        raise ValueError("Define the synthetic geometry explicitly for a new test family")
    def mm(value):
        return Measurement(value_mm=value, original_value=str(value), original_unit="mm",
            evidence=[SourceEvidence(page=1, figure_or_table="SYNTHETIC TEST INPUT",
                raw_text=str(value), extraction_method="table_extract", confidence=1.0)])
    spec = FootprintSpec(
        part_number="SYNTHETIC_TEST", manufacturer="TEST", source_file="synthetic.pdf",
        package_type=family, pin_count=8, view_convention="component_top",
        body_width=mm(5), body_length=mm(5), package_height=mm(1),
        pitch=mm(.5), terminal_width_min=mm(.3 if family == "BGA" else .25),
        terminal_length_min=mm(.5), side_pin_counts=[2, 2, 2, 2] if family == "QFN" else [],
        row_names=["A", "B"] if family == "BGA" else [], column_count=4 if family == "BGA" else None,
        pin_1_position="Top view upper left", pin_numbering="Top view counterclockwise / row-column",
        recommended_land_pattern=RuleStatus(status="missing"),
        solder_mask_expansion=RuleStatus(status="missing"), paste_mask_rule=RuleStatus(status="missing"),
        courtyard=RuleStatus(status="missing"), assembly_outline=RuleStatus(status="calculated"),
    )
    return apply_staging_manufacturing_profile(spec)
