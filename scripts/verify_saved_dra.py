"""Independently reopen a saved DRA after an interrupted generation; no repair."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from datasheet_to_allegro.models import FootprintSpec
from datasheet_to_allegro.geometry import build_geometry
from datasheet_to_allegro.actual_verification import (write_actual_export_skill,
    write_actual_replay, parse_actual_export, compare_spec_to_actual, write_actual_reports)
from datasheet_to_allegro.cadence_compatibility import executable_version, target_version_error, session_failure


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec',type=Path)
    parser.add_argument('dra',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('allegro',type=Path)
    parser.add_argument('--version',required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    report={'status':'error','scope':'Independent saved-file readback; does not erase generation failure',
            'dra':str(args.dra.resolve()),'publish_allowed':False}
    try:
        mismatch=target_version_error(executable_version(args.allegro),args.version)
        if mismatch:
            raise ValueError(mismatch)
        spec=FootprintSpec.model_validate_json(args.spec.read_text(encoding='utf-8'))
        digest=hashlib.sha256(args.dra.read_bytes()).hexdigest()
        tsv=args.output/'actual.tsv'
        exporter=write_actual_export_skill(args.dra.resolve(),tsv.resolve(),args.output/'export.il')
        replay=write_actual_replay(exporter,args.output/'export.scr')
        with (args.output/'readback.log').open('wb') as log:
            run=subprocess.run([str(args.allegro),'-nograph','-symbol','package',str(args.dra.resolve()),
                '-s',str(replay.resolve())],cwd=args.output,stdout=log,stderr=subprocess.STDOUT,timeout=120)
        log=(args.output/'readback.log').read_text(encoding='utf-8',errors='replace')
        if run.returncode or session_failure(log):
            raise RuntimeError(f'Readback exit={run.returncode}; {session_failure(log)}')
        actual=parse_actual_export(tsv,args.dra,extraction_log=log)
        diff=compare_spec_to_actual(spec,build_geometry(spec),actual,gate_mode='review')
        write_actual_reports(actual,diff,args.output)
        report.update(status=diff.status,native_complete=actual.complete,version=args.version,
                      source_dra_sha256=digest,pins=len(actual.pins),drc=actual.drc_count,
                      nonpass=[f.model_dump() for f in diff.findings if f.status!='pass'])
    except Exception as exc:
        report['error']=str(exc)
    (args.output/'saved_readback.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    main()
