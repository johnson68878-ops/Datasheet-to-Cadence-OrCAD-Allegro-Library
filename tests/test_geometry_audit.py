from datasheet_to_allegro.audit import audit_geometry, dimension_annotations
from datasheet_to_allegro.geometry import build_geometry
from datasheet_to_allegro.industry_defaults import apply_staging_manufacturing_profile
from datasheet_to_allegro.models import RuleStatus

from test_geometry_and_skill import measurement, qfn_spec


def test_geometry_audit_compares_actual_pads_to_spec_ir():
    spec = qfn_spec()
    findings = audit_geometry(spec, build_geometry(spec))
    assert findings
    assert not [item for item in findings if item.status == "fail"]
    assert {item.code for item in findings} >= {"PAD_COUNT", "PAD_NUMBER_UNIQUE", "LAND_SIZE", "PIN1_PRESENT"}


def test_dimension_annotations_preserve_datasheet_ranges_and_rule_origin():
    ranged = measurement(4.0).model_copy(update={"value_mm": None, "min_mm": 3.9, "max_mm": 4.1, "original_value": "3.9 / 4.1"})
    spec = qfn_spec().model_copy(update={"body_width": ranged})
    labels = dimension_annotations(spec, build_geometry(spec))
    assert "D=3.9..4.1 mm" in labels
    assert any(item.startswith("PCB LAND=0.32x0.7 mm MFR P") for item in labels)


def test_die_size_array_does_not_label_copper_envelope_as_body_dimension():
    raw = qfn_spec().model_copy(update={
        "package_type": "BGA",
        "pin_count": 4,
        "body_width": None,
        "body_length": None,
        "row_names": ["A", "B"],
        "column_count": 2,
        "pitch_x": measurement(0.4),
        "pitch_y": measurement(0.4),
        "terminal_width_min": measurement(0.25),
        "terminal_width_max": measurement(0.25),
        "recommended_land_pattern": RuleStatus(status="manufacturer_recommended", value={"pad_width_mm": 0.23, "pad_length_mm": 0.23}),
    })
    spec = apply_staging_manufacturing_profile(raw)
    labels = dimension_annotations(spec, build_geometry(spec))
    assert "D/E=UNRESOLVED - NO PACKAGE BODY OUTLINE DRAWN" in labels
    assert not any(label.startswith("D=0.63") or label.startswith("E=0.63") for label in labels)
