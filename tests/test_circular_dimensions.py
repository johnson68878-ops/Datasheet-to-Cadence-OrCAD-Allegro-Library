from datasheet_to_allegro.circular_dimensions import bind_circle_diameters, bind_circle_pitch, circle_outlines


def test_diameter_requires_two_witnesses_to_one_circle():
    circle = dict(x0=10, x1=30, top=20, bottom=40, linewidth=.75)
    dim = dict(axis='x', span=[10,30], level=50, value=.18)
    lines = [dict(pts=[(10,30),(10,52)],linewidth=.75),
             dict(pts=[(30,30),(30,52)],linewidth=.75)]
    assert bind_circle_diameters([dim],[circle],lines)[0]['value'] == .18
    assert not bind_circle_diameters([dim],[circle],lines[:1])
    assert not bind_circle_diameters([dim],[circle,dict(circle,top=24,bottom=44)],lines)
    assert not bind_circle_diameters([dict(dim,span=[12,28])],[circle],lines)
    assert not bind_circle_diameters([dim],[dict(circle,x1=40)],lines)


def test_center_pitch_needs_witnesses_not_numeric_scale():
    circles = [dict(x0=x-4,x1=x+4,top=20,bottom=28,linewidth=.75) for x in (10,30)]
    dim = dict(axis='x',span=[10,30],level=10,value=.41,arrow_lengths_pt=[3,3])
    lines = [dict(pts=[(x,8),(x,24)],linewidth=.75) for x in (10,30)]
    assert bind_circle_pitch([dim],circles,lines)[0]['value'] == .41
    assert not bind_circle_pitch([dim],circles,lines[:1])
    assert not bind_circle_pitch([dict(dim,span=[14,26])],circles,lines)
    assert not bind_circle_pitch([dim],[circles[0],dict(circles[1],top=35,bottom=43)],lines)
    middle = dict(circles[0],x0=16,x1=24)
    assert not bind_circle_pitch([dim],circles+[middle],lines)


def test_cubic_circle_not_square_bbox_or_distorted_curve():
    from copy import deepcopy
    k = .55228475
    c = dict(x0=-1,x1=1,top=-1,bottom=1,stroke=True,linewidth=.75,
             path=[['m',(1,0)],['c',(1,k),(k,1),(0,1)],
                   ['c',(-k,1),(-1,k),(-1,0)],['c',(-1,-k),(-k,-1),(0,-1)],
                   ['c',(k,-1),(1,-k),(1,0)]])
    assert circle_outlines([c]) == [c]
    bad = deepcopy(c); bad['path'][1][1] = (1,.8)
    assert not circle_outlines([bad])
    assert not circle_outlines([dict(c,path=[['m',(1,0)],['l',(0,1)]])])
    doubled = dict(c,path=[['m',(1,0)],['c',(1,k),(k,1),(0,1)],
                          ['c',(k,1),(1,k),(1,0)],['c',(1,k),(k,1),(0,1)],
                          ['c',(k,1),(1,k),(1,0)]])
    assert not circle_outlines([doubled])


