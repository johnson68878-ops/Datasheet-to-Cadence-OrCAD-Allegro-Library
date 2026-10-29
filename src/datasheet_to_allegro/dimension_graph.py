"""Bind scalar labels to vector dimension arrows, retaining raw coordinates.

This module never scales drawing pixels into millimetres. Numeric values come
only from printed labels; geometry is used solely to identify their endpoints.
Unsupported/broken arrow chains and ambiguous labels produce no binding.
"""
import itertools
import math
import re


def _arrows(curves):
    arrows = []
    for curve in curves:
        if not curve.get("fill"):
            continue
        points = list(dict.fromkeys((round(x, 3), round(y, 3)) for x, y in curve.get("pts", [])))
        if len(points) != 3:
            continue
        for axis in (0, 1):
            for base in itertools.combinations(points, 2):
                tip = next(p for p in points if p not in base)
                if abs(base[0][axis]-base[1][axis]) > .05:
                    continue
                if abs(tip[1-axis] - (base[0][1-axis]+base[1][1-axis])/2) > .1:
                    continue
                if abs(tip[axis]-base[0][axis]) < 1:
                    continue
                arrows.append({"axis": axis, "tip": tip,
                               "interval": sorted([tip[axis], base[0][axis]])})
    return arrows


def bind_vector_dimensions(words, lines, curves, *, unidirectional=False):
    """Return unambiguous label + arrow-tip pairs (in top-origin PDF points)."""
    arrows = _arrows(curves)
    bindings = []
    for first, second in itertools.combinations(arrows, 2):
        axis = first["axis"]
        if second["axis"] != axis:
            continue
        level = (first["tip"][1-axis]+second["tip"][1-axis])/2
        if abs(first["tip"][1-axis]-second["tip"][1-axis]) > .15:
            continue
        low, high = sorted([first["tip"][axis], second["tip"][axis]])
        if high-low < 1 or any(
            other not in (first, second) and other["axis"] == axis
            and abs(other["tip"][1-axis]-level) < .15
            and low+.15 < other["tip"][axis] < high-.15 for other in arrows
        ):
            continue
        segments = []
        segment_evidence = []
        for line in lines:
            points = line.get("pts", [])
            width = line.get('linewidth', 0)
            ink = width/2 if isinstance(width,(int,float)) and math.isfinite(width) and width>0 else 0
            alignment_limit = .15 + min(ink, (high-low)*.02)
            if len(points) != 2 or any(abs(p[1-axis]-level) > alignment_limit for p in points):
                continue
            segments.append(sorted([p[axis] for p in points]))
            segment_evidence.append({'points':points,'linewidth':width,
                                     'alignment_limit_pt':alignment_limit})
        # Inward arrows replace the short tip-to-base portions of the line.
        # Still require a line segment inside the span and a connected chain.
        if not any(a < high-.15 and b > low+.15 for a,b in segments):
            continue
        intervals = sorted(segments + [first["interval"], second["interval"]])
        merged = []
        for a,b in intervals:
            if merged and a <= merged[-1][1]+.15:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a,b])
        connected = next((v for v in merged if v[0] <= low+.15 and v[1] >= high-.15), None)
        gap_label = None
        if connected is None:
            # A deliberate text break needs two line-connected arrow halves
            # and exactly one numeric box covering the only gap. Arbitrary
            # broken lines must never be bridged by a nearby label.
            halves = [v for v in merged if v[1]>=low-.15 and v[0]<=high+.15]
            if len(halves)==2 and halves[0][0]<=low+.15 and halves[1][1]>=high-.15:
                a,b=halves[0][1],halves[1][0]
                candidates=[]
                for word in words:
                    if not re.fullmatch(r'\d+(?:\.\d+)?',word['text']):
                        continue
                    if word.get('glyph_direction','ltr')!='ltr' or axis!=0:
                        continue
                    height=word['bottom']-word['top']
                    if (height>0 and word['top']<=level<=word['bottom']
                            and word['x0']-height*.25<=a<b<=word['x1']+height*.25
                            and word['x0']<=b and word['x1']>=a):
                        candidates.append(word)
                if len(candidates)==1:
                    gap_label=candidates[0]
                    connected=[halves[0][0],halves[1][1]]
        if connected is None:
            continue
        labels = []
        for word in words:
            if not re.fullmatch(r"\d+(?:\.\d+)?", word["text"]):
                continue
            direction = word.get("glyph_direction", "ltr")
            if unidirectional and direction == 'ltr':
                # Optional candidate channel for role-aware callers. This only
                # binds text to an intact arrow chain, never to a pad itself.
                height = word['bottom']-word['top']
                parallel = ((word['x0']+word['x1'])/2 if axis == 0
                            else (word['top']+word['bottom'])/2)
                gap = (max(word['top']-level, level-word['bottom'], 0) if axis == 0
                       else max(word['x0']-level, level-word['x1'], 0))
                if height > 0 and low <= parallel <= high and gap <= height*2:
                    labels.append(word)
                    continue
            if axis==0 and direction=='ltr':
                height=word['bottom']-word['top']
                center=(word['x0']+word['x1'])/2
                crosses_baseline=word['top']<=level<=word['bottom']
                outward=(min(first['interval'][0],second['interval'][0])<low-.15
                         and max(first['interval'][1],second['interval'][1])>high+.15)
                outside=(0<=word['x0']-connected[1]<=height
                         or 0<=connected[0]-word['x1']<=height)
                if height>0 and crosses_baseline and (
                        connected[0]<=center<=connected[1] or outward and outside):
                    labels.append(word)
                    continue
            if axis == 1 and direction == 'ltr':
                # Unidirectional dimensioning keeps text horizontal even for
                # vertical dimensions. Require proximity to the connected
                # dimension line, not simply the nearest numeric word.
                height = word['bottom']-word['top']
                center = (word['top']+word['bottom'])/2
                lateral_gap = max(word['x0']-level, level-word['x1'], 0)
                beside = (lateral_gap <= height*.6
                          and low-height*.5 <= center <= high+height*.5)
                above = (word['x0'] <= level <= word['x1']
                         and 0 <= low-word['bottom'] <= height
                         and connected[0]-word['bottom'] <= height)
                if height > 0 and (beside or above):
                    labels.append(word)
                continue
            if (axis == 0) != (direction == "ltr"):
                continue
            # Use the upper side of the text baseline in its actual reading
            # direction; a nearby pin number below a horizontal dimension
            # line is not its value.
            if axis == 0 and (word["top"]+word["bottom"])/2 >= level:
                continue
            if axis == 1 and direction == "btt" and (word["x0"]+word["x1"])/2 >= level:
                continue
            if axis == 1 and direction == "ttb" and (word["x0"]+word["x1"])/2 <= level:
                continue
            x, y = (word["x0"]+word["x1"])/2, (word["top"]+word["bottom"])/2
            parallel, perpendicular = (x,y) if axis == 0 else (y,x)
            height = (word["bottom"]-word["top"]) if axis == 0 else (word["x1"]-word["x0"])
            if abs(perpendicular-level) <= height*1.2 and connected[0] <= parallel <= connected[1]:
                labels.append(word)
        if len(labels) != 1 or float(labels[0]["text"]) <= 0:
            continue
        label = labels[0]
        if gap_label is not None and label is not gap_label:
            continue
        nearby_text = [
            w["text"] for w in words if w is not label
            and abs((w["top"]+w["bottom"]-label["top"]-label["bottom"])/2)
            < max(w["bottom"]-w["top"], label["bottom"]-label["top"])*.6
            and w["x0"] <= label["x1"]+12 and w["x1"] >= label["x0"]-12
        ]
        if any(re.search(r"[±−+]|\+/-|^-", text) for text in nearby_text):
            continue  # Tolerance/sign topology needs a separate grammar.
        qualifiers = [text for text in nearby_text if re.fullmatch(r"REF|BSC|TYP", text, re.I)]
        bindings.append({
            "axis": "x" if axis == 0 else "y",
            "value": float(label["text"]), "raw_text": " ".join([label["text"], *qualifiers]),
            "qualifiers": qualifiers,
            "label_direction": label.get('glyph_direction','ltr'),
            "association": "connected_arrow_pair_unique_label",
            "dimension_line_evidence": segment_evidence,
            "text_gap_bridged": gap_label is not None,
            "label_bbox": [label["x0"], label["top"], label["x1"], label["bottom"]],
            "tips": [first["tip"], second["tip"]],
            "span": [low,high], "level": level,
            "arrow_lengths_pt": [b-a for a,b in sorted([first['interval'],second['interval']])],
        })
    # One scalar cannot silently supply two nearby dimension pairs.
    return [binding for binding in bindings if sum(
        other['label_bbox'] == binding['label_bbox'] for other in bindings) == 1]


