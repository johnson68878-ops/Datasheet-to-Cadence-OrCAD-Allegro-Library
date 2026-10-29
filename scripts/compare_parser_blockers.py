"""Compare stored parser runs without supplying any evidence to production."""
import argparse
import json
from pathlib import Path


def compare(before, after):
    def index(records):
        result = {}
        for doc in records:
            for variant in doc.get('variants', []):
                key = (doc['file'], variant['family'], variant.get('package_code'), variant.get('pin_count'))
                if key in result:
                    raise ValueError(f'Ambiguous candidate identity: {key}')
                result[key] = variant
        return result
    old, new = index(before), index(after)
    changes = []
    for key in sorted(old.keys() | new.keys(), key=str):
        a, b = old.get(key), new.get(key)
        if a is None or b is None:
            changes.append({'candidate': key, 'status': 'added' if a is None else 'missing'})
            continue
        previous, current = set(a['review_blockers']), set(b['review_blockers'])
        if previous != current:
            changes.append({'candidate': key, 'added_blockers': sorted(current-previous),
                            'removed_blockers': sorted(previous-current)})
    return {'before_candidates': len(old), 'after_candidates': len(new), 'blocker_changes': changes}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    report = compare(json.loads(args.before.read_text(encoding='utf-8')),
                     json.loads(args.after.read_text(encoding='utf-8')))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
