"""Spatial table recovery shared by PDF glyphs and OCR, with source rectangles.

No part identities, page ranges or dimension answers live here. Slanted glyphs
remain in the source audit; only the horizontal reading channel excludes them.
"""
from __future__ import annotations

import math
import re
from statistics import median

from .dimension_lexicon import clean, number, role, symbol, symbol_header, unit


def horizontal_character(obj):
    if obj.get('object_type') != 'char':
        return True
    a, b, c, d, *_ = obj.get('matrix', (1, 0, 0, 1, 0, 0))
    return abs(b) <= max(abs(a), 1e-9)*.02 and abs(c) <= max(abs(d), 1e-9)*.02


def vector_channel(page):
    horizontal = page.filter(horizontal_character).dedupe_chars(tolerance=.5)
    tokens = []
    for word in horizontal.extract_words(x_tolerance=1.5, y_tolerance=2):
        x0, y0, x1, y1 = word['x0'], word['top'], word['x1'], word['bottom']
        tokens.append({'text': word['text'], 'confidence': 1.0,
                       'polygon': [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]})
    separated = [{k: c.get(k) for k in ('text','x0','top','x1','bottom','matrix')}
                 for c in page.chars if not horizontal_character(c)]
    return horizontal, tokens, separated


def located_tokens(tokens):
    result = []
    for token in tokens:
        polygon = token.get('polygon', [])
        if len(polygon) != 4:
            continue
        xs, ys = zip(*polygon)
        if not all(math.isfinite(v) for v in (*xs,*ys)):
            continue
        result.append(dict(text=clean(token.get('text')), confidence=token.get('confidence',0),
                           x=(min(xs)+max(xs))/2, y=(min(ys)+max(ys))/2,
                           h=max(ys)-min(ys), x0=min(xs), x1=max(xs), y0=min(ys), y1=max(ys)))
    return result


def parameter_rows(tokens):
    """Read spatial, named Min/Nom/Max rows, including wrapped parameter names.

    Unlike a symbol table these rows explicitly name the physical quantity.
    Never read numbers from a neighboring drawing or infer a missing axis.
    """
    located = located_tokens(tokens)
    results = []
    for header in located:
        if header['text'].lower() != 'parameter' or header['confidence'] < .9:
            continue
        h = max(header['h'], 1)
        columns = sorted((t for t in located if t['x0'] > header['x1']
                          and abs(t['y']-header['y']) < 2*h
                          and (role(t['text']) or t['text'].lower() == 'normal')),
                         key=lambda t: t['x'])
        roles = [role(t['text']) or 'nominal_mm' for t in columns]
        if len(columns) != 3 or set(roles) != {'min_mm','nominal_mm','max_mm'}:
            continue
        if not any(unit(t['text']) == 'mm' and abs(t['y']-header['y']) < 3*h
                   and header['x'] < t['x'] < columns[-1]['x1'] for t in located):
            continue
        gap = min(b['x']-a['x'] for a,b in zip(columns,columns[1:]))
        cells = sorted((t for t in located if t['y'] > max(c['y1'] for c in columns)+h
                        and min(abs(t['x']-c['x']) for c in columns) < gap*.3
                        and (number(t['text']) is not None or t['text'] in {'/','-','—'})),
                       key=lambda t:t['y'])
        rows = []
        for token in cells:
            if not rows or token['y']-rows[-1][0]['y'] > h*.7:
                rows.append([])
            rows[-1].append(token)
        previous = header['y']
        for index, row in enumerate(rows):
            y = sum(t['y'] for t in row)/len(row)
            if y-previous > 7*h:
                break
            lower = (previous+y)/2
            upper = (y+rows[index+1][0]['y'])/2 if index+1 < len(rows) else y+1.7*h
            labels = [t for t in located if t['x1'] < columns[0]['x']-gap*.35
                      and t['x0'] > header['x0']-4*h and lower < t['y'] < upper]
            previous = y
            if not labels or any(t['confidence'] < .9 for t in labels):
                continue
            values = {}
            invalid = False
            for cell in row:
                key = roles[min(range(3),key=lambda i:abs(cell['x']-columns[i]['x']))]
                value = number(cell['text'])
                if key in values or (value is not None and cell['confidence'] < .95):
                    invalid = True
                values[key] = value
            numeric = [values.get(key) for key in ('min_mm','nominal_mm','max_mm') if values.get(key) is not None]
            if invalid or not numeric or numeric != sorted(numeric):
                continue
            label = ' '.join(t['text'] for t in sorted(labels,key=lambda t:(round(t['y']/h),t['x'])))
            if re.search(r'\bcount\b|\bnumber\s+of\b', label, re.I):
                continue  # A ball count is not a measurement in millimeters.
            results.append(dict(label=label, values={k:v for k,v in values.items() if v is not None},
                                confidence=min(t['confidence'] for t in labels+row),
                                raw=label+' | '+' | '.join(t['text'] for t in sorted(row,key=lambda t:t['x'])),
                                bbox=[min(t['x0'] for t in labels),lower,columns[-1]['x1'],upper]))
    return results


