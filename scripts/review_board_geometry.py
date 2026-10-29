"""Measure exported native geometry; board observations are not vendor defaults."""
import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from shapely.geometry import Polygon
from shapely.ops import unary_union
from datasheet_to_allegro.native_polygon import native_polygon

def numbers(s):
    return [float(v) for v in re.findall(r'[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?', s)]

def polygon(vertices, holes, scale=1.0, native_area=None):
    return native_polygon(vertices,holes,scale,native_area)

def review(path):
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    if not lines or 'COMPLETE\t1' not in lines:
        raise ValueError('Complete native export required')
    scale=1.0 if 'millimeters' in lines[0] else .0254 if '"mils"' in lines[0] else None
    if scale is None:
        raise ValueError('Unsupported native coordinate units')
    comps, raw_copper, copper, paste = {}, defaultdict(list), defaultdict(dict), defaultdict(list)
    layers, texts, unsupported = defaultdict(Counter), defaultdict(list), Counter()
    pad_layers, drills = defaultdict(lambda: defaultdict(dict)), defaultdict(list)
    for line in lines:
        f = line.split('\t')
        if len(f) < 2:
            continue
        ref = f[1].strip('"')
        if f[0] == 'COMP':
            comps[ref] = dict(footprint=f[2].strip('"'), origin=[v*scale for v in numbers(f[3])], rotation=float(f[4]), mirrored=f[5]=='t')
        elif f[0] == 'PAD_POLYGON':
            p = polygon(f[4],f[5],scale,f[6] if len(f)>6 else None)
            if p is None:
                unsupported[ref] += 1
            else:
                number=f[2].strip('"')
                pad_layers[ref][number].setdefault(f[3],[]).append(p)
                if f[3].startswith('ETCH/'):
                    raw_copper[ref].append(number)
                    copper[ref].setdefault(number, []).append(p)
        elif f[0] == 'DRILL':
            drills[ref].append(dict(pin=f[2].strip('"'),diameter_mm=float(f[3])*scale,
                slot_width_mm=float(f[4])*scale,slot_height_mm=float(f[5])*scale,
                offset_mm=[v*scale for v in numbers(f[6])],rotation=float(f[7]),
                plating=f[8].strip('"'),symbol=f[9].strip('"')))
        elif f[0] == 'SHAPE_POLYGON' and 'PASTEMASK' in f[2]:
            p = polygon(f[3],f[4],scale,f[5] if len(f)>5 else None)
            paste[ref].append(p)
            if p is None:
                unsupported[ref] += 1
        elif f[0] == 'GEOMETRY':
            layers[ref][f[2].strip('"')] += 1
        elif f[0] == 'TEXT':
            texts[ref].append(dict(layer=f[2].strip('"'),text=f[3].strip('"'),bbox=[v*scale for v in numbers(f[4])]))
    rows = []
    for ref, comp in comps.items():
        windows = paste[ref]
        assigned = defaultdict(list)
        issues = []
        for i,w in enumerate(windows):
            if w is None:
                issues.append(f'window {i}: arc/hole/malformed polygon')
                continue
            owners = [n for n, ps in copper[ref].items() if unary_union(ps).buffer(1e-5).covers(w)]
            if len(owners) == 1:
                assigned[owners[0]].append(w)
            else:
                issues.append(f'window {i}: ownership count {len(owners)}')
        groups = []
        for n,ws in assigned.items():
            cp = unary_union(copper[ref][n])
            local = []
            angle = math.radians(-comp['rotation'])
            for x,y in cp.convex_hull.exterior.coords:
                x,y = x-comp['origin'][0], y-comp['origin'][1]
                local.append((x*math.cos(angle)-y*math.sin(angle), x*math.sin(angle)+y*math.cos(angle)))
            # Mirroring changes handedness, not the measured local width/height.
            union = unary_union(ws)
            groups.append(dict(pin=n, window_count=len(ws), copper_area_mm2=cp.area,
                               paste_union_area_mm2=union.area, coverage=union.area/cp.area,
                               copper_local_size_mm=[max(x for x,y in local)-min(x for x,y in local),max(y for x,y in local)-min(y for x,y in local)],
                               min_gap_mm=min((a.distance(b) for i,a in enumerate(ws) for b in ws[:i]),default=None),
                               overlap_area_mm2=sum(p.area for p in ws)-union.area))
        pad_metrics=[]
        for number, layer_map in pad_layers[ref].items():
            for side in ('TOP','BOTTOM'):
                etch=layer_map.get('ETCH/'+side)
                if not etch: continue
                cp=unary_union(etch)
                entry=dict(pin=number,side=side,copper_area_mm2=cp.area)
                for kind in ('SOLDERMASK','PASTEMASK'):
                    shapes=layer_map.get('PIN/'+kind+'_'+side)
                    if not shapes:
                        entry[kind]=dict(status='not_present_not_inferred')
                        continue
                    mask=unary_union(shapes)
                    entry[kind]=dict(area_mm2=mask.area,area_ratio=mask.area/cp.area,
                        outside_copper_mm2=mask.difference(cp).area,
                        copper_not_open_mm2=cp.difference(mask).area,
                        interpretation='measured_not_manufacturing_approval')
                pad_metrics.append(entry)
        rows.append(dict(refdes=ref, **comp, layer_object_counts=dict(layers[ref]), texts=texts[ref],
                         pad_layer_metrics=pad_metrics,drills=drills[ref],
                         raw_copper_polygons=len(raw_copper[ref]), unsupported_polygons=unsupported[ref],
                         package_paste_window_count=len(windows), window_groups=groups, issues=issues,
                         acceptance='unresolved_requires_datasheet_constraints_and_layer_metrics'))
    return dict(component_count=len(rows), package_paste_windows=sum(len(w) for w in paste.values()),
                unsupported_polygons=sum(unsupported.values()), components=rows,
                scope='exported_curves_voids_and_pad_layers_measured; native_area_checked_when_provided; manufacturing_constraints_and_drill_clearance_not_approved')

if __name__ == '__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('source',type=Path)
    ap.add_argument('output',type=Path)
    args=ap.parse_args()
    result=review(args.source)
    args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='components'}))
    print(json.dumps([r for r in result['components'] if r['package_paste_window_count']],ensure_ascii=False))
