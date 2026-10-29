"""Production table scopes: package columns, conditional models and PoP faces."""
from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from .reference_pin_evidence import extract_table_pin_evidence
from .pin_lexicon import _header_role
from .pin_table_schema import physical_pin_table


def located_pin_table(table, page):
    """Join true subscript glyphs within a name cell, not separate text rows."""
    raw, located_rows = physical_pin_table(table)
    if not raw:
        return raw
    names = [i for i,c in enumerate(raw[0]) if _header_role(str(c or '')) == 'name']
    if len(names) != 1:
        return raw
    col = names[0]
    original_evidence = extract_table_pin_evidence(raw)
    for index, cells in enumerate(located_rows):
        if index == 0:
            continue
        box = cells[col]
        if not box:
            continue
        cell_chars = [c for c in page.chars if
                      box[0] <= (c['x0']+c['x1'])/2 <= box[2] and
                      box[1] <= (c['top']+c['bottom'])/2 <= box[3]]
        if any(abs(c.get('matrix',(1,0,0,1))[1])>.001 or abs(c.get('matrix',(1,0,0,1))[2])>.001 for c in cell_chars):
            from .column_pin_tables import horizontal_content
            # Limit filtering to signal data cells: rotated package headers
            # must survive so that neighboring variants never collapse.
            raw[index][col] = horizontal_content(page.crop(box)).extract_text() or ''
        value = raw[index][col]
        if not isinstance(value,str) or '\n' not in value or not all(
                re.fullmatch(r'[A-Za-z0-9_]+', part) for part in value.split()):
            continue
        chars = [c for c in page.chars if c['text'].strip() and
                 box[0] <= (c['x0']+c['x1'])/2 <= box[2] and
                 box[1] <= (c['top']+c['bottom'])/2 <= box[3]]
        chars.sort(key=lambda c:c['x0'])
        if len(chars) < 2 or max(c['top'] for c in chars) >= min(c['bottom'] for c in chars):
            continue
        if any(b['x0'] < a['x1']-.5 or b['x0']-a['x1'] > max(a['x1']-a['x0'],b['x1']-b['x0'])
               for a,b in zip(chars,chars[1:])):
            continue
        joined = ''.join(c['text'] for c in chars)
        if joined == re.sub(r'\s+','',value):
            raw[index][col] = joined
    if not original_evidence and extract_table_pin_evidence(raw):
        # Cleaning a few signal cells must not promote an otherwise unreadable
        # auxiliary table into the authoritative physical-pin inventory.
        # Establish header/number/variant scope before accepting newly usable rows.
        raise ValueError('Glyph recovery exposed a previously unreadable pin table; '
                         'complete table scope and pin-number evidence require verification. '
                         'Partial recovered rows cannot replace the physical pin inventory.')
    return raw


def table_face(text: str, previous: str = "unspecified") -> str:
    # Table captions take precedence over cross-references in body prose.
    match = re.search(r"Table\s*\d+[-–]\d+\s+(Top|Bottom)\s+pin", text, re.I)
    if not match:
        match = re.search(r"^\s*\d+(?:\.\d+)*\s+Pin assignments\s*[-–]\s*(top|bottom)", text, re.I | re.M)
    return match.group(1).lower() if match else previous


def conditional_model(text: str) -> str | None:
    match = re.search(r"\(([A-Z][A-Z0-9-]*\d[A-Z0-9-]*)\s+only\)|unique to\s+([A-Z][A-Z0-9-]*\d[A-Z0-9-]*)", text, re.I)
    if match:
        return (match.group(1) or match.group(2)).upper()
    if re.fullmatch(r"[A-Z]{2,}\d{2,}[A-Z0-9-]*", text.strip()):
        return text.strip().upper()
    return None


def pin_table_package(caption: str, page_text: str = "") -> str:
    """Resolve a local pin-table caption, never an unrelated package mention.

    Explicit N-pin/family declarations can disambiguate dimension-qualified
    captions (SOIC 300-mil is not a 300-pin package). Conflicts stay unresolved.
    """
    family = r"(?:[A-Z]*QFN|[A-Z]*QFP|[A-Z]*BGA|TSSOP|SSOP|SOIC|SOP|MSOP|WSON|SON|DFN|WLCSP|CSP|LGA)"
    headings = list(re.finditer(r'\b(?:pin|pad|ball|terminal)\s+description\b', caption, re.I))
    if headings:
        caption = caption[headings[-1].start():]
    matches = list(re.finditer(rf"\b({family})(?:\s*([0-9]+))?", caption, re.I))
    if len(matches) != 1:
        return ""
    match = matches[0]
    kind = match[1].upper()
    suffix = caption[match.end():]
    dimension = bool(re.match(r"\s*(?:[x×.]|[- ]*(?:mm|mil)\b)", suffix, re.I))
    if match[2] and not dimension:
        return kind + match[2]
    counts = set(re.findall(
        rf"\b([1-9][0-9]*)\s*[- ]\s*(?:pin|pad|ball|lead)s?\s+{re.escape(kind)}\b",
        page_text, re.I))
    if len(counts) == 1:
        return kind + next(iter(counts))
    # A family alone is not sufficient to separate multiple same-family drawings.
    return ""


