"""Device-neutral physical-column and mux-continuation parsing."""
import re


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def mux_schema(table):
    if not table:
        return None
    headers = [clean(c) for c in table[0]]
    select = next((i for i, h in enumerate(headers) if re.search(r"select|fcn|mux", h, re.I)), None)
    name = next((i for i, h in enumerate(headers) if re.search(r"function\s*name|signal\s*name", h, re.I)), None)
    if select is None or name is None:
        return None
    physical = [(i, h) for i, h in enumerate(headers[:name])
                if re.search(r"pin|ball|pad", h, re.I)
                and not re.search(r"gpio|select|fcn|mux|type", h, re.I)]
    if not physical:
        return None
    return {"physical": physical, "select": select, "name": name,
            "gpio": next((i for i, h in enumerate(headers) if re.search(r"gpio", h, re.I)), None),
            "description": next((i for i, h in enumerate(headers) if re.search(r"description", h, re.I)), None),
            "type": next((i for i, h in enumerate(headers) if re.search(r"type", h, re.I)), None)}


def mux_rows(table, schema, variant):
    def label(header):
        return re.sub(r"[^A-Z0-9]", "", re.sub(r"pin|ball|pad|number|no\.?", "", header, flags=re.I).upper())
    columns = schema["physical"]
    selected = re.sub(r"[^A-Z0-9]", "", (variant or "").upper())
    matches = [(i, h) for i, h in columns if selected and label(h) == selected]
    if not selected and len(columns) == 1:
        matches = columns
    if len(matches) != 1:
        raise ValueError("Select an explicit physical package column: " + ", ".join(label(h) for _, h in columns))
    col, header = matches[0]
    for row in table[1:]:
        def cell(index):
            return clean(row[index]) if index is not None and index < len(row) else ""
        # Any populated package column starts a new physical row, even if
        # this variant is absent. Never leak its mux functions into prior pin.
        new_pin = any(cell(i) for i, _ in columns)
        number = cell(col) if new_pin else None
        if number and not re.fullmatch(r"[A-Z]{0,3}\d{1,4}", number, re.I):
            if number in {"-", "NC", "N/A"}:
                number = ""
            else:
                continue  # second header row, not a pin
        name = cell(schema["name"])
        function = (cell(schema["select"]), name, cell(schema["description"]), cell(schema["type"])) if name and name != "-" else None
        yield number, cell(schema["gpio"]), function, label(header)
