from synthetic_spec import synthetic_spec
from datasheet_to_allegro.industry_defaults import apply_staging_manufacturing_profile
from datasheet_to_allegro.models import Measurement, RuleStatus
from datasheet_to_allegro.reference_defaults import apply_reference_fallback

def mm(value):
    return Measurement(value_mm=value, original_value=str(value), original_unit='mm', evidence=[])

def sample(pitch=0.35):
    spec = synthetic_spec("BGA")
    return spec.model_copy(update=dict(package_type='BGA', pitch=mm(pitch), pitch_x=None, pitch_y=None,
        recommended_land_pattern=RuleStatus(status='missing'), solder_mask_expansion=RuleStatus(status='missing'), paste_mask_rule=RuleStatus(status='missing')))

def test_board_default_is_conditional_and_review_only():
    source = sample()
    result = apply_reference_fallback(source, source)
    assert result.recommended_land_pattern.value['pad_width_mm'] == 0.22
    assert result.solder_mask_expansion.value['expansion_mm'] == 0.04
    assert result.status == 'draft'
    assert result.recommended_land_pattern.source_tier == 'reference_design'
    assert result.row_names == source.row_names
    assert any(w.code == 'BOARD_REFERENCE_DEFAULT_APPLIED' for w in result.unresolved_items)

def test_manufacturer_and_explicit_engineer_settings_win():
    for status in ('manufacturer_recommended','company_rule'):
        source = sample().model_copy(update={'solder_mask_expansion':RuleStatus(status=status,value={'expansion_mm':0.025})})
        assert apply_reference_fallback(source, sample()).solder_mask_expansion.value == {'expansion_mm':0.025}

def test_other_pitch_and_mixed_axis_not_matched():
    for source in (sample(0.4), sample().model_copy(update={'pitch_y':mm(0.4)})):
        assert apply_reference_fallback(source,source).recommended_land_pattern.value is None

def test_production_entry_uses_reference_default():
    source = sample()
    result = apply_staging_manufacturing_profile(source)
    assert result.solder_mask_expansion.value['expansion_mm'] == 0.04
    assert result.pin_count == source.pin_count