def explicit_nc_ball_labels(words: list[dict], table_top: float) -> dict[str, str]:
    """Read printed NC labels below explicit coordinate rows, never fill a grid.

    The caller must establish the same-page package and a table that explicitly
    abbreviates NC rows. No missing coordinate or unlabelled cell is invented.
    """
    from .column_pin_tables import word_lines
    lines = word_lines([w for w in words if w['top'] < table_top])
    result = {}
    for index, axis in enumerate(lines[:-1]):
        if len(axis) < 2 or not all(re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]*', w['text']) for w in axis):
            continue
        prefixes = {re.match(r'[A-Z]+', w['text'])[0] for w in axis}
        numbers = [int(re.search(r'[0-9]+', w['text'])[0]) for w in axis]
        if len(prefixes) != 1 or numbers != sorted(set(numbers)):
            continue
        xs = [(w['x0'] + w['x1']) / 2 for w in axis]
        pitch = min(b-a for a,b in zip(xs,xs[1:]))
        labels = lines[index+1]
        height = max(w['bottom']-w['top'] for w in axis)
        gap = labels[0]['top'] - axis[0]['top']
        if pitch <= 0 or not height * .8 <= gap <= height * 3:
            continue
        for word, x in zip(axis, xs):
            cell = [w for w in labels if abs((w['x0']+w['x1'])/2-x) < pitch*.4]
            if len(cell) == 1 and cell[0]['text'].upper() in {'NC','N/C'}:
                if abs((cell[0]['x0']+cell[0]['x1'])/2-x) <= pitch*.15:
                    result[word['text']] = cell[0]['text']
    return result


def read_scoped_pin_tables(path: str | Path, variant: str | None, progress=None) -> list[dict]:
    path = Path(path)
    package_selection, _, model_selection = (variant or '').partition('::MODEL=')
    model_selection = model_selection.upper()
    variant = package_selection or None
    model_scopes_seen = set()
    records = []
    face, previous_table, active_model = "unspecified", "", None
    caption_variant = ""
    caption_variants_seen = set()
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            if progress:
                progress(page_number, len(pdf.pages))
            text = page.extract_text() or ""
            face = table_face(text, face)
            if face == "top":
                page.close()
                continue
            if not re.search(r"\b(?:pin|pad|ball|terminal)\b", text, re.I):
                page.close()
                continue
            words = page.extract_words() or []
            for table in page.find_tables():
                raw = located_pin_table(table, page)
                evidence = extract_table_pin_evidence(raw)
                if not evidence:
                    continue
                # Caption immediately above this table carries continuation identity.
                above = [w for w in words if table.bbox[1] - 42 <= w["top"] < table.bbox[1]]
                caption = " ".join(w["text"] for w in above)
                package = pin_table_package(caption, text)
                if package:
                    caption_variant = package
                    caption_variants_seen.add(caption_variant)
                selected = re.sub(r"[^A-Z0-9]", "", (variant or "").upper())
                if caption_variant and selected and caption_variant != selected:
                    continue
                if len(caption_variants_seen) > 1 and not selected:
                    raise ValueError("请选择封装Pin定义：" + ", ".join(sorted(caption_variants_seen)))
                abbreviated_nc = any(
                    len(row) >= 2 and str(row[0]).strip().lower() == 'multiple'
                    and str(row[1]).strip().upper() in {'NC', 'N/C'} for row in raw)
                if (abbreviated_nc and package and 'BGA' in package
                        and re.search(r'ball\s+(?:configuration|assignments|map)', text, re.I)
                        and re.search(r'top\s+view', text, re.I)):
                    known = {r['pin_number']: r['pin_name'] for r in evidence}
                    for number, name in explicit_nc_ball_labels(words, table.bbox[1]).items():
                        if number in known:
                            if known[number].upper() not in {'NC','N/C'}:
                                raise ValueError(f'Ball map / table conflict at {number}: NC versus {known[number]}')
                            continue
                        evidence.append(dict(variant='unspecified', pin_number=number,
                            pin_name=name, row=0, raw_type='',
                            description=f'Explicit NC label in same-page ball diagram, page {page_number}'))
                identity = re.search(r"Table\s*(\d+[-–]\d+)", caption, re.I)
                table_id = identity.group(1).replace("–", "-") if identity else ""
                if not table_id or table_id != previous_table:
                    active_model = None
                previous_table = table_id
                models_by_row = {}
                for row_index, row in enumerate(raw):
                    nonempty = [str(c).strip() for c in row if c is not None and str(c).strip()]
                    if len(nonempty) == 1:
                        condition = conditional_model(nonempty[0])
                        if condition:
                            model_scopes_seen.add(condition)
                        # Ordinary group headings reset to shared scope.
                        active_model = condition
                    models_by_row[row_index] = active_model
                variants = {r["variant"] for r in evidence} - {"unspecified"}
                selected = re.sub(r"[^A-Z0-9]", "", (variant or "").upper())
                if variants and not selected:
                    raise ValueError("请选择封装Pin定义：" + ", ".join(sorted(variants)))
                for row in evidence:
                    if variants and re.sub(r"[^A-Z0-9]", "", row["variant"].upper()) != selected:
                        continue
                    model = models_by_row[row["row"]]
                    if model and not model_selection:
                        raise ValueError(f"第{page_number}页存在条件范围 {model}；请选择文件中明确列出的 MODEL 变体，文件名不能决定引脚。")
                    if model and model != model_selection:
                        continue
                    records.append(dict(row, page=page_number, interface=face, table_id=table_id,
                                        applicable_model=model or "shared"))
            page.close()
    if model_selection and model_selection not in model_scopes_seen:
        raise ValueError(f'Unknown conditional document scope: {model_selection}')
    if variant and caption_variants_seen and not records:
        raise ValueError(f'No pin evidence for selected package: {variant}')
    return records
