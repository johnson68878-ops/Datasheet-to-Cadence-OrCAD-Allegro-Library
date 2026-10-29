"""Explicit, local package-document matching; no answer registry or downloads."""
import hashlib
import re
from pathlib import Path
import pdfplumber


def reference_pair(text):
    pairs=re.findall(r'(?im)^Package Code\s+([^\s]+)\s*\nOutline Number\s+([^\s]+)\s*$',text)
    return pairs[0] if len(pairs)==1 else None


def compatible(primary, drawing):
    for name in ('package_type','pin_count','row_names','column_count','missing_positions','view_convention'):
        if getattr(primary,name)!=getattr(drawing,name): return False
    if not primary.pin_count or not primary.row_names or not primary.column_count: return False
    for name in ('body_width','body_length','pitch','pitch_x','pitch_y','package_height','terminal_width_min'):
        a,b=getattr(primary,name),getattr(drawing,name)
        if name in ('body_width','body_length','pitch') and (a is None or b is None): return False
        if a is None: continue
        if b is None: return False
        av=a.nominal_mm if a.nominal_mm is not None else a.value_mm
        bv=b.nominal_mm if b.nominal_mm is not None else b.value_mm
        if av is None or bv is None or abs(av-bv)>1e-6: return False
        for field in ('min_mm','max_mm'):
            x,y=getattr(a,field),getattr(b,field)
            if x is not None and (y is None or abs(x-y)>1e-6): return False
    return True


def inspect_with_package_document(primary_path, drawing_path):
    """Allegro-only external-source path; caller explicitly supplies both PDFs.

    Conservative initial scope: exactly one primary variant and one drawing.
    No automatic web lookup, electrical-symbol merge, or ambiguous selection.
    """
    from .pipeline import inspect_datasheet
    from .models import FootprintSpec
    primary_path,drawing_path=Path(primary_path),Path(drawing_path)
    def text(path):
        with pdfplumber.open(path) as pdf:
            return '\n'.join(p.extract_text() or '' for p in pdf.pages)
    primary_text,drawing_text=text(primary_path),text(drawing_path)
    pair=reference_pair(primary_text)
    if pair is None: raise ValueError('A unique explicit package code / outline number pair is required')
    for identifier in pair:
        if not re.search(r'(?<![\w+.-])'+re.escape(identifier)+r'(?![\w+.-])',drawing_text):
            raise ValueError('Package drawing does not contain the referenced identity')
    fingerprint, primary_specs=inspect_datasheet(primary_path)
    _, drawing_specs=inspect_datasheet(drawing_path)
    if len(primary_specs)!=1 or len(drawing_specs)!=1:
        raise ValueError('Multiple package variants require explicit scoped association')
    a,b=primary_specs[0],drawing_specs[0]
    if not compatible(a,b): raise ValueError('Package topology, orientation or measured dimensions conflict / are incomplete')
    # Keep the drawing as the actual geometry source. Primary identity belongs
    # in the association audit; never disguise drawing page numbers as primary.
    payload=b.model_dump(mode='json')
    def annotate(node):
        if isinstance(node,dict):
            if {'page','raw_text','extraction_method'} <= node.keys():
                node['source_uri']=drawing_path.resolve().as_uri()
                node['source_title']=drawing_path.name
                node['document_kind']='package_drawing'
            for v in node.values(): annotate(v)
        elif isinstance(node,list):
            for v in node: annotate(v)
    annotate(payload)
    payload['document_scan']['external_package_association']={
        'mode':'explicit_external_package_document','automatic_pdf_only':False,
        'primary_part_number':a.part_number,'package_code':pair[0],'outline_number':pair[1],
        'primary_uri':primary_path.resolve().as_uri(),'drawing_uri':drawing_path.resolve().as_uri(),
        'primary_sha256':hashlib.sha256(primary_path.read_bytes()).hexdigest(),
        'drawing_sha256':hashlib.sha256(drawing_path.read_bytes()).hexdigest(),
        'physical_match':'exact nominal values and any declared bounds; identical topology and view',
        'scope':'Allegro footprint only; no OrCAD pin/function replacement'}
    return fingerprint,[FootprintSpec.model_validate(payload)]
