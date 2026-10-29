"""Native fresh-process regression for the production mask exporter."""
from pathlib import Path
import shutil
import subprocess
from datasheet_to_allegro.actual_verification import write_actual_export_skill, parse_actual_export

work = Path('output/board_layers_0539/native_mask_test').resolve()
work.mkdir(parents=True, exist_ok=True)
source = Path('output/generic_staging/Winbond_W25Q80JVSNIQ_bd4166e6/W25Q80JV/soic_8_w25q80jv.dra').resolve()
copy = work / source.name
shutil.copy2(source, copy)
report = work / 'actual.tsv'
script = write_actual_export_skill(copy, report, work / 'export.il')
replay = work / 'export.scr'
replay.write_text(f'skill load("{script.as_posix()}")\nexit\nfillin no\n', encoding='ascii')
run = subprocess.run(['D:/Cadence/SPB_17.4/tools/bin/allegro.exe', '-nograph', str(copy), '-s', str(replay)], cwd=work, capture_output=True, timeout=120)
log = (run.stdout+run.stderr).decode(errors='replace')
(work / 'console.log').write_text(log)
actual = parse_actual_export(report, copy, extraction_log=log)
assert actual.complete and len(actual.pins) == 8
assert all(len(p.mask_apertures) == 2 for p in actual.pins)
(work / 'actual.json').write_text(actual.model_dump_json(indent=2))
print('Native mask exporter: 8 pins, 16 aperture records, complete')
