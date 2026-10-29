"""Recover printed symmetric tolerances from vector strokes inside table cells."""
import re
from .models import Measurement, SourceEvidence
from .dimension_lexicon import symbol


def has_plus_minus(lines, box):
    x0,y0,x1,y1 = box
    local = [l for l in lines if x0 < l['x0'] <= l['x1'] < x1 and y0 < l['top'] <= l['bottom'] < y1]
    horizontal = [l for l in local if abs(l['top']-l['bottom']) < .05 and l['x1']-l['x0'] > .5]
    vertical = [l for l in local if abs(l['x0']-l['x1']) < .05 and l['bottom']-l['top'] > .5]
    matches = []
    for upper in horizontal:
        w=upper['x1']-upper['x0']; cx=(upper['x0']+upper['x1'])/2
        for lower in horizontal:
            if not (.5*w < lower['top']-upper['top'] < 1.5*w): continue
            if max(abs(lower[k]-upper[k]) for k in ('x0','x1')) > .1: continue
            for stem in vertical:
                if (abs(stem['x0']-cx)<.1 and stem['top']<upper['top']<stem['bottom']<lower['top']
                    and abs((stem['top']+stem['bottom'])/2-upper['top'])<.1):
                    matches.append((upper,lower,stem))
    return len(matches)==1


def read_vector_tolerances(page, page_number):
    context=page.extract_text() or ''
    if not re.search(r'all dimensions (?:are )?in millimeters?\b',context,re.I): return {}
    words=page.extract_words(); found={}; conflicts=set()
    for table in page.find_tables():
        rows=table.extract()
        if not rows or not re.search(r'COMMON\s+DIMENSIONS', str(rows[0]),re.I): continue
        for row, physical in zip(rows,table.rows):
            if len(row)!=2 or not row[0] or not row[1]: continue
            key=symbol(row[0])
            if key is None: continue
            box=physical.cells[1]
            if not box: continue
            local=[w for w in words if box[0]<=w['x0'] and w['x1']<=box[2] and box[1]<=w['top'] and w['bottom']<=box[3]]
            nums=sorted([w for w in local if re.fullmatch(r'\d+(?:\.\d+)?',w['text'])],key=lambda w:w['x0'])
            if len(nums)!=2: continue
            a,b=nums
            if abs(a['top']-b['top'])>max(a['bottom']-a['top'],b['bottom']-b['top'])*.3: continue
            if not has_plus_minus(page.lines,(a['x1'],box[1],b['x0'],box[3])): continue
            nominal,tolerance=float(a['text']),float(b['text'])
            if nominal<=tolerance or tolerance<=0: continue
            raw=f"{key}: {a['text']} ± {b['text']}; cell={box}; vector plus/minus between numbers"
            value=Measurement(min_mm=round(nominal-tolerance,9),nominal_mm=nominal,max_mm=round(nominal+tolerance,9),original_value=raw,original_unit='mm',
                evidence=[SourceEvidence(page=page_number,figure_or_table='Vector-confirmed symmetric tolerance cell',raw_text=raw,extraction_method='pdf_text',confidence=.96,evidence_scope='package_outline')])
            if key in found and found[key].nominal_mm!=nominal: conflicts.add(key)
            else: found[key]=value
    return {k:v for k,v in found.items() if k not in conflicts}
