"""Circle-role evidence from PDF vectors; never infer millimetres from scale."""
import math


def circle_outlines(curves):
    """Accept four canonical cubic quarter arcs, not merely a square bbox."""
    result = []
    for curve in curves:
        if not curve.get('stroke'):
            continue
        x0, x1, y0, y1 = (curve[k] for k in ('x0','x1','top','bottom'))
        radius = (x1-x0)/2
        if radius <= 0 or abs((y1-y0)/2-radius) > .05:
            continue
        center = ((x0+x1)/2,(y0+y1)/2)
        path = curve.get('path', [])
        if not path or path[0][0] != 'm':
            continue
        current = path[0][1]
        arcs = [p for p in path[1:] if p[0] != 'h']
        if len(arcs) != 4 or any(p[0] != 'c' for p in arcs):
            continue
        valid = True
        winding = None
        for _, first, second, end in arcs:
            a = ((current[0]-center[0])/radius,(current[1]-center[1])/radius)
            b = ((end[0]-center[0])/radius,(end[1]-center[1])/radius)
            if (abs(math.hypot(*a)-1) > .002 or abs(math.hypot(*b)-1) > .002
                    or abs(a[0]*b[0]+a[1]*b[1]) > .002):
                valid = False; break
            turn = a[0]*b[1]-a[1]*b[0]
            if winding is not None and turn*winding <= 0:
                valid = False; break
            winding = turn
            k = .55228475
            expected_first = (current[0]+k*radius*b[0],current[1]+k*radius*b[1])
            expected_second = (end[0]+k*radius*a[0],end[1]+k*radius*a[1])
            if max(abs(v-w) for p,q in ((first,expected_first),(second,expected_second)) for v,w in zip(p,q)) > .05:
                valid = False; break
            current = end
        if valid and math.dist(current,path[0][1]) < .05:
            result.append(curve)
    return result


def bind_circle_pitch(dimensions, circles, lines):
    """Bind two dimension witnesses to adjacent circle centers, retaining paths.

    A small arrow-to-witness drafting gap is bounded by half the actual arrow
    length. Circle-to-witness alignment is bounded separately by source ink.
    No such gap is used to move circles or derive physical dimensions.
    """
    result = []
    for dim in dimensions:
        axis = 0 if dim['axis']=='x' else 1
        lengths = dim.get('arrow_lengths_pt',[])
        if len(lengths)!=2:
            continue
        centers = [((c['x0']+c['x1'])/2,(c['top']+c['bottom'])/2) for c in circles]
        ends = []
        for tip, arrow_length in zip(dim['span'],lengths):
            paths = []
            for line in lines:
                pts, lw = line.get('pts',[]),line.get('linewidth',0)
                if len(pts)!=2 or not isinstance(lw,(int,float)) or not math.isfinite(lw) or lw<=0:
                    continue
                if abs(pts[0][axis]-pts[1][axis])>.15:
                    continue
                gap = abs(pts[0][axis]-tip)
                if gap > arrow_length/2+lw/2 or gap > (dim['span'][1]-dim['span'][0])*.05:
                    continue
                low,high = sorted(p[1-axis] for p in pts)
                if not low-lw/2<=dim['level']<=high+lw/2:
                    continue
                touched = []
                for i,(circle,center) in enumerate(zip(circles,centers)):
                    cw = circle.get('linewidth',0)
                    if not isinstance(cw,(int,float)) or not math.isfinite(cw) or cw<=0:
                        continue
                    if abs(center[axis]-pts[0][axis]) <= (cw+lw)/2+.15 and low-lw/2<=center[1-axis]<=high+lw/2:
                        touched.append(i)
                if touched:
                    paths.append({'points':pts,'linewidth':lw,'circle_indices':touched,'arrow_clearance_pt':gap})
            if len(paths)!=1:
                break
            ends.append(paths[0])
        if len(ends)!=2 or ends[0]['points']==ends[1]['points']:
            continue
        pairs = []
        for i in ends[0]['circle_indices']:
            for j in ends[1]['circle_indices']:
                a,b = centers[i],centers[j]
                limit = (circles[i]['linewidth']+circles[j]['linewidth'])/2+.15
                if i==j or b[axis]<=a[axis] or abs(a[1-axis]-b[1-axis])>limit:
                    continue
                if any(a[axis]<c[axis]<b[axis] and abs(c[1-axis]-a[1-axis])<=limit for n,c in enumerate(centers) if n not in (i,j)):
                    continue  # total span is not adjacent pitch
                pairs.append([i,j])
        if pairs:
            result.append({**dim,'role':'adjacent_circle_pitch','center_pairs':[[centers[i],centers[j]] for i,j in pairs],
                           'witness_evidence':ends})
    return result


def bind_circle_diameters(dimensions, circles, lines):
    """Require two ink-bounded witness lines reaching the same circle's extrema."""
    result = []
    for dim in dimensions:
        axis = 0 if dim['axis'] == 'x' else 1
        lo, hi = ('x0','x1') if axis == 0 else ('top','bottom')
        cross_lo, cross_hi = ('top','bottom') if axis == 0 else ('x0','x1')
        candidates = []
        for circle in circles:
            width = circle.get('linewidth',0)
            if not isinstance(width,(int,float)) or not math.isfinite(width) or width <= 0:
                continue
            if abs(circle['x1']-circle['x0']-circle['bottom']+circle['top']) > .1:
                continue
            center = (circle[cross_lo]+circle[cross_hi])/2
            if circle[cross_lo] <= dim['level'] <= circle[cross_hi]:
                continue
            witnesses = []
            for edge, tip in zip((circle[lo],circle[hi]),dim['span']):
                paths = []
                for line in lines:
                    pts, lw = line.get('pts',[]), line.get('linewidth',0)
                    if len(pts)!=2 or not isinstance(lw,(int,float)) or not math.isfinite(lw) or lw<=0:
                        continue
                    a,b = sorted(p[1-axis] for p in pts)
                    if (abs(pts[0][axis]-pts[1][axis]) <= .15
                            and max(abs(p[axis]-edge) for p in pts) <= (width+lw)/2+.15
                            and max(abs(p[axis]-tip) for p in pts) <= lw+.15
                            and a-lw/2 <= center <= b+lw/2
                            and a-lw/2 <= dim['level'] <= b+lw/2):
                        paths.append({'points':pts,'linewidth':lw})
                if len(paths)!=1:
                    break
                witnesses.append(paths[0])
            if len(witnesses)==2:
                candidates.append({**dim,'role':'circle_diameter',
                    'circle_bbox':[circle[k] for k in ('x0','top','x1','bottom')],
                    'witness_evidence':witnesses})
        if len(candidates)==1:
            result.append(candidates[0])
    return result
