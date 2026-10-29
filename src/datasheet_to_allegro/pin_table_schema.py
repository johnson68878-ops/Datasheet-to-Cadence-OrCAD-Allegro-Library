"""Recover physical columns from repeated cell spans, without document identity."""
from collections import Counter


def physical_pin_table(table):
    raw = table.extract()
    rows = [list(row.cells) for row in getattr(table, 'rows', [])]
    if not raw or len(rows) != len(raw) or len(raw) < 4:
        return raw, rows
    layouts = [tuple((round(b[0],2), round(b[2],2)) for b in row if b)
               for row in rows[2:]]
    candidates = Counter(layout for layout in layouts if len(layout) >= 3)
    if not candidates:
        return raw, rows
    layout, count = candidates.most_common(1)[0]
    # A repeated data-row grid is independent of centered header text splits.
    if count < 2 or count * 2 < len(layouts) or len(layout) >= len(raw[0]):
        return raw, rows
    if any(abs(a[1]-b[0]) > .5 for a,b in zip(layout,layout[1:])):
        return raw, rows
    result, boxes = [], []
    for values, cells in zip(raw,rows):
        out, positions = [None]*len(layout), [None]*len(layout)
        for value, box in zip(values,cells):
            if box is None:
                continue
            contained = [i for i,(left,right) in enumerate(layout)
                         if box[0] >= left-.5 and box[2] <= right+.5]
            if len(contained) == 1:
                col = contained[0]
            else:
                # A genuine merged parent starts/ends at physical boundaries.
                starts = [i for i,(left,_) in enumerate(layout) if abs(box[0]-left)<.5]
                ends = [i for i,(_,right) in enumerate(layout) if abs(box[2]-right)<.5]
                if len(starts)!=1 or len(ends)!=1 or starts[0]>ends[0]:
                    return raw, rows
                col = starts[0]
            if value is not None and str(value).strip():
                if out[col] is not None and str(out[col]).strip():
                    # Multiple distinct values in one reconstructed cell are
                    # ambiguous; never concatenate unrelated signal columns.
                    return raw, rows
                out[col] = value
            if positions[col] is None or (value is not None and str(value).strip()):
                positions[col] = box
        result.append(out)
        boxes.append(positions)
    return result, boxes
