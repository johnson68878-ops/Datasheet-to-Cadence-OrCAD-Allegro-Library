"""Cadence polygon vertices: radius belongs to previous->current edge.

Cadence 17.4 algroskill/20plyopr.html: arcs never cross quadrants;
positive radius is left of center, negative is right of center.
Tessellation has an explicit maximum sagitta, checked against native area.
"""
import math
import re
from shapely.geometry import Polygon


def sexpr(text):
    tokens=iter(re.findall(r'\(|\)|[^\s()]+',text))
    def read(token):
        if token=='(':
            items=[]
            for t in tokens:
                if t==')': return items
                items.append(read(t))
            raise ValueError('Unclosed list')
        if token=='nil': return []
        return float(token)
    result=read(next(tokens))
    if next(tokens,None) is not None: raise ValueError('Trailing data')
    return result


def ring(vertices, scale, tolerance):
    result=[]
    for i,(point,radius) in enumerate(vertices):
        q=[v*scale for v in point]
        p=[v*scale for v in vertices[i-1][0]]
        r=radius*scale
        if not r:
            result.append(q); continue
        dx,dy=q[0]-p[0],q[1]-p[1]
        chord=math.hypot(dx,dy)
        if chord==0 or chord>abs(r)*math.sqrt(2)+1e-4:
            raise ValueError('Invalid quadrant arc')
        h=math.sqrt(max(0,r*r-chord*chord/4))
        candidates=[]
        for sign in (-1,1):
            cx=(p[0]+q[0])/2-sign*dy*h/chord
            cy=(p[1]+q[1])/2+sign*dx*h/chord
            a=math.atan2(p[1]-cy,p[0]-cx)
            b=math.atan2(q[1]-cy,q[0]-cx)
            sweep=(b-a+math.pi)%(2*math.pi)-math.pi
            mid=math.cos(a+sweep/2)
            if (r>0 and mid<0) or (r<0 and mid>0):
                candidates.append((cx,cy,a,sweep))
        if len(candidates)!=1: raise ValueError('Ambiguous arc center')
        cx,cy,a,sweep=candidates[0]
        step=2*math.acos(max(-1,1-min(tolerance/abs(r),1)))
        n=max(1,math.ceil(abs(sweep)/step))
        for j in range(1,n+1):
            angle=a+sweep*j/n
            result.append((cx+abs(r)*math.cos(angle),cy+abs(r)*math.sin(angle)))
    return result


def native_polygon(vertices, holes, scale=1, native_area=None, tolerance=.00001):
    try:
        outer=ring(sexpr(vertices),scale,tolerance)
        inner=[ring(h,scale,tolerance) for h in sexpr(holes)]
        poly=Polygon(outer,inner)
        if not poly.is_valid or poly.area<=0: return None
        # Native area is an independent Cadence result, not a bbox estimate.
        if native_area is not None:
            expected=abs(float(native_area))*scale*scale
            if abs(poly.area-expected)>max(1e-6,poly.length*tolerance*2): return None
        return poly
    except (ValueError,TypeError,IndexError,StopIteration):
        return None
