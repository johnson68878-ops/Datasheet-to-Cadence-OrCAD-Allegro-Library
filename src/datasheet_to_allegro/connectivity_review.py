"""Native DSN/BRD topology comparison, with explicit evidence coverage.

Net labels may differ across exports: compare physical membership, not names.
Reference/ball aliases are never guessed from similar part names.
"""
from collections import defaultdict
import csv
from .engineering_checks import audit_net_semantics

TYPES = {0: 'Input', 1: 'Bidirectional', 2: 'Output', 3: 'OpenCollector',
         4: 'Passive', 5: '3State', 7: 'Power'}


def read_dsn(path):
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    if not lines or lines[-1] != 'COMPLETE':
        raise ValueError('Incomplete native DSN export')
    return [dict(refdes=r['refdes'], number=r['pin'], name=r['name'],
                 type=TYPES.get(int(r['type']), 'Unknown'), net=r['net'], occurrence=r['occurrence'])
            for r in csv.DictReader(lines[:-1], delimiter='\t') if r['kind'] == 'PIN']


def read_board(path):
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    if 'COMPLETE\t1' not in lines:
        raise ValueError('Incomplete native board export')
    records = []
    for line in lines:
        f = line.split('\t')
        if f[0] == 'PIN' and len(f) >= 7:
            records.append(dict(refdes=f[1].strip('"'), number=f[2].strip('"'), net=f[6].strip('"')))
    if not records:
        raise ValueError('Empty board export, possibly locked source')
    return records


def topology(records):
    index, groups, errors = {}, defaultdict(set), []
    for row in records:
        key = (row['refdes'], row['number'])
        if not all(key):
            errors.append({'pin': key, 'issue': 'missing physical identity'})
            continue
        if key in index:
            errors.append({'pin': key, 'issue': 'duplicate physical identity'})
        index[key] = row
        if row['net']:
            groups[row['net']].add(key)
    return index, groups, errors


def compare_connectivity(dsn, board, datasheet_evidence=()):
    left, lg, le = topology(dsn)
    right, rg, re = topology(board)
    common = left.keys() & right.keys()
    # Missing components remain explicit even when shared-net comparison matches.
    differences = []
    for key in sorted(common):
        a, b = left[key]['net'], right[key]['net']
        ap, bp = lg.get(a, set()) & common, rg.get(b, set()) & common
        if ap != bp or bool(a) != bool(b):
            differences.append({'pin': key, 'dsn_net': a, 'board_net': b,
                                'dsn_only_peers': sorted(ap-bp), 'board_only_peers': sorted(bp-ap)})
    evidence = {(e['refdes'], e['number']): e for e in datasheet_evidence}
    pins = []
    for key, row in left.items():
        item = dict(row)
        ev = evidence.get(key, {})
        # A page pointer AND an explicit type are required; no type by name regex.
        item['type_evidence'] = ev.get('source') if ev.get('type') else None
        item['datasheet_type'] = ev.get('type')
        item['explicit_nc'] = bool(ev.get('explicit_nc') and ev.get('source'))
        pins.append(item)
    semantic = audit_net_semantics(pins, [{'name': n, 'pins': sorted(keys)} for n, keys in lg.items()])
    return dict(status='fail' if le or re or differences or semantic['status'] == 'fail' else 'unresolved'
                if left.keys() != right.keys() or semantic['status'] != 'pass' else 'pass',
                dsn_pin_count=len(left), board_pin_count=len(right), shared_pin_count=len(common),
                matching_shared_pin_topology=len(common)-len(differences), differences=differences,
                missing_from_board=sorted(left.keys()-right.keys()), missing_from_dsn=sorted(right.keys()-left.keys()),
                identity_errors=le+re, electrical_review=semantic,
                datasheet_type_evidenced=sum(bool(p['type_evidence']) for p in pins))


def main(argv=None):
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument('--review-connectivity', action='store_true')
    parser.add_argument('--dsn', type=Path, required=True)
    parser.add_argument('--board', type=Path, required=True)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.resolve() in {p.resolve() for p in (args.dsn, args.board, args.evidence) if p}:
        raise ValueError('Output must not overwrite evidence input')
    evidence = json.loads(args.evidence.read_text(encoding='utf-8')) if args.evidence else []
    result = compare_connectivity(read_dsn(args.dsn), read_board(args.board), evidence)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result['status'] == 'pass' else 2