def bind_rectangle_spans(dimensions, rectangles, lines):
    """Associate endpoints only when both extension lines reach the same rect."""
    matches = []
    for dimension in dimensions:
        exact_matches = []
        axis = 0 if dimension["axis"] == "x" else 1
        lo_key, hi_key = ("x0","x1") if axis == 0 else ("top","bottom")
        cross_lo, cross_hi = ("top","bottom") if axis == 0 else ("x0","x1")
        for rect in rectangles:
            if max(abs(rect[lo_key]-dimension["span"][0]),
                   abs(rect[hi_key]-dimension["span"][1])) > .15:
                continue
            reaches = []
            for endpoint in dimension["span"]:
                reaches.append(any(
                    len(line.get("pts", [])) == 2
                    and all(abs(p[axis]-endpoint) <= .15 for p in line["pts"])
                    and min(p[1-axis] for p in line["pts"]) <= dimension["level"]+.15
                    and max(p[1-axis] for p in line["pts"]) >= dimension["level"]-.15
                    and max(p[1-axis] for p in line["pts"]) >= rect[cross_lo]-.15
                    and min(p[1-axis] for p in line["pts"]) <= rect[cross_hi]+.15
                    for line in lines
                ))
            if all(reaches):
                exact_matches.append({**dimension,
                                "rectangle_bbox": [rect["x0"],rect["top"],rect["x1"],rect["bottom"]]})
        if exact_matches:
            matches.extend(exact_matches)
        else:
            matches.extend(_bind_stroked_rectangle(dimension, rectangles, lines))
    return matches


