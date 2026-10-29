"""Full effective properties + display settings audit; original files unchanged."""
import hashlib
import json
from pathlib import Path
import subprocess
from datasheet_to_allegro.orcad_library import write_olb_readback_tcl

config = globals().get('AUDIT_CONFIG', {})
root = Path(config.get('source', 'SCH&PCB')).resolve()
out = Path(config.get('output', 'output/capture_attributes_20260910')).resolve()
if out == root or root in out.parents:
    raise ValueError('Audit output must be outside the input directory')
out.mkdir(parents=True, exist_ok=True)
scripts_root = Path(config.get('scripts_root', 'scripts')).resolve()
cadence_root = Path(config.get('cadence_root', 'D:/Cadence/SPB_17.4')).resolve()
helpers = (scripts_root / 'capture_attribute_helpers.tcl').as_posix()
manifest = []
for source in sorted(root.iterdir()):
    if source.suffix.lower() not in ('.dsn', '.olb'):
        continue
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    folder = out / (source.stem + source.suffix.lower().replace('.', '_'))
    folder.mkdir(exist_ok=True)
    script = folder / 'export.tcl'
    attrs = folder / 'attributes.tsv'
    preamble = f'source {{{helpers}}}\nset attributeReport [open {{{attrs.as_posix()}}} w]\nfconfigure $attributeReport -encoding utf-8\nputs $attributeReport "kind\\tkey\\tname\\tvalue"\n'
    if source.suffix.lower() == '.olb':
        write_olb_readback_tcl(source, script, folder / 'pins.tsv', cadence_root=cadence_root)
        code = script.read_text(encoding='utf-8')
        code = preamble + code
        code = code.replace('  set parts [$package NewPartsIter $status]', '  auditProps $package "package:[value $packageName]"\n  set parts [$package NewPartsIter $status]')
        code = code.replace('    set pins [$part NewLPinsIter $status]', '    auditProps $part "part:[value $packageName]:$section"\n    set pins [$part NewLPinsIter $status]')
        code = code.replace('      set pinType [$pin GetPinType $status]', '      auditPin $pin "pin:[value $packageName]:$section:[value $pinNumber]" $status\n      set pinType [$pin GetPinType $status]')
        argv = []
    else:
        code = preamble + (scripts_root / 'audit_orcad_dsn.tcl').read_text(encoding='utf-8')
        code = code.replace('D:/Cadence/SPB_17.4/tools/bin/orDb_Dll_Tcl64.dll', (cadence_root / 'tools/bin/orDb_Dll_Tcl64.dll').as_posix())
        code = code.replace('set parts [$package NewPartsIter $status]', 'auditProps $package "package:$key"\n    set parts [$package NewPartsIter $status]')
        code = code.replace('set pins [$part NewLPinsIter $status]', 'auditProps $part "part:$key:$section"\n        set pins [$part NewLPinsIter $status]')
        code = code.replace('$device GetPinNumber [$pin GetPinPosition $status] $number', '$device GetPinNumber [$pin GetPinPosition $status] $number\n            auditPin $pin "pin:$key:$section:[cv $number]" $status')
        code = code.replace('exportPackagePins $package $status', 'auditProps $placed "instance:$schematicText:$pageText:[cv $ref]"\n                exportPackagePins $package $status')
        argv = [str(source), str(folder / 'instances.tsv'), str(folder / 'pins.tsv')]
    code = code.replace('exit 0', 'close $attributeReport\nexit 0')
    script.write_text(code, encoding='utf-8')
    run = subprocess.run([str(cadence_root / 'tools/bin/tclsh.exe'), str(script), *argv], capture_output=True, timeout=180)
    (folder / 'console.log').write_bytes(run.stdout+run.stderr)
    record = dict(source=str(source), sha256=before, unchanged=before == hashlib.sha256(source.read_bytes()).hexdigest(), returncode=run.returncode, attributes=str(attrs))
    manifest.append(record)
    print(json.dumps(record), flush=True)
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2))
if not manifest or any(r['returncode'] or not r['unchanged'] for r in manifest):
    raise RuntimeError('Capture audit failed or no input files were found; inspect manifest and console logs')
