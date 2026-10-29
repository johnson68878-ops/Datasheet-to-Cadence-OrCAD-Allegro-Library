"""Packaged read-only audit entry point, independent of a Python installation."""
import argparse
import contextlib
from datetime import datetime
import json
from pathlib import Path
import runpy

def main(argv, resource_root):
    parser = argparse.ArgumentParser()
    parser.add_argument('--audit-capture', required=True, type=Path)
    parser.add_argument('--audit-output', required=True, type=Path)
    parser.add_argument('--cadence-root', required=True, type=Path)
    args = parser.parse_args(argv)
    source, output = args.audit_capture.resolve(), args.audit_output.resolve()
    if not source.is_dir() or output == source or source in output.parents:
        raise ValueError('Input must be a folder; output must be outside that folder')
    if not (args.cadence_root / 'tools/bin/tclsh.exe').is_file():
        raise ValueError('Cadence Tcl runtime was not found')
    # Each invocation preserves previous reports rather than overwriting them.
    output = output / datetime.now().strftime('run_%Y%m%d_%H%M%S_%f')
    output.mkdir(parents=True)
    scripts = Path(resource_root) / 'scripts'
    config = dict(source=str(source), output=str(output), scripts_root=str(scripts), cadence_root=str(args.cadence_root.resolve()))
    with (output / 'audit.log').open('w', encoding='utf-8') as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        try:
            for filename in ('audit_capture_attributes.py', 'compare_capture_attributes.py'):
                runpy.run_path(str(scripts / filename), init_globals={'AUDIT_CONFIG': config})
            report = json.loads((output / 'comparison.json').read_text(encoding='utf-8'))
            if any(item['errors'] for item in report['summary'].values()):
                raise RuntimeError('Some native attributes could not be read; inspect comparison.json')
            (output / 'status.json').write_text(json.dumps(dict(status='complete', output=str(output), scope='attribute_comparison_not_electrical_or_visual_acceptance')), encoding='utf-8')
        except Exception as exc:
            (output / 'status.json').write_text(json.dumps(dict(status='failed', error=str(exc))), encoding='utf-8')
            raise
    return output