def _joined_row_tokens(tokens, height):
    """Join numeric qualifiers split into adjacent words, without joining columns."""
    result=[]
    for token in tokens:
        if result and re.fullmatch(r'(?:BSC|TYP|REF|REFERENCE)\.?',token['text'],re.I) and number(result[-1]['text']) is not None and token['x0']-result[-1]['x1']<height:
            last=result[-1]
            last['text']+=' '+token['text']
            last['x1']=token['x1']
            last['x']=(last['x0']+last['x1'])/2
        else:
            result.append(dict(token))
    return result


def _scope(tokens, header):
    # Captions on the same reading column beat distant page-wide text. Retain
    # mechanical and recommended-land tables separately even on a single page.
    lines = {}
    h = max(header['h'],1)
    for t in tokens:
        if 0 < header['y']-t['y'] < 28*h:
            line = round(t['y']/max(h*.7,1))
            lines.setdefault(line,[]).append(t)
    captions = []
    for line in lines.values():
        text=' '.join(t['text'] for t in sorted(line,key=lambda t:t['x']))
        if re.search(r'land\s*pattern|pad\s*layout|mounting\s*pad|board\s*layout|pcb\s*layout|soldering\s*footprint|焊盘|焊盤',text,re.I):
            captions.append((max(t['y'] for t in line),'land_pattern'))
        elif re.search(r'package\s*(?:outline|dimension)|mechanical|外形|封装|封裝',text,re.I):
            captions.append((max(t['y'] for t in line),'package_outline'))
    return max(captions)[1] if captions else 'package_outline'


def _scalar_table(located,header,method):
    h=max(header['h'],1)
    units=[t for t in located if unit(t['text']) and t['x']>header['x']+h and abs(t['y']-header['y'])<h]
    if len(units)!=1:
        return None
    unit_token=units[0]
    labels=sorted((t for t in located if symbol(t['text']) and abs(t['x']-header['x'])<h
                   and t['y']>header['y']+h),key=lambda t:t['y'])
    rows=[['Symbol',unit_token['text']],['','Value']]
    last=header['y']
    for label in labels:
        if label['y']-last>5*h:
            break
        cells=[t for t in located if abs(t['x']-unit_token['x'])<h*1.5 and abs(t['y']-label['y'])<h*.6]
        if len(cells)!=1:
            break
        rows.append([label['text'],cells[0]['text'] if cells[0]['confidence']>=.95 else 'UNREADABLE('+cells[0]['text']+')'])
        last=label['y']
    if len(rows)<4:
        return None
    return dict(rows=rows,bbox=[header['x0'],header['y0'],unit_token['x1'],last+h],scope=_scope(located,header),method=method)