def _bind_stroked_rectangle(dimension, rectangles, lines):
    """Resolve witness endpoints using source stroke widths, not a larger epsilon.

    A drafting clearance is allowed only at the rectangle-facing line end,
    bounded by both combined ink widths and one quarter of the short edge.
    Competing targets or multiple witness paths remain unresolved.
    """
    import math
    axis = 0 if dimension['axis'] == 'x' else 1
    lo, hi = ('x0','x1') if axis == 0 else ('top','bottom')
    cross_lo, cross_hi = ('top','bottom') if axis == 0 else ('x0','x1')
    candidates=[]
    for rect in rectangles:
        rw=rect.get('linewidth')
        if not isinstance(rw,(int,float)) or not math.isfinite(rw) or rw<=0:
            continue
        short=min(rect['x1']-rect['x0'],rect['bottom']-rect['top'])
        level=dimension['level']
        if level < rect[cross_lo]:
            facing=rect[cross_lo]
        elif level > rect[cross_hi]:
            facing=rect[cross_hi]
        else:
            continue  # internal/center-line dimensions need a different role
        witnesses=[]
        for edge, tip in zip((rect[lo],rect[hi]),dimension['span']):
            paths=[]
            for line in lines:
                pts=line.get('pts',[])
                lw=line.get('linewidth')
                if len(pts)!=2 or not isinstance(lw,(int,float)) or not math.isfinite(lw) or lw<=0:
                    continue
                # The source ink must support collinearity and both endpoint
                # associations. No drawing-to-mm scale is inferred.
                if abs(pts[0][axis]-pts[1][axis])>.15:
                    continue
                edge_error=max(abs(p[axis]-edge) for p in pts)
                tip_error=max(abs(p[axis]-tip) for p in pts)
                if edge_error > (rw+lw)/2+.15 or tip_error > lw/2+.15:
                    continue
                a,b=sorted(p[1-axis] for p in pts)
                if not a-lw/2-.15 <= level <= b+lw/2+.15:
                    continue
                endpoint=min((a,b),key=lambda p:abs(p-facing))
                # Only the end facing the rectangle may bridge a clearance;
                # an overlong line crossing a row of pads is not an endpoint.
                gap=abs(endpoint-facing)
                limit=min(rw+lw,short/4)
                overshoot=(endpoint-facing) if level<facing else (facing-endpoint)
                if gap>limit or overshoot>lw/2:
                    continue
                paths.append({'line_points':pts,'line_width':lw,'rectangle_line_width':rw,
                              'edge_error_pt':edge_error,'tip_error_pt':tip_error,
                              'clearance_pt':gap,'clearance_limit_pt':limit})
            if len(paths)!=1:
                break
            witnesses.append(paths[0])
        if len(witnesses)==2:
            candidates.append({**dimension,
                'rectangle_bbox':[rect['x0'],rect['top'],rect['x1'],rect['bottom']],
                'endpoint_binding':'source_stroke_bounded_witness_ends',
                'witness_evidence':witnesses})
    return candidates if len(candidates)==1 else []


