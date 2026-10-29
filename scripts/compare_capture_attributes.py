import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from datasheet_to_allegro.capture_reference_rules import compare_attribute_maps, review_instance_identity

config = globals().get('AUDIT_CONFIG', {})
root = Path(config.get('output', 'output/capture_attributes_20260910'))
datasets = {}
summary = {}
for path in sorted(root.glob('*/attributes.tsv')):
    rows = list(csv.DictReader(path.open(encoding='utf-8-sig'), delimiter='\t'))
    maps = {(r['kind'],r['key'],r['name']):r['value'] for r in rows if r['kind'] != 'ERROR'}
    datasets[path.parent.name] = maps
    instances = defaultdict(dict)
    for row in rows:
        if row['kind'] == 'PROPERTY' and row['key'].startswith('instance:'):
            instances[row['key']][row['name']] = row['value']
    issues = [{'object':k,'findings':review_instance_identity(v),'properties':v} for k,v in instances.items() if review_instance_identity(v)]
    summary[path.parent.name] = dict(rows=len(rows), counts=dict(Counter(r['kind'] for r in rows)),
        property_names=dict(Counter(r['name'] for r in rows if r['kind']=='PROPERTY')),
        instances=len(instances), identity_review=issues,
        errors=[r for r in rows if r['kind']=='ERROR'],
        pin_type_counts=dict(Counter(r['value'] for r in rows if r['kind']=='PIN' and r['name']=='GetPinType')),
        hidden_pins=[r['key'] for r in rows if r['kind']=='PIN' and r['name']=='GetIsVisible' and r['value']=='0'])
pairs = [(lib,dsn) for lib in datasets for dsn in datasets if lib.endswith('_olb') and dsn.endswith('_dsn')]
comparison = {}
for lib, dsn in pairs:
    # Instance overrides are a separate scope, not absent library fields.
    left = datasets[lib]
    right = {k:v for k,v in datasets[dsn].items() if not k[1].startswith('instance:')}
    comparison[lib+'__vs__'+dsn] = compare_attribute_maps(left,right)
payload = dict(summary=summary, comparisons=comparison)
payload['pair_policy'] = 'All library/design combinations are candidates only; shared cache names do not prove matching devices or projects.'
(root/'comparison.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2),encoding='utf-8')
for name,data in summary.items():
    print(name,data['counts'],'instances',data['instances'],'identity_review',len(data['identity_review']))
for name,data in comparison.items():
    print(name,'matched',data['matched_fields'],'differences',len(data['differences']),Counter(d['attribute'] for d in data['differences']))