def transposed_tables(located,method):
    """Retain aligned stacked limits, with an explicit inference/review flag.

    Without MIN/MAX labels this is a candidate interval, never silent authority.
    At least three columns must share the same descending, two-baseline layout.
    """
    tables=[]
    for header in located:
        if header['text'].upper()!='UNIT' or header['confidence']<.9:
            continue
        h=max(header['h'],1)
        labels=sorted((t for t in located if symbol(t['text']) and t['confidence']>=.9
                       and t['x']>header['x']+h and abs(t['y']-header['y'])<h),key=lambda t:t['x'])
        units=[t for t in located if unit(t['text']) and t['confidence']>=.9
               and abs(t['x']-header['x'])<h and h*.5<t['y']-header['y']<5*h]
        if len(labels)<3 or len(units)!=1:
            continue
        rows=[['Symbol',units[0]['text'],''],['','Max','Min']]
        bottom=header['y']
        for label in labels:
            cells=sorted((t for t in located if abs(t['x']-label['x'])<h
                          and h*.5<t['y']-header['y']<5*h and number(t['text']) is not None),key=lambda t:t['y'])
            if len(cells)!=2 or any(t['confidence']<.95 for t in cells) or number(cells[0]['text'])<number(cells[1]['text']):
                continue
            rows.append([label['text'],cells[0]['text'],cells[1]['text']])
            bottom=max(bottom,cells[-1]['y1'])
        if len(rows)>=5:
            tables.append(dict(rows=rows,bbox=[header['x0'],header['y0'],labels[-1]['x1'],bottom],
                scope='package_outline',method=method,orientation='transposed_stacked_limits',
                review_codes=['DIMENSION_STACKED_LIMITS_INFERRED']))
    return tables


