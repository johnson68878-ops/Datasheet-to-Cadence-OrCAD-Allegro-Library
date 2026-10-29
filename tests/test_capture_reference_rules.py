from datasheet_to_allegro.capture_reference_rules import compare_attribute_maps, review_instance_identity

def test_library_design_scope_differences_are_not_overwritten():
    key = ('PROPERTY','package:GENERIC','PCB Footprint')
    result = compare_attribute_maps({key:'C0805'}, {key:''})
    assert result['differences'][0]['library'] == 'C0805'
    assert result['differences'][0]['design'] == ''
    assert result['policy'] == 'review_only_no_automatic_override'

def test_case_distinct_attributes_and_missing_identity_preserved():
    a = {('PROPERTY','instance:R1','Room'):'A', ('PROPERTY','instance:R1','ROOM'):'B'}
    assert compare_attribute_maps(a,a)['matched_fields'] == 2
    assert review_instance_identity({'Value':'Heatsink','PCB Footprint':''}) == ['FOOTPRINT_MISSING']
    assert review_instance_identity({'Value':'IC','PCB Footprint':'12'}) == ['FOOTPRINT_NUMERIC_REVIEW']
