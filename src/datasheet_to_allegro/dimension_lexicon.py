"""Shared dimension typography; no package/vendor-specific numeric substitutions."""
import re
import unicodedata


def clean(value):
    return ' '.join(unicodedata.normalize('NFKC',str(value or '')).split())


def number(value):
    text=clean(value).replace('−','-')
    text=re.sub(r'(?<=\.)\s+(?=\d)','',text)
    text=re.sub(r'(?:\(\d+\.?\)|\[\d+\])','',text)
    text=re.sub(r'\(?(BSC|REF|TYP)\)?\s*$',r'\1',text,flags=re.I)
    text=re.sub(r'(?:BSC|REFERENCE|REF|TYP|MAX|MIN)\.?$','',text,flags=re.I).strip()
    if re.fullmatch(r'\d+,\d+',text): text=text.replace(',','.')
    if not re.fullmatch(r'\+?\d*(?:\.\d+)?',text) or not text or text=='.': return None
    return float(text)


def role(text):
    low=clean(text).lower().strip('.')
    return {'min':'min_mm','minimum':'min_mm','max':'max_mm','maximum':'max_mm',
            'typ':'nominal_mm','typical':'nominal_mm','nom':'nominal_mm','nominal':'nominal_mm','nor':'nominal_mm',
            'value':'value_mm'}.get(low)


def unit(text):
    low=clean(text).lower()
    low=low.replace('miilimeters','millimeters').replace('miilimetres','millimetres')
    # Letter-spaced headings are common in embedded-font dimension tables.
    low=re.sub(r'(?<!\w)(?:[a-z]\s+){2,}[a-z](?!\w)',lambda m:m[0].replace(' ',''),low)
    low=re.sub(r'dimensions?in(millimeters?|millimetres?|inches)',r'\1',low)
    if re.search(r'\b(?:millimeters?|millimetres?|mm)\b|毫米',low): return 'mm'
    if re.search(r'\b(?:inch(?:es)?|in)\b|英寸',low): return 'inch'
    return None


def symbol(text):
    text=clean(text)
    if text in {'in','is','to','of','as','by','on','at','or','if','an','be'}:
        return None
    # Footnote indexes do not become extra dimension values.
    text=re.sub(r'(?:\(\d+\.?\)|\[\d+\]|\*)','',text).strip()
    return text if re.fullmatch(r'[A-Za-z]{1,2}\d?(?:/[A-Za-z]{1,2}\d?)?',text) else None


def symbol_header(text):
    return bool(re.fullmatch(r'symbols?|dim(?:ension)?\.?|字符|符号|符號',clean(text),re.I))

