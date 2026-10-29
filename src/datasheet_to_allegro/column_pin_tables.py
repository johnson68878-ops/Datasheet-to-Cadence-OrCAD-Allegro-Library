"""Header-anchored text columns for unruled and watermark-overlaid pin tables.

Never uses source filenames, IC names, or fixed page/column coordinates.
Only explicit physical-pin headers and explicitly marked continuations qualify.
"""
from __future__ import annotations
import re
import pdfplumber


def horizontal_content(page):
    # A non-destructive extraction view: preserve the original PDF and all
    # graphics; exclude rotated characters only for horizontal table reading.
    return page.filter(lambda o: o.get('object_type') != 'char' or (
        abs(o.get('matrix',(1,0,0,1))[1]) < .001 and
        abs(o.get('matrix',(1,0,0,1))[2]) < .001))


def word_lines(words):
    rows=[]
    for w in sorted(words,key=lambda w:(w['top'],w['x0'])):
        if not rows or abs(w['top']-rows[-1][0]['top'])>3:
            rows.append([w])
        else:
            rows[-1].append(w)
    return [sorted(r,key=lambda w:w['x0']) for r in rows]


def header_schema(line, words):
    labels=[w['text'].lower() for w in line]
    if 'ball' in labels and 'signal' in labels and 'description' in labels:
        lookup={w['text'].lower():w['x0'] for w in line}
        if not ('i/o' in lookup and 'type' in lookup):
            return None
        return {'number':lookup['ball'],'name':lookup['signal'],
                'end_name':lookup['i/o'],'type':lookup['i/o'],
                'buffer':lookup['type'],'description':lookup['description'],'mode':'ball'}
    if 'multi-function' in labels and 'name' in labels:
        name_index=labels.index('name')
        name_x=line[name_index-1]['x0'] if name_index and labels[name_index-1]=='pin' else line[name_index]['x0']
        number_words=[w for w in words if w['text'].lower()=='order' and
                      abs(w['top']-line[0]['top'])<18 and w['x0']<name_x]
        if not number_words:
            return None
        multi=next(w['x0'] for w in line if w['text'].lower()=='multi-function')
        power=next((w['x0'] for w in line if w['text'].lower()=='pad' and w['x0']>multi),None)
        if power is None:
            return None
        return {'number':number_words[0]['x0'],'name':name_x,
                'end_name':multi,'description':power,'mode':'mux'}
    return None


def read_column_pin_tables(path):
    result=[]
    previous=None
    with pdfplumber.open(path) as pdf:
        for page_no,page in enumerate(pdf.pages,1):
            clean=horizontal_content(page)
            words=clean.extract_words(x_tolerance=2,y_tolerance=2)
            lines=word_lines(words)
            headers=[(i,s) for i,line in enumerate(lines) if (s:=header_schema(line,words))]
            continuation=bool(previous and re.search(r'from\s*previous\s*page',clean.extract_text() or '',re.I))
            if not headers and not continuation:
                previous=None
                page.close(); continue
            regions=[]
            if continuation:
                regions.append((0,headers[0][0] if headers else len(lines),previous))
            for j,(idx,schema) in enumerate(headers):
                regions.append((idx+1,headers[j+1][0] if j+1<len(headers) else len(lines),schema))
            for start,end,schema in regions:
                for k in range(start,end):
                    footer=re.sub(r'\s+','', ' '.join(w['text'] for w in lines[k])).lower()
                    if any(token in footer for token in ('allrightsreserved','unauthorizedreproduction','proprietaryand')):
                        end=k
                        break
                def column(line,left,right):
                    return ' '.join(w['text'] for w in line if left-2<=w['x0']<right-2)
                anchors=[]
                for i in range(start,end):
                    n=column(lines[i],schema['number'],schema['name'])
                    name=column(lines[i],schema['name'],schema['end_name'])
                    if re.fullmatch(r'(?:[A-Z]{0,3}\d+[, ]*)+',n) and name:
                        anchors.append((i,n,name))
                for j,(idx,number,name) in enumerate(anchors):
                    stop=anchors[j+1][0] if j+1<len(anchors) else end
                    raw_type=''; description=''; functions=[]
                    if schema['mode']=='ball':
                        raw_type=column(lines[idx],schema['type'],schema['buffer'])
                        # Header captions and cell contents can have different
                        # alignment. A separated, explicit buffer code is not
                        # part of the signal identifier.
                        embedded=re.search(r'\s+(I(?:U|D|S|US|DS)?/O\d+|I[UDS]|O\d+)$',name)
                        if embedded and (not raw_type or raw_type.upper() in {'CMOS', 'TTL', 'SCHMITT'}):
                            raw_type=embedded.group(1)
                            name=name[:embedded.start()].rstrip()
                        buffer=column(lines[idx],schema['buffer'],schema['description'])
                        if buffer=='P': raw_type='P'
                        description=column(lines[idx],schema['description'],page.width)
                        # Spaces in a PDF identifier can represent separated
                        # text runs; retain the visible spelling as evidence.
                    else:
                        # The physical cell can span multiple wrapped lines.
                        ns=[]; names=[]
                        for row in lines[idx:stop]:
                            n=column(row,schema['number'],schema['name'])
                            if n and re.fullmatch(r'[\d, ]+',n): ns.append(n)
                            nm=column(row,schema['name'],schema['end_name'])
                            if nm and re.fullmatch(r'[A-Za-z0-9_/#.\[\]-]+',nm): names.append(nm)
                            fn=column(row,schema['end_name'],schema['description'])
                            fn=re.sub(r'\s+','',fn)
                            if fn and fn!='NA' and re.fullmatch(r'[A-Za-z0-9_/#.\[\]-]+',fn): functions.append(fn)
                        number=' '.join(ns)
                        name=''.join(names)
                    if not name or not re.fullmatch(r'[A-Za-z0-9_/#.\[\] ()+-]+',name):
                        continue
                    for n in re.findall(r'[A-Z]{0,3}\d+',number):
                        result.append({'pin_number':n,'pin_name':name,'raw_type':raw_type,
                                       'description':description,'page':page_no,
                                       'functions':functions,'method':'horizontal_header_columns'})
                previous=schema
            page.close()
    if any(r['functions'] for r in result):
        evidence=read_signal_type_tables(path)
        for row in result:
            row['function_evidence']={}
            for name in [row['pin_name']]+row['functions']:
                key=(row['pin_number'],re.sub(r'^PAD_','',name))
                found=evidence.get(key)
                if found:
                    row['function_evidence'][name]=found
                    if name==row['pin_name'] and not row['raw_type']:
                        row['raw_type']=found['type']
    return result


