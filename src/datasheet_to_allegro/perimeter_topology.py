"""Recover a complete, caption-scoped perimeter map from positioned PDF glyphs.

No pin is invented. Counterclockwise order and all four physical sides must
be independently visible in a component-top drawing.
"""
from collections import Counter
import re
from statistics import median

from .package_vocabulary import identify_package_families


def oriented_words(page):
    """Keep 90-degree labels, reading their glyphs along the PDF text matrix."""
    def direction(obj):
        if obj.get("object_type") != "char":
            return None
        a, b, c, d, *_ = obj.get("matrix", (1, 0, 0, 1, 0, 0))
        if abs(b) < 1e-6 and abs(c) < 1e-6 and a > 0:
            return "ltr"
        if abs(a) < 1e-6 and abs(d) < 1e-6 and abs(b) > 1e-6:
            return "btt" if b > 0 else "ttb"
        return None  # Slanted watermarks are not a pin-label channel.

    words = []
    for orientation in ("ltr", "btt", "ttb"):
        channel = page.filter(lambda obj, o=orientation: direction(obj) == o)
        for word in channel.extract_words(
            x_tolerance=2, y_tolerance=2,
            char_dir_rotated=orientation if orientation != "ltr" else "ttb",
        ):
            words.append({**word, "glyph_direction": orientation})
    return words


def prove_ring(words, count):
    """Return observed side counts only for a complete, ordered rectangular ring."""
    pins = [w for w in words if re.fullmatch(r"\d+", w["text"])
            and 1 <= int(w["text"]) <= count]
    if Counter(int(w["text"]) for w in pins) != Counter(range(1, count + 1)):
        return None
    points = [(int(w["text"]), (w["x0"] + w["x1"]) / 2,
               (w["top"] + w["bottom"]) / 2) for w in pins]
    tolerance = median(min(w["x1"] - w["x0"], w["bottom"] - w["top"]) for w in pins)
    left, right = min(p[1] for p in points), max(p[1] for p in points)
    top, bottom = min(p[2] for p in points), max(p[2] for p in points)
    if tolerance <= 0 or min(right - left, bottom - top) < 8 * tolerance:
        return None
    sides = [[], [], [], []]  # Left down, bottom right, right up, top left.
    for number, x, y in points:
        distances = [abs(x-left), abs(y-bottom), abs(x-right), abs(y-top)]
        matches = [i for i, distance in enumerate(distances) if distance <= tolerance]
        if len(matches) != 1:
            return None
        sides[matches[0]].append((number, x, y))
    if any(len(side) < 2 for side in sides):
        return None
    keys = (lambda p: p[2], lambda p: p[1], lambda p: -p[2], lambda p: -p[1])
    ordered = [sorted(side, key=key) for side, key in zip(sides, keys)]
    if [p[0] for side in ordered for p in side] != list(range(1, count + 1)):
        return None
    return {
        "side_pin_counts": [len(side) for side in ordered],
        "bbox_pdf_points": [left, top, right, bottom],
        "pins": [{"number": number, "x_pt": x, "y_pt": y} for number, x, y in points],
    }


def read_perimeter_maps(words, page_height):
    """Separate stacked figures by their own package/count + pinout caption."""
    lines = []
    for word in sorted((w for w in words if w.get("glyph_direction", "ltr") == "ltr"),
                       key=lambda w: (w["top"], w["x0"])):
        if not lines or abs(word["top"] - lines[-1][0]["top"]) > 2:
            lines.append([])
        lines[-1].append(word)
    captions = []
    for line in lines:
        text = " ".join(w["text"] for w in sorted(line, key=lambda w: w["x0"]))
        if not re.search(r"\bpinout\b|\bpin\s+(?:assignment|configuration)s?\b", text, re.I):
            continue
        labels = []
        for word in line:
            match = re.fullmatch(r"([A-Za-z]+)[-_]?(\d+)", word["text"])
            if not match:
                continue
            families = set(identify_package_families(word["text"])) & {"QFP", "QFN"}
            if len(families) == 1:
                labels.append((word["text"], next(iter(families)), int(match[2])))
        # Multiple side-by-side captions cannot be scoped by a vertical cut.
        if len(labels) != 1 or re.search(r"\.{3,}", text):
            continue
        captions.append((line[0]["top"], max(w["bottom"] for w in line), labels[0]))
    results = []
    for index, (_, start, (label, family, count)) in enumerate(captions):
        end = captions[index + 1][0] if index + 1 < len(captions) else page_height * .95
        region = [w for w in words if start < w["top"] and w["bottom"] < end]
        text = " ".join(w["text"] for w in sorted(
            (w for w in region if w.get("glyph_direction", "ltr") == "ltr"),
            key=lambda w: (round(w["top"] / 3), w["x0"])))
        if not re.search(r"\btop\s+view\b", text, re.I) or re.search(r"\bbottom\s+view\b", text, re.I):
            continue
        ring = prove_ring(region, count)
        if ring:
            results.append({**ring, "family": family, "pin_count": count,
                            "caption": label, "view": "component_top",
                            "region_pdf_points": [0, start, max(w["x1"] for w in region), end]})
    return results