def positioned_tables(tokens, *, method='ocr'):
    """Recover a table from explicit headers and aligned cells, not sorted digits.

    Coordinates may be PDF points or image pixels; thresholds scale with text.
    Borderless and over-segmented vector tables use the same reader as scans.
    A returned rectangle makes multiple tables auditable, not one page-global
    dictionary. Low-confidence numeric tokens are retained as unreadable cells.
    """
    located=located_tokens(tokens)
    headers=[t for t in located if symbol_header(t['text']) and t['confidence']>=.9
             and not (t['text'].lower()=='dimension' and any(unit(u['text']) and 0<u['x']-t['x']<6*max(t['h'],1)
                     and abs(u['y']-t['y'])<max(t['h'],1) for u in located))]
    tables=transposed_tables(located,method)
    for header in headers:
        h=max(header['h'],1)
        roles=[t for t in located if (role(t['text']) or t['text'].upper()=='MIM')
               and t['confidence']>=.9 and t['x']>header['x'] and abs(t['y']-header['y'])<=4*h]
        if not roles:
            scalar=_scalar_table(located,header,method)
            if scalar:
                tables.append(scalar)
            continue
        role_y=min(roles,key=lambda t:abs(t['y']-header['y']))['y']
        roles=sorted((t for t in roles if abs(t['y']-role_y)<h*.7),key=lambda t:t['x'])
        # Stop at the next side-by-side table's symbol header.
        next_x=min((t['x'] for t in headers if t['x']>header['x']+h and abs(t['y']-header['y'])<h),default=float('inf'))
        roles=[t for t in roles if t['x']<next_x]
        if any(role(t['text']) and t['confidence']<.9 and header['x']<t['x']<next_x and abs(t['y']-role_y)<h for t in located):
            continue  # A missing/weak header must not shift subsequent columns.
        centers=[header['x']]+[t['x'] for t in roles]
        if len(roles)<2 or min(b-a for a,b in zip(centers,centers[1:]))<h:
            continue
        if any(t['text'].upper()=='MIM' for t in roles) and not {'max_mm','nominal_mm'} <= {role(t['text']) for t in roles}:
            continue
        # Join letter-spaced unit headings only within their actual text line.
        unit_tokens=[t for t in located if unit(t['text']) and t['text'].lower()!='in' and t['confidence']>=.9
                     and -h < role_y-t['y'] < 9*h and header['x']-h<t['x']<next_x]
        for y in sorted({round(t['y']/h) for t in located if 0<role_y-t['y']<9*h}):
            line=sorted((t for t in located if round(t['y']/h)==y and header['x']<t['x']<next_x),key=lambda t:t['x'])
            if line:
                joined=' '.join(t['text'] for t in line)
                if unit(joined) and not any(unit(t['text']) for t in line):
                    unit_tokens.append(dict(text=joined,x=median(t['x'] for t in line)))
        unit_row=['']+[min(unit_tokens,key=lambda u:abs(u['x']-t['x']))['text'] if unit_tokens else '' for t in roles]
        rows=[unit_row,['Symbol']+[t['text'] for t in roles]]
        labels=sorted((t for t in located if symbol(t['text']) and t['confidence']>=.9
                       and abs(t['x']-header['x'])<h*1.2
                       and t['y']>max(role_y,header['y'])+h*.6),key=lambda t:t['y'])
        last_y=max(role_y,header['y'])
        bottom=last_y
        for label in labels:
            if label['y']-last_y>6*h:
                break
            # Another header ends this region, even if the symbol column aligns.
            if any(last_y<t['y']<label['y'] for t in headers if abs(t['x']-header['x'])<2*h):
                break
            label_text=label['text']
            subscripts=[t for t in located if re.fullmatch(r'\d',t['text']) and -h*.3<=t['x0']-label['x1']<h*.45
                        and 0<t['y']-label['y']<h*.5 and t['h']<label['h']*.9]
            if len(subscripts)==1:
                label_text+=subscripts[0]['text']
            row=[label_text]+['']*(len(rows[0])-1)
            values=sorted((t for t in located if abs(t['y']-label['y'])<=h*.62
                           and header['x']+h<t['x']<next_x),key=lambda t:t['x'])
            if values and not any(number(t['text']) is not None or re.search(r'\d',t['text']) for t in values):
                break  # Prose footnotes below the table are not dimension rows.
            for value in _joined_row_tokens(values,h):
                col=min(range(1,len(centers)),key=lambda i:abs(centers[i]-value['x']))
                tolerance=min(centers[col]-centers[col-1],centers[col+1]-centers[col] if col+1<len(centers) else centers[col]-centers[col-1])*.48
                if abs(value['x']-centers[col])>tolerance:
                    # BSC/REF/TYP is often centered over merged numeric columns.
                    merged=[(i,i+1) for i in range(1,len(centers)-1)
                            if abs(value['x']-(centers[i]+centers[i+1])/2)<(centers[i+1]-centers[i])*.12
                            and unit(unit_row[i])==unit(unit_row[i+1])]
                    if number(value['text']) is None or len(merged)!=1:
                        continue
                    # An unqualified value centered in a merged Min/Max cell is
                    # a scalar, not a lower limit. Add a schema column explicitly.
                    if not re.search(r'BSC|REF|TYP',value['text'],re.I):
                        source_unit=unit_row[merged[0][0]]
                        col=next((i for i in range(len(centers),len(rows[0])) if rows[0][i]==source_unit),None)
                        if col is None:
                            col=len(rows[0])
                            rows[0].append(source_unit)
                            rows[1].append('Value')
                            for previous_row in rows[2:]: previous_row.append('')
                            row.append('')
                raw=value['text'] if value['confidence']>=.95 else 'UNREADABLE('+value['text']+')'
                row[col]=(row[col]+' '+raw).strip()
            if any(row[1:]):
                rows.append(row)
                bottom=label['y1']
            last_y=label['y']
        if len(rows)>2:
            tables.append({'rows':rows,'bbox':[header['x0'],min(header['y0'],role_y-h),roles[-1]['x1'],bottom],
                           'scope':_scope(located,header),'method':method})
    return tables
