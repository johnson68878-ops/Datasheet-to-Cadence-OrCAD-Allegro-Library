"""Schema-aware mechanical table reading, independent of vendor and part number.

Units and MIN/TYP/MAX come from headers, not value magnitude or column order.
Dash cells remain absent. Damaged digits are never reconstructed arithmetically.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

from .models import Measurement, SourceEvidence
from .dimension_lexicon import clean, number, role, unit, symbol, symbol_header




@dataclass
class Schema:
    columns: dict
    symbol_column: int
    width: int




def infer_schema(rows, context=''):
    columns={}
    symbol_col=None
    units={}
    active=None
    width=max((len(r) for r in rows),default=0)
    for row in rows[:5]:
        for i,cell in enumerate(row):
            text=clean(cell)
            if symbol_header(text): symbol_col=i
            found=unit(text)
            if found: active=found
            if active: units[i]=active
            found_role=role(text)
            if text.upper()=='MIM' and any(role(c)=='max_mm' for c in row) and any(role(c)=='nominal_mm' for c in row):
                found_role='min_mm'  # Header-only printed/OCR substitution, never a numeric repair.
            if found_role: columns[i]=(units.get(i),found_role)
        active=None
    # Dual-unit tables repeat MIN/NOM/MAX in adjacent groups. A merged unit
    # heading can be centred one cell away, so nearest-cell propagation may
    # incorrectly assign the first column of group two to group one. Resolve a
    # complete repeated role sequence as two structural unit groups.
    role_entries=[]
    for row in rows[:5]:
        current=[(i,role(clean(cell))) for i,cell in enumerate(row) if role(clean(cell))]
        if len(current)>len(role_entries): role_entries=current
    starts=[i for i,item in enumerate(role_entries) if role_entries and item[1]==role_entries[0][1]]
    if len(starts)==2:
        groups=[role_entries[starts[0]:starts[1]],role_entries[starts[1]:]]
        if groups[0] and [item[1] for item in groups[0]]==[item[1] for item in groups[1]]:
            resolved=[]
            for group in groups:
                candidates=[units.get(column) for column,_field in group if units.get(column)]
                resolved.append(max(set(candidates),key=candidates.count) if candidates else None)
            if None not in resolved and len(set(resolved))==2:
                columns={column:(resolved[group_index],field)
                         for group_index,group in enumerate(groups)
                         for column,field in group}
    # A single-unit table often declares its unit in a caption/footnote.
    context_units={u for u in (unit(word) for word in re.findall(r'\b(?:mm|millimeters?|millimetres?|inches|inch)\b',context,re.I)) if u}
    if len(context_units)==1:
        inferred=next(iter(context_units))
        columns={i:(u or inferred,r) for i,(u,r) in columns.items()}
    if symbol_col is not None and not columns and len(units)==1:
        columns={i:(u,'value_mm') for i,u in units.items() if i!=symbol_col}
    if symbol_col is None or not columns or any(u is None for u,r in columns.values()): return None
    if len({u for u,r in columns.values()})==1 and not units and not context_units: return None
    return Schema(columns,symbol_col,width)


def _opposes_metric_authority(text):
    return bool(re.search(
        r"(?:inch(?:es)?).{0,30}(?:control|govern)|"
        r"(?:mm|millimeters?).{0,30}converted\s+from\s+inch",
        text, re.I | re.S,
    ))


def _metric_authority(text, page):
    """Only an explicit conversion declaration establishes the primary unit."""
    if _opposes_metric_authority(text):
        return None
    match = re.search(
        r"values\s+in\s+inches\s+are\s+converted\s+from\s+(?:mm|millimeters?|millimetres?)"
        r"(?:\s+and\s+rounded[^.\n]*)?", text, re.I,
    )
    return {"page": page, "raw_text": match[0], "primary_unit": "mm"} if match else None


def read_table(rows, page, context='', inherited=None, *, method='table_extract', scope='package_outline', label='Dimension table', unit_authority=None):
    schema=infer_schema(rows,context)
    if schema is None and inherited and rows and max(len(r) for r in rows)==inherited.width:
        # Cross-page continuation must begin with an actual dimension row.
        if symbol(rows[0][inherited.symbol_column]) and any(number(c) is not None for c in rows[0]):
            schema=inherited
    if schema is None: return {},[],None
    found={}
    issues=[]
    conflicted=set()
    for index,row in enumerate(rows):
        if len(row)<=schema.symbol_column: continue
        key=symbol(row[schema.symbol_column])
        if key is None: continue
        # Counts and angles are not lengths, even under a table-wide mm/inch heading.
        numeric=[number(c) for c in row[schema.symbol_column+1:] if number(c) is not None]
        if (key in {'N','ND','NE','MD','ME'} and numeric and len(set(numeric))==1 and numeric[0]>=1 and numeric[0].is_integer()) or any(re.search(r'[°º]|\bdegrees?\b',clean(c),re.I) for c in row): continue
        groups={}
        scalar_units=set()
        invalid=False
        invalid_units=set()
        for column,(units,field) in schema.columns.items():
            raw=row[column] if column<len(row) else None
            text=clean(raw)
            if not text or re.fullmatch(r'[-—–]+',text): continue
            if re.fullmatch(r'(?:BSC|REF|REFERENCE)\.?',text,re.I):
                # An adjacent qualifier is not an unreadable number. Distinct
                # MIN/MAX values plus BSC/REF remain conflicting below.
                scalar_units.add(units)
                continue
            value=number(text)
            if value is None:
                invalid=True
                invalid_units.add(units)
                issues.append({'code':'DIMENSION_CELL_UNREADABLE','page':page,'row':index,'symbol':key,'raw':text})
                continue
            group=groups.setdefault(units,{})
            if re.search(r'BSC',text,re.I): scalar_units.add(units)
            elif re.search(r'(?:TYP|REF|REFERENCE)\.?\s*(?:\(\d+\.?\))?$',text,re.I):
                field='nominal_mm' if re.search('TYP',text,re.I) else 'value_mm'
            if field in group and group[field]!=value:
                invalid=True
                invalid_units.add(units)
                issues.append({'code':'DIMENSION_COLUMN_ROLE_CONFLICT','page':page,'row':index,'symbol':key,'raw':row})
            group[field]=value
        if not groups or (invalid and ('mm' not in groups or 'mm' in invalid_units)): continue
        # A damaged secondary inch cell must not erase intact mm evidence.
        # Its issue is retained above, so the document still requires review.
        for bad_unit in invalid_units:
            groups.pop(bad_unit,None)
            scalar_units.discard(bad_unit)
        invalid=False
        for units in scalar_units:
            vals=set(groups[units].values())
            if len(vals)!=1:
                invalid=True
                issues.append({'code':'DIMENSION_BSC_CONFLICT','page':page,'row':index,'symbol':key,'raw':row})
            else: groups[units]={'value_mm':next(iter(vals))}
        if invalid: continue
        selected=groups.get('mm') or {k:v*25.4 for k,v in groups.get('inch',{}).items()}
        values=[selected[k] for k in ('min_mm','nominal_mm','max_mm','value_mm') if k in selected]
        if not values: continue
        if values!=sorted(values) or any(v<0 or v>1000 for v in values):
            issues.append({'code':'DIMENSION_RANGE_CONFLICT','page':page,'row':index,'symbol':key,'raw':row})
            continue
        if 'mm' in groups and 'inch' in groups:
            for field in groups['mm'].keys() & groups['inch'].keys():
                if abs(groups['mm'][field]-groups['inch'][field]*25.4)>0.02:
                    authority = None if _opposes_metric_authority(context) else (
                        unit_authority or _metric_authority(context, page))
                    issue = {'code':'DIMENSION_UNIT_CONFLICT','page':page,'row':index,'symbol':key,'field':field,'raw':row}
                    if authority:
                        issue.update(severity="warning", resolution="explicit_primary_unit",
                                     authority=authority)
                    issues.append(issue)
        raw=' | '.join(clean(c) for c in row)
        evidence=SourceEvidence(page=page,figure_or_table=f'{label} row {index+1}',raw_text=raw,
                                extraction_method=method,confidence=.95 if method=='ocr' else .96,evidence_scope=scope)
        measurement=Measurement(**selected,original_value=raw,original_unit='mm' if 'mm' in groups else 'inch',evidence=[evidence])
        for name in key.split('/'):
            if name in conflicted: continue
            if name in found and found[name].model_dump(exclude={'evidence','original_value'})!=measurement.model_dump(exclude={'evidence','original_value'}):
                issues.append({'code':'DIMENSION_DUPLICATE_CONFLICT','page':page,'symbol':name})
                found.pop(name,None)
                conflicted.add(name)
            else: found[name]=measurement
    return found,issues,schema


def _following_unit_footnote(rows, current, following):
    """Resolve a table-header footnote, never a nearby unreferenced assertion."""
    if following.number != current.number + 1 or _opposes_metric_authority(current.text) or _opposes_metric_authority(following.text):
        return None
    if not re.search(r'^\s*Notes\s*$', following.text, re.I | re.M):
        return None
    # A new figure/table/package section ends this notes-only continuation.
    if re.search(r'^\s*(?:Table|Figure)\s+\d|\b(?:QFN|QFP|LQFP|TQFP|SOIC|DFN|BGA)[-_ ]?\d+\b', following.text, re.I | re.M):
        return None
    references = {m for row in rows[:2] for cell in row
                  for m in re.findall(r'\binch(?:es)?\s*\(\s*(\d+)\.?\s*\)', clean(cell), re.I)}
    if len(references) != 1:
        return None
    reference = next(iter(references))
    entries = list(re.finditer(r'^\s*(\d+)\.\s*', following.text, re.M))
    matches = [i for i,m in enumerate(entries) if m[1] == reference]
    if len(matches) != 1:
        return None
    i = matches[0]
    note = following.text[entries[i].end():entries[i+1].start() if i+1 < len(entries) else len(following.text)]
    authority = _metric_authority(note, following.number)
    if authority:
        return {**authority, 'reference_page': current.number, 'footnote': reference,
                'binding': 'table_unit_header_to_adjacent_numbered_note'}
    return None


def read_document_tables(records):
    result={}
    issues=[]
    previous=None
    previous_page=None
    previous_identity=None
    for record_index, record in enumerate(records):
        identity=set(re.findall(r'\b(?:U?F?QFPN?|[LT]?QFP|[ST]?SSOP|SOIC|DFN|BGA)[-_ ]?\d+\b',record.text,re.I))
        authority = _metric_authority(record.text, record.number)
        if (authority is None and not _opposes_metric_authority(record.text)
                and len(identity) == 1 and record_index+1 < len(records)):
            following = records[record_index+1]
            following_identity = set(re.findall(r'\b(?:U?F?QFPN?|[LT]?QFP|[ST]?SSOP|SOIC|DFN|BGA)[-_ ]?\d+\b', following.text,re.I))
            if following.number == record.number+1 and following_identity == identity:
                authority = _metric_authority(following.text, following.number)
        if previous_page is None or record.number!=previous_page+1 or (identity and previous_identity and identity!=previous_identity): previous=None
        local={}
        conflicted=set()
        tables=getattr(record,'located_dimension_tables',[]) or [dict(rows=t,scope='package_outline',method='table_extract') for t in record.tables]
        for table_index,table in enumerate(tables):
            for code in table.get('review_codes',[]):
                issues.append({'code':code,'page':record.number,'table':table_index+1,'bbox':table.get('bbox')})
            for name in set(table.get('channel_conflicts',[])):
                issues.append({'code':'DIMENSION_CHANNEL_DISAGREEMENT','page':record.number,'symbol':name})
            table_authority = authority
            if table_authority is None and record_index+1 < len(records):
                table_authority = _following_unit_footnote(table['rows'], record, records[record_index+1])
            values,problems,schema=read_table(table['rows'],record.number,record.text,previous,
                method=table['method'],scope=table['scope'],label=f'Dimension table {table_index+1}',
                unit_authority=table_authority)
            # Preserve separate table evidence; land-pattern symbols must never
            # overwrite same-named body/terminal symbols on the same page.
            issues.extend(problems)
            if table['scope']!='package_outline':
                continue
            for name,value in values.items():
                if name in conflicted: continue
                if name in local and local[name].model_dump(exclude={'evidence','original_value'})!=value.model_dump(exclude={'evidence','original_value'}):
                    issues.append({'code':'DIMENSION_VARIANT_AMBIGUOUS','page':record.number,'symbol':name})
                    local.pop(name,None)
                    conflicted.add(name)
                else: local[name]=value
            previous=schema
        if local: result[record.number]=local
        previous_page=record.number
        previous_identity=identity or previous_identity
    return result,issues


def tables_from_ocr(tokens):
    """Compatibility API; PDF and OCR now share one spatial table reader."""
    from .positioned_tables import positioned_tables
    return [table['rows'] for table in positioned_tables(tokens,method='ocr')]
