"""Local second-engine evidence; disagreement never increases confidence."""
import re
from functools import lru_cache
from threading import Lock

_LOCK = Lock()


@lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def recognize_image(path):
    with _LOCK:
        rows, _ = _engine()(str(path))
    tokens = [{"polygon": [[float(x), float(y)] for x, y in box], "text": text, "confidence": float(score)}
              for box, text, score in (rows or [])]
    return {"engine": "rapidocr_onnxruntime", "tokens": tokens,
            "text": "\n".join(t["text"] for t in tokens)}


def recognize_table_regions(path, tokens, tesseract=None, *, language=None):
    """Re-read small tables at their own scale instead of shrinking an entire page.

    Regions come from visible header/row positions, not page numbers or parts.
    Results stay a separate evidence channel; the caller audits disagreements.
    """
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from PIL import Image
    from .positioned_tables import located_tokens
    from .dimension_tables import symbol_header, role, symbol

    located=located_tokens(tokens)
    results=[]
    with Image.open(path) as image, TemporaryDirectory(prefix='ds2a_table_ocr_') as directory:
        for header in located:
            transposed=header['text'].upper()=='UNIT'
            parameter=header['text'].lower()=='parameter'
            damaged_symbol = bool(re.fullmatch(r'symbo[il1]',header['text'],re.I))
            if not (symbol_header(header['text']) or transposed or parameter or damaged_symbol) or header['confidence']<.9:
                continue
            h=max(header['h'],1)
            roles=[t for t in located if (role(t['text']) or (parameter and t['text'].lower()=='normal')) and t['x']>header['x'] and abs(t['y']-header['y'])<4*h]
            if transposed:
                roles=[t for t in located if symbol(t['text']) and t['x']>header['x'] and abs(t['y']-header['y'])<h]
            if len(roles)<2:
                # A damaged header can propose a crop, never accepted values.
                # Re-recognition must still recover a valid numeric schema.
                headings=[t for t in located if re.search(r'\bdimensions\b',t['text'],re.I)
                          and t['x']>header['x'] and abs(t['y']-header['y'])<h]
                if not (headings and (damaged_symbol or symbol_header(header['text']))):
                    continue
                roles += headings
            labels=sorted((t for t in located if (symbol(t['text']) or parameter) and abs(t['x']-header['x'])<1.5*h and t['y']>header['y']),key=lambda t:t['y'])
            end=header['y']
            for label in labels:
                if label['y']-end>6*h:
                    break
                end=label['y1']
            if transposed:
                end=header['y']+4*h
            if end-header['y']<3*h:
                continue
            right=max(t['x1'] for t in roles)
            if transposed:
                right=max([right]+[t['x1'] for t in located if header['y']<t['y']<end and t['x']>header['x']])
            box=[max(0,int(header['x0']-h)),max(0,int(min(header['y0'],min(t['y0'] for t in roles))-3*h)),
                 min(image.width,int(right+2*h)),min(image.height,int(end+2*h))]
            if any(abs(box[0]-r['bbox'][0])<h and abs(box[1]-r['bbox'][1])<4*h for r in results):
                continue
            crop=Path(directory)/f'{len(results)}.png'
            image.crop(box).save(crop)
            try:
                result=recognize_image(crop)
            except Exception as exc:
                result={'engine':'rapidocr_onnxruntime','tokens':[], 'text':'',
                        'error':f'{type(exc).__name__}: {exc}'}
            result['independent_language']=language
            result['independent_status']='unavailable' if not tesseract or not language else 'attempted'
            if tesseract and language:
                errors=[]
                result['independent_tokens']=_tesseract_region(
                    crop,tesseract,result['tokens'],language=language,errors=errors)
                result['independent_errors']=errors
                result['independent_status']='partial_error' if errors else ('completed' if result['independent_tokens'] else 'no_tokens')
                result['cross_checked_numeric_tokens']=confirm_numeric_tokens(result['tokens'],result['independent_tokens'])
                result['cross_checked_header_tokens']=confirm_header_tokens(result['tokens'],result['independent_tokens'])
            for token in result['tokens']:
                token['polygon']=[[x+box[0],y+box[1]] for x,y in token['polygon']]
            for token in result.get('independent_tokens',[]):
                token['polygon']=[[x+box[0],y+box[1]] for x,y in token['polygon']]
            results.append({'bbox':box,**result})
    return results