def bind_envelope_spans(dimensions, rectangles, lines):
    """Bind an outer span to actual extreme pads, not virtual box corners.

    Caller must supply a single, role-identified package's pads. Each witness
    must end at one unique extreme pad, with source-stroke-bounded clearance.
    Internal dimensions and through-lines cannot establish an outer span.
    """
    if not rectangles:
        return []
    result = []
    envelope = [min(r['x0'] for r in rectangles), min(r['top'] for r in rectangles),
                max(r['x1'] for r in rectangles), max(r['bottom'] for r in rectangles)]
    for dimension in dimensions:
        axis = 0 if dimension['axis'] == 'x' else 1
        cross_lo, cross_hi = ('top', 'bottom') if axis == 0 else ('x0', 'x1')
        edge_keys = ('x0', 'x1') if axis == 0 else ('top', 'bottom')
        level = dimension['level']
        if envelope[1-axis] <= level <= envelope[3-axis]:
            continue
        ends = []
        for end, key in enumerate(edge_keys):
            edge = envelope[axis + 2*end]
            tip = dimension['span'][end]
            candidates = []
            for rect in rectangles:
                rw = rect.get('linewidth')
                if (abs(rect[key]-edge) > .15 or not isinstance(rw, (int, float))
                        or not math.isfinite(rw) or rw <= 0):
                    continue
                facing = rect[cross_lo] if level < rect[cross_lo] else rect[cross_hi]
                short = min(rect['x1']-rect['x0'], rect['bottom']-rect['top'])
                for line in lines:
                    pts, lw = line.get('pts', []), line.get('linewidth')
                    if (len(pts) != 2 or not isinstance(lw, (int, float))
                            or not math.isfinite(lw) or lw <= 0):
                        continue
                    if abs(pts[0][axis]-pts[1][axis]) > .15:
                        continue
                    near, far = sorted(pts, key=lambda p: abs(p[1-axis]-facing))
                    # Use the actual two line endpoints: slight line slant
                    # must not cause its remote end to move the pad anchor.
                    edge_error = abs(near[axis]-rect[key])
                    a, b = sorted(p[1-axis] for p in pts)
                    if not a-lw/2-.15 <= level <= b+lw/2+.15:
                        continue
                    delta = far[1-axis]-near[1-axis]
                    if abs(delta) < 1:
                        continue
                    at_level = near[axis]+(far[axis]-near[axis])*(level-near[1-axis])/delta
                    tip_error = abs(at_level-tip)
                    gap = abs(near[1-axis]-facing)
                    limit = min(rw+lw, short/4)
                    overshoot = near[1-axis]-facing if level < facing else facing-near[1-axis]
                    if (edge_error > (rw+lw)/2+.15 or tip_error > lw/2+.15
                            or gap > limit or overshoot > lw/2):
                        continue
                    candidates.append({'rectangle_bbox':[rect['x0'],rect['top'],rect['x1'],rect['bottom']],
                        'line_points':pts, 'line_width':lw, 'rectangle_line_width':rw,
                        'edge_error_pt':edge_error, 'tip_error_pt':tip_error,
                        'clearance_pt':gap, 'clearance_limit_pt':limit})
            if len(candidates) != 1:
                break
            ends.append(candidates[0])
        if len(ends) == 2 and ends[0]['line_points'] != ends[1]['line_points']:
            result.append({**dimension, 'envelope_bbox':envelope,
                           'endpoint_binding':'actual_extreme_pad_witness_ends',
                           'witness_evidence':ends})
    return result


