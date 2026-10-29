from copy import deepcopy
from pathlib import Path
import pytest
from datasheet_to_allegro.dimension_graph import bind_vector_dimensions, bind_rectangle_spans
from datasheet_to_allegro.perimeter_topology import oriented_words
from datasheet_to_allegro.dimension_graph import read_vector_land_patterns


def scene(value):
    words = [dict(text=value,x0=15,x1=25,top=2,bottom=8,glyph_direction="ltr")]
    curves = [
        dict(fill=True,pts=[(13,9),(13,11),(10,10),(13,9)]),
        dict(fill=True,pts=[(27,9),(27,11),(30,10),(27,9)]),
    ]
    lines = [dict(pts=[(13,10),(27,10)]),
             dict(pts=[(10,10),(10,20)]),dict(pts=[(30,10),(30,20)])]
    rectangles = [dict(x0=10,x1=30,top=20,bottom=50)]
    return words,lines,curves,rectangles


@pytest.mark.parametrize("value", ["0.45","0.63"])
def test_arrow_extension_lines_bind_numeric_label_not_pixel_scale(value):
    words,lines,curves,rectangles=scene(value)
    dims=bind_vector_dimensions(words,lines,curves)
    bound=bind_rectangle_spans(dims,rectangles,lines)
    assert len(bound)==1 and bound[0]["value"]==float(value)
    assert bound[0]["rectangle_bbox"]==[10,20,30,50]
    assert bind_rectangle_spans(dims,rectangles,lines[:2])==[]


def test_conflicting_label_disconnected_arrow_and_tolerance_are_not_values():
    words,lines,curves,_=scene("0.45")
    assert not bind_vector_dimensions(words+[dict(words[0],text="0.55")],lines,curves)
    assert not bind_vector_dimensions(words,lines[1:],curves)
    assert not bind_vector_dimensions(words+[dict(words[0],text="±",x0=26,x1=29)],lines,curves)


@pytest.mark.parametrize('value', ['0.18', '0.27'])
def test_opt_in_unidirectional_labels_keep_ambiguity_and_connectivity(value):
    words, lines, curves, _ = scene(value)
    words[0].update(top=15, bottom=21)
    assert not bind_vector_dimensions(words, lines, curves)
    dims = bind_vector_dimensions(words, lines, curves, unidirectional=True)
    assert len(dims) == 1 and dims[0]['value'] == float(value)
    assert not bind_vector_dimensions(words + [dict(words[0], text='0.99')], lines, curves, unidirectional=True)
    assert not bind_vector_dimensions(words, lines[1:], curves, unidirectional=True)
    assert not bind_vector_dimensions(words + [dict(words[0], text='±', x0=26, x1=29)], lines, curves, unidirectional=True)


@pytest.mark.parametrize('value', ['0.36','1.47'])
def test_horizontal_text_on_vertical_dimension_is_not_identity_dependent(value):
    words,lines,curves,rectangles=scene(value)
    for obj in lines+curves:
        obj['pts']=[(y,x) for x,y in obj['pts']]
    words=[dict(text=value,x0=12,x1=28,top=16,bottom=24,glyph_direction='ltr')]
    dims=bind_vector_dimensions(words,lines,curves)
    assert len(dims)==1 and dims[0]['axis']=='y' and dims[0]['value']==float(value)
    # A dimension label is not automatically a copper size: both witnesses
    # must still reach the same physical rectangle.
    rectangles=[dict(x0=20,x1=50,top=10,bottom=30)]
    assert len(bind_rectangle_spans(dims,rectangles,lines))==1
    assert not bind_rectangle_spans(dims,rectangles,lines[:2])
    assert not bind_vector_dimensions(words+[dict(words[0],text='2.99')],lines,curves)
    assert not bind_vector_dimensions(words,lines[1:],curves)
    assert not bind_vector_dimensions([dict(words[0],x0=70,x1=86)],lines,curves)


def test_horizontal_label_above_vertical_arrow_and_sign_conflict():
    _,lines,curves,_=scene('1.47')
    for obj in lines+curves:
        obj['pts']=[(y,x) for x,y in obj['pts']]
    words=[dict(text='1.47',x0=2,x1=18,top=-1,bottom=7,glyph_direction='ltr')]
    assert len(bind_vector_dimensions(words,lines,curves))==1
    assert not bind_vector_dimensions(words+[dict(words[0],text='±',x0=19,x1=23)],lines,curves)