def _tesseract_region(path, executable, header_tokens=(), *, language='eng', errors=None):
    import csv
    import io
    import subprocess
    from pathlib import Path
    import cv2
    import numpy as np
    def execute(image, psm, timeout):
        try:
            run=subprocess.run([str(executable),str(image),'stdout','-l',language,'--psm',psm,'tsv'],
                               capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            if errors is not None:
                errors.append({'psm':psm,'language':language,'error':f'{type(exc).__name__}: {exc}'})
            return None
        if run.returncode:
            if errors is not None:
                errors.append({'psm':psm,'language':language,'returncode':run.returncode,'error':run.stderr})
            return None
        return run
    # Grid lines interfere with character segmentation in the independent
    # engine. Remove only long horizontal/vertical strokes in this OCR channel;
    # the original crop and RapidOCR channel stay unchanged for audit.
    pixels=cv2.imdecode(np.fromfile(str(path),dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
    prepared=Path(path).with_name(Path(path).stem+'_gridless.png')
    if pixels is not None:
        binary=cv2.threshold(pixels,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[1]
        h,w=binary.shape
        horizontal=cv2.morphologyEx(binary,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(max(40,w//6),1)))
        vertical=cv2.morphologyEx(binary,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(1,max(40,h//6))))
        cleaned=255-cv2.subtract(binary,cv2.bitwise_or(horizontal,vertical))
        cleaned=cv2.resize(cleaned,None,fx=2,fy=2,interpolation=cv2.INTER_CUBIC)
        cv2.imencode('.png',cleaned)[1].tofile(str(prepared))
    run=execute(prepared if pixels is not None else path,'6',45)
    result=[]
    for row in csv.DictReader(io.StringIO(run.stdout if run else ''),delimiter='\t'):
        if not row.get('text','').strip():
            continue
        scale=2 if pixels is not None else 1
        x,y,w,h=(int(row[k])/scale for k in ('left','top','width','height'))
        result.append(dict(text=row['text'],confidence=float(row['conf'])/100,preprocessing='long_table_lines_removed',
                           polygon=[[x,y],[x+w,y],[x+w,y+h],[x,y+h]]))
    # Re-segment uncertain header words as single-line cells. Coordinates come
    # from OCR, never from a part-specific table template.
    if pixels is not None:
        from .positioned_tables import located_tokens
        from .dimension_lexicon import role
        for index,header in enumerate(located_tokens(header_tokens)):
            if not role(header['text']) or header['confidence']>=.9:
                continue
            if any(t['confidence']>=.85 and role(t['text'])==role(header['text'])
                   and abs(t['x']-header['x'])<header['h'] and abs(t['y']-header['y'])<header['h']*.6
                   for t in located_tokens(result)):
                continue
            margin=max(2,int(header['h']*.25))
            left=max(0,int(header['x0']-margin))
            top=max(0,int(header['y0']-margin))
            right=min(pixels.shape[1],int(header['x1']+margin))
            bottom=min(pixels.shape[0],int(header['y1']+margin))
            cell=Path(path).with_name(f'header_{index}.png')
            raw_cell=cv2.resize(pixels[top:bottom,left:right],None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
            cv2.imencode('.png',raw_cell)[1].tofile(str(cell))
            retry=execute(cell,'7',15)
            if retry is None:
                continue
            for row in csv.DictReader(io.StringIO(retry.stdout),delimiter='\t'):
                if role(row.get('text','')) != role(header['text']) or float(row.get('conf',-1))<85:
                    continue
                x=int(row['left'])/3+left
                y=int(row['top'])/3+top
                w,h=int(row['width'])/3,int(row['height'])/3
                result.append(dict(text=row['text'],confidence=float(row['conf'])/100,
                                   preprocessing='original_single_header_cell_3x',
                                   polygon=[[x,y],[x+w,y],[x+w,y+h],[x,y+h]]))
    return result


def confirm_header_tokens(primary, independent):
    """Corroborate exact header roles at the same position, never guessed words."""
    from .positioned_tables import located_tokens
    from .dimension_lexicon import role, symbol_header
    def key(text):
        return role(text) or ('symbol' if symbol_header(text) else None)
    second=located_tokens(independent)
    count=0
    for token in primary:
        positions=located_tokens([token])
        if not positions or not key(token['text']):
            continue
        p=positions[0]
        matches=[t for t in second if t['confidence']>=.85 and key(t['text'])==key(token['text'])
                 and abs(t['x']-p['x'])<max(t['h'],p['h']) and abs(t['y']-p['y'])<max(t['h'],p['h'])*.6]
        if len(matches)==1:
            token['original_confidence']=token['confidence']
            token['corroboration']='tesseract_same_cell_header_agreement'
            token['confidence']=max(token['confidence'],.96)
            count+=1
    return count


def confirm_numeric_tokens(primary, independent):
    """Corroborate numbers one-to-one in an unambiguous physical cell."""
    from .positioned_tables import located_tokens
    from .dimension_tables import number
    second=located_tokens(independent)
    count=0
    used=set()
    for token in primary:
        positions=located_tokens([token])
        if not positions:
            continue
        located=positions[0]
        value=number(token['text'])
        if value is None:
            continue
        nearby=[(index,other) for index,other in enumerate(second) if other['confidence']>=.85
                and abs(other['x']-located['x'])<max(located['h'],other['h'])
                and abs(other['y']-located['y'])<max(located['h'],other['h'])*.6
                and number(other['text']) is not None]
        matches=[(index,other) for index,other in nearby if number(other['text'])==value and index not in used]
        # A second conflicting numeric reading in the same cell makes the cell
        # ambiguous even when one candidate happens to equal the primary.
        if len(matches)==1 and len({number(other['text']) for _,other in nearby})==1:
            used.add(matches[0][0])
            token['original_confidence']=token['confidence']
            token['corroboration']='tesseract_same_cell_numeric_agreement'
            token['confidence']=max(token['confidence'],.96)
            count+=1
    return count


def compare_numeric_evidence(primary_text, secondary):
    from collections import Counter
    # Keep sign and tolerance together. A bag/set of unsigned scalar values can
    # falsely approve -0.3 vs +0.3 or 0.30+/-0.05 vs 0.30+/-0.50.
    pattern = r"(?<![A-Za-z0-9])[-+]?\d*(?:\.\d+)(?:\s*(?:±|\+/-)\s*\d*(?:\.\d+))?(?![A-Za-z0-9])"
    normalize = lambda value: re.sub(r"\s+", "", value).replace("+/-", "±")
    first = Counter(normalize(value) for value in re.findall(pattern, primary_text))
    second = Counter(normalize(value) for value in re.findall(pattern, secondary.get("text", "")))
    return {"status": "agree" if first and first == second else "review_required",
            "primary_only": sorted((first - second).elements()), "secondary_only": sorted((second - first).elements()),
            "note": "Numeric agreement is not evidence of correct dimension/pin association."}