def read_dual_maps(words, page_height):
    """Prove two numbered rails between a local configuration heading and caption.

    Counts come from the caption and every number must be observed. Numeric
    signal subscripts outside the rails are not pin numbers. Only top-view,
    upper-left pin 1, counterclockwise drawings are supported in this grammar.
    """
    lines = []
    for w in sorted((w for w in words if w.get('glyph_direction', 'ltr') == 'ltr'),
                    key=lambda w: (w['top'], w['x0'])):
        if not lines or abs(w['top'] - lines[-1][0]['top']) > 2:
            lines.append([])
        lines[-1].append(w)
    text = lambda line: ' '.join(w['text'] for w in sorted(line, key=lambda w: w['x0']))
    results = []
    duals = {'SOIC', 'DFN'}
    for i, line in enumerate(lines):
        caption = text(line)
        if not re.match(r'Figure\s+\S+', caption, re.I) or not re.search(r'pin\s+(?:assignments?|configuration)', caption, re.I):
            continue
        counts = re.findall(r'\b(\d+)\s*[- ]\s*pins?\b', caption, re.I)
        families = set(identify_package_families(caption)) & duals
        if len(counts) != 1 or len(families) != 1:
            continue
        count, family = int(counts[0]), next(iter(families))
        if count < 4 or count % 2:
            continue
        headings = [j for j in range(i) if re.search(r'pin\s+configuration', text(lines[j]), re.I)]
        if not headings:
            continue
        j = headings[-1]
        if set(identify_package_families(text(lines[j]))) & duals != {family}:
            continue
        start, end = max(w['bottom'] for w in lines[j]), min(w['top'] for w in line)
        region = [w for w in words if start < w['top'] and w['bottom'] < end]
        region_text = ' '.join(text(row) for row in lines[j+1:i])
        if not re.search(r'\btop\s+view\b', region_text, re.I) or re.search(r'\bbottom\s+view\b|\bFigure\b', region_text, re.I):
            continue
        pins = [w for w in region if re.fullmatch(r'\d+', w['text']) and 1 <= int(w['text']) <= count]
        if not pins:
            continue
        tolerance = median(w['bottom']-w['top'] for w in pins) * .4
        tracks = []
        for w in sorted(pins, key=lambda w: (w['x0']+w['x1'])/2):
            x = (w['x0']+w['x1'])/2
            if not tracks or x - (tracks[-1][0]['x0']+tracks[-1][0]['x1'])/2 > tolerance:
                tracks.append([])
            tracks[-1].append(w)
        matches = []
        for left in tracks:
            for right in tracks:
                if (right[0]['x0']+right[0]['x1']-left[0]['x0']-left[0]['x1'])/2 < 4*tolerance:
                    continue
                a, b = (sorted(t, key=lambda w:w['top']) for t in (left,right))
                if [int(w['text']) for w in a] != list(range(1,count//2+1)) or [int(w['text']) for w in b] != list(range(count,count//2,-1)):
                    continue
                if any(abs((u['top']+u['bottom']-v['top']-v['bottom'])/2)>tolerance for u,v in zip(a,b)):
                    continue
                gaps = [a[k+1]['top']-a[k]['top'] for k in range(len(a)-1)]
                if min(gaps) <= 2*tolerance or max(gaps)-min(gaps)>tolerance:
                    continue
                matches.append(a+b)
        if len(matches) == 1:
            results.append({'family':family, 'pin_count':count, 'caption':caption,
                'view':'component_top', 'side_pin_counts':[count//2,count//2],
                'region_pdf_points':[0,start,max(w['x1'] for w in region),end],
                'pins':[{'number':int(w['text']), 'x_pt':(w['x0']+w['x1'])/2,
                         'y_pt':(w['top']+w['bottom'])/2} for w in matches[0]]})
    return results
