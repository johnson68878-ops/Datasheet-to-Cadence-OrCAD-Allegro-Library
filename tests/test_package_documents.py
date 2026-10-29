from types import SimpleNamespace
from datasheet_to_allegro.package_documents import reference_pair, compatible


def test_reference_pair_requires_one_explicit_pair():
    assert reference_pair('Package Code OTHER+7\nOutline Number 12-3456') == ('OTHER+7','12-3456')
    assert reference_pair('Package Code OTHER+7\nOutline Number 12-3456\nPackage Code SECOND\nOutline Number 12-9999') is None


def test_physical_conflicts_reject_matching_family():
    def spec(value):
        m=SimpleNamespace(value_mm=value,nominal_mm=None,min_mm=None,max_mm=None)
        return SimpleNamespace(package_type='WLCSP',pin_count=9,row_names=['A','B','C'],column_count=3,missing_positions=[],view_convention='component_top',body_width=m,body_length=m,pitch=m,pitch_x=m,pitch_y=m,package_height=m,terminal_width_min=None)
    assert compatible(spec(.4),spec(.4))
    assert not compatible(spec(.4),spec(.5))
    for field,value in [('pin_count',8),('column_count',4),('row_names',['A','B']),('missing_positions',['A1']),('view_convention','terminal_bottom'),('package_type','QFN')]:
        other=spec(.4)
        setattr(other,field,value)
        assert not compatible(spec(.4),other)
    incomplete=spec(.4)
    incomplete.pitch=None
    assert not compatible(incomplete,spec(.4))
