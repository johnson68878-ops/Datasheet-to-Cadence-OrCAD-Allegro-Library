import math
import pytest
from datasheet_to_allegro.native_polygon import native_polygon


def test_quadrant_circle_area_and_native_crosscheck():
    v='(((1 0) -1) ((0 1) -1) ((-1 0) 1) ((0 -1) 1))'
    p=native_polygon(v,'nil',native_area=math.pi)
    assert p is not None
    assert p.area==pytest.approx(math.pi,abs=.0001)
    assert native_polygon(v,'nil',native_area=4) is None


def test_void_subtracted_and_units_scaled():
    v='(((0 0) 0) ((4 0) 0) ((4 4) 0) ((0 4) 0))'
    h='((((1 1) 0) ((2 1) 0) ((2 2) 0) ((1 2) 0)))'
    p=native_polygon(v,h,.0254,15)
    assert p.area==pytest.approx(15*.0254**2)
    assert len(p.interiors)==1
    assert native_polygon(v,'(poly:12345)') is None