def read_signal_type_tables(path):
    """Join explicit secondary function/type tables by BOTH pin and function."""
    result={}
    with pdfplumber.open(path) as pdf:
        for page_no,page in enumerate(pdf.pages,1):
            lines=word_lines(horizontal_content(page).extract_words(x_tolerance=2,y_tolerance=2))
            active=False
            for line in lines:
                text=' '.join(w['text'] for w in line)
                if re.search(r'Signal Name\s+Signal Type\s+Function\s+Pin',text,re.I):
                    active=True
                    pin_x=line[-1]['x0']-5
                    continue
                if not active: continue
                if 'Proprietary' in text or 'rights reserved' in text:
                    active=False; continue
                # Names remain one identifier; numbers must be in the Pin column.
                m=re.match(r'^([A-Za-z0-9_/#]+)\s+(Input|Output|Bidirectional|Power|Ground)\b',text,re.I)
                if not m: continue
                number_text=' '.join(w['text'] for w in line if w['x0']>=pin_x)
                if not re.fullmatch(r'[\d, ]+',number_text): continue
                for number in re.findall(r'\d+',number_text):
                    key=(number,m.group(1))
                    item={'type':m.group(2),'page':page_no,'description':f'PDF p{page_no}: {text}'}
                    if key in result and result[key]['type']!=item['type']:
                        raise ValueError(f'Conflicting function type: {key}')
                    result[key]=item
            page.close()
    return result


def read_ball_map_labels(path):
    """Read labeled matrix cells using repeated numbered axes, not row text."""
    result={}
    with pdfplumber.open(path) as pdf:
        for page_no,page in enumerate(pdf.pages,1):
            clean=horizontal_content(page)
            if not re.search(r'ball\s*map',clean.extract_text() or '',re.I):
                page.close(); continue
            lines=word_lines(clean.extract_words())
            axes=[line for line in lines if len(line)>=3 and all(re.fullmatch(r'\d+',w['text']) for w in line)]
            if len(axes)<2: page.close(); continue
            top,bottom=axes[0],axes[-1]
            if [w['text'] for w in top]!=[w['text'] for w in bottom]:
                page.close(); continue
            xs=[(w['x0']+w['x1'])/2 for w in top]
            pitch=min(b-a for a,b in zip(xs,xs[1:]))
            labels=[w for w in clean.extract_words() if re.fullmatch(r'[A-Z]{1,3}',w['text']) and
                    (w['x1']<xs[0]-pitch/2 or w['x0']>xs[-1]+pitch/2) and top[0]['top']<w['top']<bottom[0]['top']]
            labels.sort(key=lambda w:w['top'])
            if len(labels)<3: page.close(); continue
            dy=min(b['top']-a['top'] for a,b in zip(labels,labels[1:]))
            if dy<=0: page.close(); continue
            for row in labels:
                for col,x in zip(top,xs):
                    chars=[c for c in clean.chars if x-pitch/2<=(c['x0']+c['x1'])/2<x+pitch/2 and
                           row['top']-dy*.35<=c['top']<row['top']+dy*.65]
                    name=''.join(c['text'] for c in sorted(chars,key=lambda c:c['x0'])).strip()
                    if name and re.fullmatch(r'[A-Za-z0-9_/#.\[\]-]+',name):
                        key=row['text']+col['text']
                        if key in result and result[key]['name']!=name:
                            raise ValueError(f'Ball Map conflict at {key}')
                        result[key]={'name':name,'page':page_no}
            page.close()
    return result
