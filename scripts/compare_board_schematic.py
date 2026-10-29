"""Compare physical pin identities, not display names or multipart instance counts."""
import argparse
import csv
import json
import re
from collections import defaultdict
from collections import Counter
from pathlib import Path

def compare(board, dsn, olb):
    components = {}
    for line in board.read_text().splitlines():
        fields = line.split('\t')
        fields = [s.strip('"') for s in fields]
        if fields[0] == 'COMP':
            components[fields[1]] = dict(footprint=fields[2], rotation=fields[4], mirrored=fields[5], pins=[], xy=[], pad_sizes=[])
        elif fields[0] == 'PIN':
            components[fields[1]]['pins'].append(fields[2])
            components[fields[1]]['xy'].append([float(n) for n in re.findall(r'-?\d+(?:\.\d+)?', fields[3])])
            box = [float(n) for n in re.findall(r'-?\d+(?:\.\d+)?', fields[4])]
            if len(box) == 4:
                components[fields[1]]['pad_sizes'].append((round(box[2]-box[0],6), round(box[3]-box[1],6)))
    unit_line = board.read_text().splitlines()[0].lower()
    factor = 0.0254 if 'mils' in unit_line else 1.0 if 'millimeters' in unit_line else None
    if factor is None:
        raise ValueError('Unsupported or missing native board units')
    for comp in components.values():
        axes = [sorted({round(v[i]*factor,6) for v in comp['xy']}) for i in (0,1)]
        comp['board_axis_min_spacing_mm'] = [min((b-a for a,b in zip(v,v[1:]) if b-a>0.00001), default=None) for v in axes]
        comp['pad_sizes_mm'] = [dict(width=round(w*factor,6), height=round(h*factor,6), count=n) for (w,h),n in Counter(comp.pop('pad_sizes')).most_common()]
        comp.pop('xy')
    packages = defaultdict(set)
    with olb.open(encoding='utf-8-sig') as f:
        for row in csv.DictReader(f, delimiter='\t'):
            packages[row['package']].add(row['pin_number'])
    groups = defaultdict(list)
    with dsn.open(encoding='utf-8-sig') as f:
        for row in csv.DictReader(f, delimiter='\t'):
            match = re.fullmatch(r'(U\d+)[A-Z]*', row['reference'], re.I)
            if match:
                groups[match[1]].append(row)
    result = []
    for ref, rows in sorted(groups.items()):
        comp = components.get(ref)
        expected = set().union(*(packages[r['package']] for r in rows))
        numbered = [p for p in comp['pins'] if p] if comp else []
        actual = set(numbered)
        result.append(dict(reference=ref, packages=sorted({r['package'] for r in rows}),
            part_values=sorted({r.get('part_value', '') for r in rows}),
            identity_policy='cache_package_name_is_not_manufacturer_part_identity',
            dsn_footprints=sorted({r['pcb_footprint'] for r in rows}), board=comp,
            expected_count=len(expected), actual_count=len(actual),
            missing=sorted(expected-actual), extra=sorted(actual-expected),
            duplicate_physical_pins=len(numbered)-len(actual),
            unnumbered_pad_count=len(comp['pins'])-len(numbered) if comp else 0,
            unnumbered_pad_status='requires_separate_mechanical_review' if comp and len(comp['pins']) != len(numbered) else 'none',
            pin_identity_match=bool(expected) and expected == actual and len(numbered) == len(actual),
            footprint_match=bool(comp) and all(r['pcb_footprint'].upper() == comp['footprint'].upper() for r in rows)))
    return dict(component_count=len(components), ic_comparisons=result,
                note='Project reference evidence only; not manufacturing approval. Dimensions/padstacks require separate checks.')

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    for arg in ('board', 'dsn', 'olb', 'output'):
        ap.add_argument(arg, type=Path)
    args = ap.parse_args()
    data = compare(args.board, args.dsn, args.olb)
    args.output.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    for row in data['ic_comparisons']:
        print(row['reference'], row['packages'], row['expected_count'], row['actual_count'], row['pin_identity_match'], row['footprint_match'])