def _hatch_directions(rect, lines):
    from statistics import median
    directions = []
    for line in lines:
        points = line.get("pts", [])
        if len(points) != 2:
            continue
        dx,dy = points[1][0]-points[0][0], points[1][1]-points[0][1]
        if abs(dx)<=1 or abs(dy)<=1:
            continue
        # Clip hatch strokes to the rectangle; PDF strokes can cross an edge.
        tx=sorted([(rect["x0"]-points[0][0])/dx,(rect["x1"]-points[0][0])/dx])
        ty=sorted([(rect["top"]-points[0][1])/dy,(rect["bottom"]-points[0][1])/dy])
        lo,hi=max(0,tx[0],ty[0]),min(1,tx[1],ty[1])
        if hi>lo and min(abs(dx),abs(dy))*(hi-lo)>1:
            directions.append(dy/dx)
    if len(directions)<2:
        return set()
    center=median(directions)
    return {round(center,1)} if all(abs(v-center)<.2 for v in directions) else {round(v,1) for v in directions}


def _rectangles(rectangles, curves, lines):
    """Include rectangles encoded as connected orthogonal polyline segments."""
    result=list(rectangles)
    segments=[line["pts"] for line in lines if len(line.get("pts",[]))==2]
    segments += [[a,b] for curve in curves for a,b in zip(curve.get("pts",[]),curve.get("pts",[])[1:])]
    for curve in curves:
        pts=curve.get("pts",[])
        if len(pts)<3 or any(abs(a[0]-b[0])>.1 and abs(a[1]-b[1])>.1 for a,b in zip(pts,pts[1:])):
            continue
        x0,x1=min(p[0] for p in pts),max(p[0] for p in pts)
        y0,y1=min(p[1] for p in pts),max(p[1] for p in pts)
        if min(x1-x0,y1-y0)<1:
            continue
        edges=[((x0,y0),(x1,y0)),((x1,y0),(x1,y1)),((x1,y1),(x0,y1)),((x0,y1),(x0,y0))]
        def covered(edge):
            for a,b in segments:
                if (all(abs(a[i]-edge[0][i])<.1 and abs(b[i]-edge[1][i])<.1 for i in (0,1))
                        or all(abs(b[i]-edge[0][i])<.1 and abs(a[i]-edge[1][i])<.1 for i in (0,1))):
                    return True
            return False
        if all(covered(edge) for edge in edges) and not any(
            max(abs(r[k]-v) for k,v in zip(("x0","top","x1","bottom"),(x0,y0,x1,y1)))<.1 for r in result
        ):
            result.append(dict(x0=x0,x1=x1,top=y0,bottom=y1))
    return result


