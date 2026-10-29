"""Inspect an explicitly supplied primary PDF and package drawing for Allegro."""
import argparse
import json
from pathlib import Path
from datasheet_to_allegro.package_documents import inspect_with_package_document
from datasheet_to_allegro.industry_defaults import apply_staging_manufacturing_profile
from datasheet_to_allegro.generation_gate import review_generation_blockers

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('primary',type=Path)
p.add_argument('drawing',type=Path)
p.add_argument('output',type=Path)
args=p.parse_args()
args.output.mkdir(parents=True,exist_ok=False)
_,specs=inspect_with_package_document(args.primary,args.drawing)
for i,spec in enumerate(specs):
    spec=apply_staging_manufacturing_profile(spec)
    (args.output/f'spec_{i:03d}.json').write_text(spec.model_dump_json(indent=2),encoding='utf-8')
    print(json.dumps({'source_mode':'external_package_document','blockers':[b.code for b in review_generation_blockers(spec)]}))
