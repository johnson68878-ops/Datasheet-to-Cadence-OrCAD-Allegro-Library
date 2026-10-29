"""Review existing native exports without modifying designs."""
import argparse
import json
from pathlib import Path
from datasheet_to_allegro.connectivity_review import read_dsn, read_board, compare_connectivity

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dsn', type=Path, required=True)
    ap.add_argument('--board', type=Path, required=True)
    ap.add_argument('--evidence', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    evidence = json.loads(args.evidence.read_text(encoding='utf-8')) if args.evidence else []
    result = compare_connectivity(read_dsn(args.dsn), read_board(args.board), evidence)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k,v in result.items() if k not in {'differences','missing_from_board','missing_from_dsn','identity_errors','electrical_review'}}, ensure_ascii=False))
    return 0 if result['status'] == 'pass' else 2

if __name__ == '__main__':
    raise SystemExit(main())