def _numbered_ring(sides, words, radius):
    """Prove a unique cyclic side origin from all eight printed corner labels.

    Do not mirror the ring, divide a pin count by four, or invent missing labels.
    Caller supplies independently observed rectangles in one drawing scope.
    """
    count=sum(map(len,sides))
    candidates=[]
    for turn in range(4):
        rotated=sides[turn:]+sides[:turn]
        ordered=[p for side in rotated for p in side]
        expected=[]
        offset=0
        for side in rotated:
            expected.extend([offset+1,offset+len(side)])
            offset+=len(side)
        numbered={}
        for word in words:
            if not word['text'].isdigit() or int(word['text']) not in expected:
                continue
            point=((word['x0']+word['x1'])/2,(word['top']+word['bottom'])/2)
            distances=[(point[0]-x)**2+(point[1]-y)**2 for x,y in ordered]
            nearest=sorted(range(count),key=lambda i:distances[i])
            if distances[nearest[0]]>=radius**2:
                continue
            if len(nearest)>1 and abs(distances[nearest[1]]-distances[nearest[0]])<1e-6:
                return None
            numbered.setdefault(int(word['text']),[]).append(nearest[0]+1)
        if all(numbered.get(n)==[n] for n in expected):
            candidates.append((turn,rotated,numbered))
    return candidates[0] if len(candidates)==1 else None


