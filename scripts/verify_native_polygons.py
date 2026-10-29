"""Read-only disposable-copy verification of polygon export; never saves inputs."""
import json
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from datasheet_to_allegro.actual_verification import write_actual_export_skill, write_actual_replay, parse_actual_export

source = Path('output/golden_gate_v0.5.17_test/qfn_24_rts5452_gr.dra').resolve()
out = Path('output/native_polygons_0542') / datetime.now().strftime('%Y%m%d_%H%M%S')
out.mkdir(parents=True)
copy = out / source.name
shutil.copy2(source, copy)
tsv = (out / 'actual.tsv').resolve()
script = write_actual_export_skill(copy, tsv, out / 'export.il')
replay = write_actual_replay(script, out / 'export.scr')
proc = subprocess.run(['D:/Cadence/SPB_17.4/tools/bin/allegro.exe', '-nograph', str(copy.resolve()), '-s', str(replay.resolve())], cwd=out, capture_output=True, timeout=180)
log = (proc.stdout + proc.stderr).decode(errors='replace')
(out/'console.log').write_text(log)
if not tsv.exists():
    raise RuntimeError(log)
actual = parse_actual_export(tsv, copy, extraction_log=log)
(out/'actual.json').write_text(actual.model_dump_json(indent=2))
print(json.dumps({'complete':actual.complete,'pins':len(actual.pins),'paste_polygons':len(actual.paste_polygons),
                  'copper_polygons':sum(len(p.copper_polygons) for p in actual.pins),
                  'polygon_errors':actual.polygon_review_errors,'output':str(out)}),flush=True)
assert actual.complete
