"""Export native board evidence from disposable copies; never save source boards."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

EXPORT = r'''
procedure(ds2aVertices(vs)
  let((result)
    result = "("
    foreach(v vs
      result = strcat(result sprintf(nil "((%.12g %.12g) %.12g) " car(car(v)) cadr(car(v)) cadr(v)))
    )
    strcat(result ")")
  )
)
procedure(ds2aHoles(hs)
  let((result)
    result = "("
    foreach(h hs result = strcat(result ds2aVertices(h->vertices)))
    strcat(result ")")
  )
)
procedure(ds2aBoardEvidence()
  prog((d f s p pad box layer child poly)
    d = axlDBGetDesign()
    f = outfile(getShellEnvVar("DS2A_BOARD_TSV"))
    fprintf(f "UNITS\t%L\n" axlDBGetDesignUnits())
    foreach(s d->symbols
      when(s->component
        fprintf(f "COMP\t%L\t%L\t%L\t%L\t%L\n" s->component->name s->name s->xy s->rotation s->isMirrored)
        foreach(p s->pins
          pad = axlDBGetPad(p if(s->isMirrored then "PIN/BOTTOM" else "PIN/TOP") "REGULAR")
          box = if(pad then pad->bBox else nil)
          fprintf(f "PIN\t%L\t%L\t%L\t%L\t%L\t%L\n" s->component->name p->number p->xy box p->name if(p->net then p->net->name else ""))
          fprintf(f "DRILL\t%L\t%L\t%L\t%L\t%L\t%L\t%L\t%L\t%L\n" s->component->name p->number p->definition->drillDiameter p->definition->drillSizeWidth p->definition->drillSizeHeight p->definition->drillOffset p->rotation p->definition->plating p->definition->drillFigureName)
          foreach(layer list("ETCH/TOP" "ETCH/BOTTOM" "PIN/SOLDERMASK_TOP" "PIN/SOLDERMASK_BOTTOM" "PIN/PASTEMASK_TOP" "PIN/PASTEMASK_BOTTOM")
            foreach(poly axlPolyFromDB(p ?layer layer)
              fprintf(f "PAD_POLYGON\t%L\t%L\t%s\t%s\t%s\t%.12g\n" s->component->name p->number layer ds2aVertices(poly->vertices) ds2aHoles(poly->holes) poly->area)
            )
          )
          foreach(layer list("PIN/SOLDERMASK_TOP" "PIN/SOLDERMASK_BOTTOM" "PIN/PASTEMASK_TOP" "PIN/PASTEMASK_BOTTOM")
            pad = axlDBGetPad(p layer "REGULAR")
            fprintf(f "MASK\t%L\t%L\t%s\t%L\n" s->component->name p->number layer if(pad then pad->bBox else nil))
          )
        )
        foreach(child s->children
          fprintf(f "GEOMETRY\t%L\t%L\t%L\t%L\n" s->component->name child->layer child->bBox axlDBGetProperties(child))
          when(child->objType == "shape"
            foreach(poly axlPolyFromDB(child)
              fprintf(f "SHAPE_POLYGON\t%L\t%L\t%s\t%s\t%.12g\n" s->component->name child->layer ds2aVertices(poly->vertices) ds2aHoles(poly->holes) poly->area)
            )
          )
          when(child->objType == "text"
            fprintf(f "TEXT\t%L\t%L\t%L\t%L\n" s->component->name child->layer child->text child->bBox)
          )
        )
      )
    )
    fprintf(f "COMPLETE\t1\n")
    close(f)
  )
)
ds2aBoardEvidence()
'''

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('source', type=Path)
    ap.add_argument('output', type=Path)
    ap.add_argument('--allegro', type=Path, required=True)
    args = ap.parse_args()
    root = args.source.resolve()
    out = args.output.resolve()
    if out == root or root in out.parents:
        raise ValueError('Evidence output must be outside the source directory')
    out.mkdir(parents=True, exist_ok=True)
    exporter = out / 'board_export.il'
    exporter.write_text(EXPORT, encoding='ascii')
    manifest = []
    for board in ([root] if root.is_file() and root.suffix.lower() == '.brd' else sorted(root.glob('*.brd'))):
        work = out / board.stem
        work.mkdir(exist_ok=True)
        before = digest(board)
        copy = work / board.name
        shutil.copy2(board, copy)
        tsv = work / 'board.tsv'
        replay = work / 'export.scr'
        replay.write_text(f'skill load("{exporter.as_posix()}")\nexit\nfillin no\n', encoding='ascii')
        env = dict(os.environ, DS2A_BOARD_TSV=tsv.as_posix())
        try:
            proc = subprocess.run([str(args.allegro.resolve()), '-nograph', str(copy), '-s', str(replay)], cwd=work, env=env, capture_output=True, timeout=180)
            (work / 'console.log').write_bytes(proc.stdout + proc.stderr)
            content = tsv.read_text(errors='replace') if tsv.exists() else ''
            log = (proc.stdout + proc.stderr).decode(errors='replace')
            complete = proc.returncode == 0 and 'COMPLETE\t1' in content and '\nPIN\t' in content and 'Open Canceled' not in log
            record = dict(source=str(board), sha256=before, unchanged=before == digest(board), complete=complete, returncode=proc.returncode, evidence=str(tsv))
        except subprocess.TimeoutExpired:
            record = dict(source=str(board), sha256=before, unchanged=before == digest(board), complete=False, error='Native export timeout')
        manifest.append(record)
        print(json.dumps(record), flush=True)
        (out / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return 0 if manifest and all(x['complete'] and x['unchanged'] for x in manifest) else 1

if __name__ == '__main__':
    raise SystemExit(main())