def read_vector_land_patterns(page):
    """Read an explicitly dimensioned, legend-identified rectangular copper ring.

    The first supported drawing grammar uses repeated hatched soldering-area
    rectangles with a matching legend. Mask rectangles cannot supply copper
    dimensions. Unsupported legends/shapes remain unbound.
    """
    from collections import defaultdict
    from .perimeter_topology import oriented_words
    from .package_vocabulary import identify_package_families
    words = oriented_words(page)
    lines_by_y = defaultdict(list)
    for word in words:
        if word["glyph_direction"] == "ltr":
            lines_by_y[round(word["top"]/3)].append(word)
    captions = []
    for row in lines_by_y.values():
        row = sorted(row,key=lambda w:w["x0"])
        text = " ".join(w["text"] for w in row)
        if not re.search(r"\bfootprint\s+example\b|\brecommended\s+(?:land\s+pattern|footprint)\b", text,re.I):
            continue
        labels = []
        for word in row:
            match = re.fullmatch(r"([A-Za-z]+)[-_]?(\d+)",word["text"])
            families = set(identify_package_families(word["text"])) & {"QFP"}
            if match and len(families)==1:
                labels.append((word["text"],next(iter(families)),int(match[2])))
        if len(labels)==1:
            captions.append((min(w["top"] for w in row),max(w["bottom"] for w in row),labels[0],text))
    results = []
    for index,(_,start,(caption,family,count),caption_text) in enumerate(sorted(captions)):
        end = sorted(captions)[index+1][0] if index+1<len(captions) else float(page.height)*.95
        local_words = [w for w in words if start<w["top"] and w["bottom"]<end]
        local_text = " ".join(w["text"] for w in sorted(local_words,key=lambda w:(round(w["top"]/3),w["x0"])))
        if not re.search(r"dimensions\s+are\s+expressed\s+in\s+millimeters",local_text,re.I):
            continue
        lines = [l for l in page.lines if start<l["top"] and l["bottom"]<end]
        curves = [c for c in page.curves if start<c["top"] and c["bottom"]<end]
        rectangles = [r for r in _rectangles(page.rects,curves,lines) if start<r["top"] and r["bottom"]<end]
        legends = [w for w in local_words if w["text"].lower()=="soldering" and
                   any(v["text"].lower()=="area" and abs(v["top"]-w["top"])<2
                       and 0<=v["x0"]-w["x1"]<12 for v in local_words)]
        legend_styles = [
            _hatch_directions(r,lines) for r in rectangles for w in legends
            if 0<w["x0"]-r["x1"]<20 and r["top"]<=w["bottom"] and r["bottom"]>=w["top"]
        ]
        legend_styles = [s for s in legend_styles if s]
        if len(legend_styles)!=1:
            continue
        groups = []
        for rect in rectangles:
            if _hatch_directions(rect,lines) != legend_styles[0]:
                continue
            size = sorted([rect["x1"]-rect["x0"],rect["bottom"]-rect["top"]])
            matching=[g for s,g in groups if all(abs(a-b)<=max(.15,abs(a)*.01) for a,b in zip(size,s))]
            if len(matching)>1:
                groups=[]
                break
            if matching:
                matching[0].append(rect)
            else:
                groups.append((size,[rect]))
        groups = [g for _,g in groups if len(g)==count]
        if len(groups)!=1:
            continue
        pads = groups[0]
        dimensions = bind_vector_dimensions(local_words,lines,curves)
        bound = bind_rectangle_spans(dimensions,pads,lines)
        widths, lengths = set(), set()
        for dimension in bound:
            x0,y0,x1,y1=dimension["rectangle_bbox"]
            measured_is_short = ((x1-x0)<(y1-y0)) == (dimension["axis"]=="x")
            (widths if measured_is_short else lengths).add(dimension["value"])
        if len(widths)!=1 or len(lengths)!=1:
            continue
        envelope = [min(r["x0"] for r in pads),min(r["top"] for r in pads),
                    max(r["x1"] for r in pads),max(r["bottom"] for r in pads)]
        overall = {}
        outer_bindings = bind_envelope_spans(dimensions, pads, lines)
        for axis in (0,1):
            values = {d['value'] for d in dimensions if d['axis']==('x' if axis==0 else 'y')
                      and abs(d['span'][0]-envelope[axis])<.15
                      and abs(d['span'][1]-envelope[axis+2])<.15}
            values.update(d['value'] for d in outer_bindings if d['axis']=='xy'[axis])
            if len(values)==1:
                overall["xy"[axis]]=next(iter(values))
        if len(overall)!=2:
            continue
        centers=[((r["x0"]+r["x1"])/2,(r["top"]+r["bottom"])/2) for r in pads]
        xlo,xhi=min(x for x,y in centers),max(x for x,y in centers)
        ylo,yhi=min(y for x,y in centers),max(y for x,y in centers)
        sides=[
            sorted([p for p in centers if abs(p[0]-xlo)<.15],key=lambda p:p[1]),
            sorted([p for p in centers if abs(p[1]-yhi)<.15],key=lambda p:p[0]),
            sorted([p for p in centers if abs(p[0]-xhi)<.15],key=lambda p:-p[1]),
            sorted([p for p in centers if abs(p[1]-ylo)<.15],key=lambda p:-p[0]),
        ]
        if sum(map(len,sides))!=count or any(len(s)<2 for s in sides):
            continue
        ring=_numbered_ring(sides,local_words,max(pads[0]['x1']-pads[0]['x0'],pads[0]['bottom']-pads[0]['top']))
        if ring is None:
            continue
        turn,canonical_sides,numbered=ring
        pitch_values=set()
        for dimension in dimensions:
            axis=0 if dimension["axis"]=="x" else 1
            for side in sides:
                for a,b in zip(side,side[1:]):
                    if abs(a[1-axis]-b[1-axis])>.15:
                        continue
                    span=sorted([a[axis],b[axis]])
                    if max(abs(v-w) for v,w in zip(span,dimension["span"]))<.15:
                        pitch_values.add(dimension["value"])
        if len(pitch_values)!=1:
            continue
        results.append({
            "family":family, "pin_count":count, "caption":caption,
            "caption_text":caption_text, "unit":"mm",
            "pad_width_mm":next(iter(widths)), "pad_length_mm":next(iter(lengths)),
            "overall_x_mm":overall['y' if turn%2 else 'x'], "overall_y_mm":overall['x' if turn%2 else 'y'],
            "source_overall_x_mm":overall['x'], "source_overall_y_mm":overall['y'],
            "pitch_mm":next(iter(pitch_values)),
            "side_pin_counts":[len(side) for side in canonical_sides],
            "source_side_origin_quarter_turns":turn,
            "role_method":"soldering_area_hatch_legend",
            "numbered_corner_pads":numbered,
            "origin":"dimension_arrows_bound_to_legend_soldering_area",
            "dimension_bindings":dimensions, "pad_bindings":bound,
            "outer_bindings":outer_bindings,
            "copper_rectangles":[[r["x0"],r["top"],r["x1"],r["bottom"]] for r in pads],
            "region_pdf_points":[0,start,float(page.width),end],
        })
    return results
