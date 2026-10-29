"""Read native layer evidence without inferring missing mask apertures."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re

def summarize(path):
    result = defaultdict(lambda: defaultdict(Counter))
    for line in path.read_text().splitlines():
        f = line.split('\t')
        if f[0] not in ('MASK', 'GEOMETRY', 'PIN'):
            continue
        ref = f[1].strip('"')
        if not re.fullmatch(r'U\d+', ref):
            continue
        layer = f[3] if f[0] == 'MASK' else f[2].strip('"') if f[0] == 'GEOMETRY' else 'COPPER'
        raw = f[4] if f[0] in ('MASK', 'PIN') else f[3]
        box = [float(x) for x in re.findall(r'-?\d+(?:\.\d+)?', raw)]
        size = 'absent' if len(box) != 4 else f'{abs(box[2]-box[0]):.4f} x {abs(box[3]-box[1]):.4f}'
        result[ref][layer][size] += 1
    return {ref: {layer: dict(counts) for layer, counts in layers.items()} for ref,layers in result.items()}

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('board', type=Path)
    ap.add_argument('output', type=Path)
    args = ap.parse_args()
    data = summarize(args.board)
    args.output.write_text(json.dumps(data, indent=2), encoding='utf-8')
    print(json.dumps(data, indent=2))
