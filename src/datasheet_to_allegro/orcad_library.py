from __future__ import annotations

import csv
import json
import re
from .pin_lexicon import _clean, _header_role, _valid_pin_number, _pin_numbers
import subprocess
import string
from functools import lru_cache
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import pdfplumber
from pypdf import PdfReader
from .document_access import require_readable_document
from .symbol_reference_policy import (
    TEXT_UNIT_PER_CHAR as _CAPTURE_TEXT_UNIT_PER_CHAR,
    TEXT_GUTTER as _SYMBOL_TEXT_GUTTER,
    MIN_PIN_SPACING as _MIN_CAPTURE_PIN_SPACING,
    MAX_SOURCE_WHITESPACE_SCALE,
)


_HEADER_KEYS = ("BGA", "PIN", "GPIO", "Fcn", "Pad Function Name", "Pin Type")
_POWER_RE = re.compile(r"(?:^|_)(?:VDD|VSS|GND|AVDD|DVDD|VDDA|GNDA|VREF|VBAT|VCC)", re.I)
_CLOCK_ANALOG_RE = re.compile(r"ADC|DAC|XTAL|OSC|CLK|CLOCK|MIPI|USB|MIC|AUDIO|VCOMP|DDR|DQ\d|MEM", re.I)
_INTERFACE_RE = re.compile(r"I2C|I²C|SPI|UART|CAN|JTAG|SWD|SCL|SDA|MOSI|MISO|TX|RX|PCIE|ETH", re.I)
_SINGLE_PART_MAX_PINS = 100
_MIN_FUNCTIONAL_PART_PINS = 5
_MAX_PINS_PER_LARGE_PART = 80
_SECTION_IDS = string.ascii_uppercase
_CAPTURE_UNITS_PER_PDF_POINT = 3.4


@dataclass(slots=True)
class PinFunction:
    select: str
    name: str
    description: str
    pin_type: str


@dataclass(slots=True)
class OrcadPin:
    number: str
    name: str
    electrical_type: str
    description: str
    evidence_page: int
    gpio_number: str = ""
    functions: list[PinFunction] = field(default_factory=list)
    section: str = "A"
    design_context_name: str = ""
    design_context_page: int = 0
    functional_group: str = ""
    symbol_side: str = ""
    symbol_order: int = 0
    architecture_evidence_page: int = 0
    source_y_ratio: float = -1.0


@dataclass(slots=True)
class OrcadSymbolSpec:
    part_name: str
    manufacturer: str
    datasheet_revision: str
    package_variant: str
    pcb_footprint: str
    source_pdf: str
    pins: list[OrcadPin]
    schematic_context_source: str = ""
    context_matched_pins: int = 0
    architecture_evidence_pages: list[int] = field(default_factory=list)
    schematic_component_refdes: str = ""
    part_aspect_ratios: dict[str, float] = field(default_factory=dict)
    part_source_sizes: dict[str, list[float]] = field(default_factory=dict)
    geometry_source_pages: list[int] = field(default_factory=list)
    review_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)



def _is_pin_table_header(row: list[object]) -> bool:
    text = " ".join(_clean(cell) for cell in row)
    return all(key.lower() in text.lower() for key in _HEADER_KEYS)


def _mux_function_importance(function: PinFunction) -> int:
    """Rank schematic-useful mux functions without relying on a device name."""

    name = re.sub(r"[^A-Z0-9_]", "", function.name.upper())
    text = f"{name} {function.description.upper()}"
    if not name or name == "-":
        return 0
    if re.fullmatch(r"GPIO\d*", name):
        return 10
    if re.search(r"\b(?:I2S|PCM|PDM)\d*", text):
        return 140
    if re.search(r"\b(?:MIPI|PCIE|USB|DDR|SDIO|RMII|RGMII)", text):
        return 135
    if re.search(r"\bUART\d*", text):
        return 130
    if re.search(r"(?:MOSI|MISO|SCLK|SCK|SCL|SDA|I2C|I2C|SPI)", text):
        return 120
    if re.search(r"(?:ADC|DAC|PWM|TRACE|SWD|JTAG|CAN|ETH)", text):
        return 105
    if re.search(r"(?:CHIP.?SELECT|\bNCE\d*|\bCS\d*)", text):
        return 80
    if re.search(r"(?:TIMER|COUNTER|\bCT\d*)", text):
        return 70
    return 50


def _is_low_value_mux_alias(name: str) -> bool:
    """Hide implementation-oriented mux aliases from the symbol face.

    These aliases remain in ``OrcadPin.functions`` and the evidence manifest;
    only the short name rendered in Capture is filtered.  A physical pin is
    never removed by this rule.
    """

    normalized = re.sub(r"[^A-Z0-9]", "", name.upper())
    return bool(re.fullmatch(r"(?:M\d*)?NCE\d+|CT\d+", normalized))


def _merge_mux_display_groups(functions: list[PinFunction]) -> list[tuple[int, str, list[PinFunction]]]:
    """Collapse related alternate functions into short engineering labels."""

    grouped: dict[tuple[str, str], list[PinFunction]] = {}
    for function in functions:
        name = re.sub(r"\s+", "", function.name.upper())
        if not name or name == "-" or re.fullmatch(r"GPIO\d*", name) or _is_low_value_mux_alias(name):
            continue
        if match := re.fullmatch(r"I2S(\d+)_?(.+)", name):
            key = (f"I2S{match.group(1)}", "I2S")
        elif match := re.fullmatch(r"UART(\d+)_?(CTS|RTS|TX|RX)", name):
            key = (match.group(2), "UART")
        else:
            key = (name, "RAW")
        grouped.setdefault(key, []).append(function)

    result: list[tuple[int, str, list[PinFunction]]] = []
    for (key, kind), members in grouped.items():
        if kind == "I2S":
            suffixes: list[str] = []
            for member in members:
                suffix = re.fullmatch(r"I2S\d+_?(.+)", member.name.upper()).group(1)  # type: ignore[union-attr]
                if suffix not in suffixes:
                    suffixes.append(suffix)
            label = f"{key}_{'/'.join(suffixes)}"
        elif kind == "UART":
            instances = sorted({re.fullmatch(r"UART(\d+)_?.+", member.name.upper()).group(1) for member in members})  # type: ignore[union-attr]
            label = f"UART{'/'.join(instances)}_{key}"
        else:
            label = key
        result.append((max(_mux_function_importance(member) for member in members), label, members))
    return sorted(result, key=lambda item: (-item[0], item[1]))


def _select_name(functions: list[PinFunction], gpio_number: str) -> tuple[str, str, str]:
    usable = [fn for fn in functions if fn.name and fn.name != "-"]
    if not usable:
        if gpio_number and gpio_number != "-":
            return f"GPIO{gpio_number}", "Bidirectional", "General purpose I/O"
        return "NC", "Passive", "No connect / reserved"

    important = _merge_mux_display_groups(usable)
    selected_groups: list[tuple[int, str, list[PinFunction]]] = []
    visible_length = 0
    for group in important:
        # A symbol name must stay readable inside Capture. Keep at most three
        # high-value function families and cap the combined visible length.
        added = len(group[1]) + (1 if selected_groups else 0)
        if len(selected_groups) >= 3 or visible_length + added > 44:
            continue
        selected_groups.append(group)
        visible_length += added
    if selected_groups:
        selected_functions = [member for _score, _label, members in selected_groups for member in members]
        display_name = "/".join(label for _score, label, _members in selected_groups)
        electrical_types = {normalize_electrical_type(member.pin_type, member.name) for member in selected_functions}
        electrical = (
            "Bidirectional"
            if "Bidirectional" in electrical_types or {"Input", "Output"} <= electrical_types
            else next(iter(electrical_types), "Passive")
        )
        descriptions = list(dict.fromkeys(member.description for member in selected_functions if member.description))
        return display_name, electrical, "; ".join(descriptions)

    # GPIO is only the visible fallback when no more specific, useful function
    # is available for that physical pin.
    gpio = next((fn for fn in usable if fn.name.upper().startswith("GPIO")), None)
    if gpio is not None:
        return gpio.name, normalize_electrical_type(gpio.pin_type, gpio.name), gpio.description
    if gpio_number and gpio_number != "-":
        return f"GPIO{gpio_number}", "Bidirectional", "General purpose I/O"
    chosen = usable[0]
    return chosen.name, normalize_electrical_type(chosen.pin_type, chosen.name), chosen.description


def normalize_electrical_type(raw_type: str, pin_name: str = "") -> str:
    text = _clean(raw_type).lower()
    name = _clean(pin_name)
    tokens = {token for token in re.split(r"[^a-z]+", text) if token}
    if re.fullmatch(r'i(?:u|d|s|us|ds|r)?/o(?:\d+|p)?', text):
        return 'Bidirectional'
    if re.fullmatch(r'i(?:u|d|s|us|ds|r)', text):
        return 'Input'
    if re.fullmatch(r'o(?:\d+|p)', text):
        return 'Output'
    if "bidirectional" in text or text in {"i/o", "io"}:
        return "Bidirectional"
    if re.search(r"open[ -]?(?:collector|drain)", text) or text == "od":
        return "OpenCollector"
    if re.search(r"tri[ -]?state|3[ -]?state", text):
        return "3State"
    if text in {"p", "g", "power", "ground", "电源", "電源", "接地"} or tokens & {"pi", "po", "gndp", "gnda", "gndc"} or _POWER_RE.search(name):
        return "Power"
    if "bidirectional" in text or text in {"i/o", "io", "输入/输出", "輸入/輸出"} or tokens & {"b", "bh"} or ({"ai", "ao"} <= tokens) or ({"di", "do"} <= tokens):
        return "Bidirectional"
    if "output" in text or text in {"o", "输出", "輸出"} or tokens & {"ao", "do"}:
        return "Output"
    if "input" in text or text in {"i", "输入", "輸入"} or tokens & {"ai", "di"}:
        return "Input"
    if "open collector" in text:
        return "OpenCollector"
    if "tri" in text:
        return "3State"
    return "Passive"


def _assign_section(pin: OrcadPin) -> str:
    if pin.electrical_type == "Power" or _POWER_RE.search(pin.name):
        return "D"
    # Partition by the visible primary function.  Looking at every alternate
    # mux choice makes almost every GPIO look like SPI/UART/clock and creates
    # artificial, tiny A/B/C/D fragments.
    if _CLOCK_ANALOG_RE.search(pin.name):
        return "C"
    if _INTERFACE_RE.search(pin.name):
        return "B"
    return "A"


def apply_symbol_partition_policy(pins: list[OrcadPin]) -> None:
    """Apply the audited OrCAD Golden-library multipart policy in-place.

    Existing project libraries keep devices through 100 pins as one readable
    symbol.  Larger devices may be split by coherent function, but a part with
    fewer than five pins is folded into the largest compatible part.  This
    prevents the previous 1- or 2-pin fragment symbols.
    """

    if len(pins) <= _SINGLE_PART_MAX_PINS:
        for pin in pins:
            pin.section = "A"
        return

    functional_groups = {letter: [] for letter in "ABCD"}
    for pin in pins:
        functional_groups[_assign_section(pin)].append(pin)

    populated = [group for group in functional_groups.values() if group]
    while len(populated) > 1 and any(len(group) < _MIN_FUNCTIONAL_PART_PINS for group in populated):
        tiny = min((group for group in populated if len(group) < _MIN_FUNCTIONAL_PART_PINS), key=len)
        target = max((group for group in populated if group is not tiny), key=len)
        target.extend(tiny)
        populated.remove(tiny)

    parts: list[list[OrcadPin]] = []
    for group in populated:
        chunk_count = max(1, (len(group) + _MAX_PINS_PER_LARGE_PART - 1) // _MAX_PINS_PER_LARGE_PART)
        base, extra = divmod(len(group), chunk_count)
        cursor = 0
        for index in range(chunk_count):
            size = base + (1 if index < extra else 0)
            parts.append(group[cursor : cursor + size])
            cursor += size
    if len(parts) > len(_SECTION_IDS):
        raise ValueError(f"功能分件超过OrCAD安全上限：{len(parts)} parts")
    for section, group in zip(_SECTION_IDS, parts):
        for pin in group:
            pin.section = section


def extract_mux_table_spec(
    pdf_path: str | Path, *, package_variant: str | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> OrcadSymbolSpec | None:
    """Parse select/function tables by column roles, never by device identity."""
    from .mux_tables import mux_schema, mux_rows
    path = Path(pdf_path).resolve()
    pins: dict[str, OrcadPin] = {}
    active: OrcadPin | None = None
    selected_label = ""
    declared_counts: dict[str, set[int]] = {}
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            if progress:
                progress(page_number, len(pdf.pages))
            text = page.extract_text() or ""
            for family, count in re.findall(r"\b(BGA|WLCSP|CSP)\s*\(\s*(\d+)\s+(?:pins|balls)\b", text, re.I):
                key = family.upper().replace("WLCSP", "CSP")
                declared_counts.setdefault(key, set()).add(int(count))
            if not re.search(r"(?:pin|pad|ball).*?(?:function|connection|select)", text, re.I | re.S):
                page.close()
                continue
            for table in page.extract_tables() or []:
                schema = mux_schema(table)
                if schema is None:
                    continue
                for number, gpio, function, label in mux_rows(table, schema, package_variant):
                    selected_label = label
                    if number is not None:
                        active = None
                        if number:
                            if number in pins:
                                raise ValueError(f"Duplicate physical pin {number} on page {page_number}; review table scope")
                            active = OrcadPin(number, "", "Passive", "", page_number, gpio_number=gpio)
                            pins[number] = active
                    if active is not None and function:
                        active.functions.append(PinFunction(*function))
            page.close()
    if not pins:
        return None
    counts = declared_counts.get(selected_label.replace("WLCSP", "CSP"), set())
    if len(counts) > 1 or (counts and len(pins) not in counts):
        raise ValueError(f"Physical pin count conflict: {selected_label} document declares {sorted(counts)}, table contains {len(pins)}. Provide pin-map evidence; missing balls are never synthesized.")
    for pin in pins.values():
        pin.name, pin.electrical_type, pin.description = _select_name(pin.functions, pin.gpio_number)
        if not pin.name:
            raise ValueError(f"Missing function evidence for physical pin {pin.number}")
    extracted = list(pins.values())
    apply_symbol_partition_policy(extracted)
    return OrcadSymbolSpec(
        part_name=re.sub(r"[^A-Za-z0-9_]+", "_", path.stem).upper()[:64],
        manufacturer="EE_REVIEW_REQUIRED", datasheet_revision="PDF_SOURCE",
        package_variant=selected_label, pcb_footprint="", source_pdf=str(path), pins=extracted,
        review_warnings=["Physical pin count/map reconciliation and mux configuration require engineering review; no missing ball is synthesized."],
    )


def discover_orcad_package_variants(
    pdf_path: str | Path,
    *,
    evidence_pages: Iterable[int] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[dict[str, object]]:
    """Return distinct package/pin-definition choices before OLB generation.

    The detector is schema-driven: two or more physical-number columns before
    the Pin Name/Function column are treated as separate package definitions.
    It does not depend on a manufacturer or part number.
    """

    path = Path(pdf_path).resolve()
    found: dict[str, dict[str, object]] = {}
    observed_pins: dict[str, set[str]] = {}
    package_order_options: list[tuple[str, str]] = []
    face = "unspecified"
    from .structured_pin_tables import table_face, pin_table_package, conditional_model
    from .reference_pin_evidence import extract_table_pin_evidence
    from .pin_table_schema import physical_pin_table
    conditional_scopes: set[str] = set()
    with pdfplumber.open(path) as pdf:
        selected_pages = sorted({int(value) for value in evidence_pages or [] if 1 <= int(value) <= len(pdf.pages)})
        page_numbers = selected_pages or list(range(1, len(pdf.pages) + 1))
        total_pages = len(page_numbers)
        for scan_index, page_number in enumerate(page_numbers, start=1):
            page = pdf.pages[page_number - 1]
            raw_text = page.extract_text() or ""
            if progress:
                progress(scan_index, total_pages)
            face = table_face(raw_text, face)
            if face == "top":
                continue
            text = raw_text.lower()
            for match in re.finditer(r"\b([A-Z][A-Z0-9]+-[A-Z0-9]+)\s*\(([^)]+)\)", raw_text, re.I):
                package_order_options.append((_clean(match.group(1)), _clean(match.group(2))))
            if not any(token in text for token in ("pin", "ball", "terminal", "引脚", "引腳", "管脚")):
                continue
            words = page.extract_words() or []
            for located_table in page.find_tables():
                table, _ = physical_pin_table(located_table)
                evidence = extract_table_pin_evidence(table)
                if evidence:
                    for row in table:
                        cells = [_clean(c) for c in row if _clean(c)]
                        if len(cells) == 1 and (scope := conditional_model(cells[0])):
                            conditional_scopes.add(scope)
                caption = " ".join(w["text"] for w in words
                                   if located_table.bbox[1] - 42 <= w["top"] < located_table.bbox[1])
                scoped_package = pin_table_package(caption, raw_text) if evidence else ""
                if scoped_package:
                    entry = found.setdefault(scoped_package, {
                        "id": scoped_package, "label": scoped_package,
                        "package_code": scoped_package, "interface": face,
                        "evidence_pages": []})
                    if page_number not in entry["evidence_pages"]:
                        entry["evidence_pages"].append(page_number)
                    observed_pins.setdefault(scoped_package, set()).update(r['pin_number'] for r in evidence)
                if len(table) < 2:
                    continue
                width = max(len(row) for row in table[:3])
                rows = [[_clean(row[col]) if col < len(row) else "" for col in range(width)] for row in table[:3]]
                name_columns = [col for col in range(width) if any(_header_role(row[col]) == "name" for row in rows)]
                if not name_columns:
                    continue
                name_col = min(name_columns)
                candidates: list[tuple[int, str]] = []
                for col in range(name_col):
                    header = rows[0][col].strip()
                    package_pin_header = bool(re.search(r"\b(?:BGA|CSP|WLCSP|LGA|QFN|DFN|SON|PACKAGE)\b.*\b(?:PIN|BALL|PAD)\b", header, re.I))
                    if (_header_role(header) != "number" and not package_pin_header) or re.search(r"GPIO|FUNCTION|FCN|SELECT", header, re.I):
                        continue
                    label = re.sub(r"(?i)\b(?:pin|ball|terminal|pad|number|no)\b|[#.]", " ", header)
                    label = re.sub(r"\s+", " ", label).strip() or f"PACKAGE {col + 1}"
                    candidates.append((col, label))
                if len(candidates) < 2:
                    parent = next((col for col in range(name_col) if _header_role(rows[0][col]) == "number"), None)
                    if parent is not None:
                        candidates = []
                        for col in range(parent, name_col):
                            label = rows[1][col].strip() if len(rows) > 1 else ""
                            if label and not _header_role(label):
                                candidates.append((col, label))
                if len(candidates) < 2:
                    continue
                for col, package_type in candidates:
                    key = re.sub(r"[^A-Z0-9]+", "_", package_type.upper()).strip("_")
                    order_name = next(
                        (name for name, kind in package_order_options if package_type.upper() in kind.upper() or kind.upper() in package_type.upper()),
                        package_type,
                    )
                    label = order_name + (" (PCB Bottom)" if face == "bottom" else "")
                    entry = found.setdefault(key, {"id": key, "label": label, "package_code": package_type, "interface": face, "column": col, "evidence_pages": []})
                    if page_number not in entry["evidence_pages"]:
                        entry["evidence_pages"].append(page_number)
                    for row in table[1:]:
                        value = _clean(row[col]) if col < len(row) else ""
                        observed_pins.setdefault(key, set()).update(_pin_numbers(value))
    for key, entry in found.items():
        entry["observed_pin_count"] = len(observed_pins.get(key, set()))
    if conditional_scopes:
        choices = list(found.values()) or [{'id': '', 'label': 'Document', 'evidence_pages': []}]
        return [dict(choice, id=f"{choice['id']}::MODEL={scope}",
                     label=f"{choice['label']} / {scope}", conditional_scope=scope)
                for choice in choices for scope in sorted(conditional_scopes)]
    return list(found.values())


def inspect_schematic_components(
    schematic_pdf: str | Path,
    progress: Callable[[int, int], None] | None = None,
) -> list[dict[str, object]]:
    """Inventory selectable schematic components from an EDA-exported PDF.

    OrCAD PDF exports retain `CO<refdes>` component markers and `PI<refdes>0<pin>`
    pin markers.  These are stronger evidence than free-form OCR labels and let
    the UI ask which component should drive the symbol-generation context.
    """

    path = Path(schematic_pdf).resolve()
    require_readable_document(path)
    pages_by_ref: dict[str, set[int]] = {}
    pins_by_ref: dict[str, set[str]] = {}
    names_by_ref: dict[str, dict[str, int]] = {}

    def base_ref(value: str) -> str:
        match = re.fullmatch(r"([A-Z]{1,3}\d+)[A-Z]?", value.upper())
        return match.group(1) if match else value.upper()

    def candidate_name(value: str) -> bool:
        token = value.strip()
        upper = token.upper()
        if not (3 <= len(token) <= 64) or " " in token:
            return False
        if upper.startswith(("PI", "CO")) or re.fullmatch(r"[A-Z]{1,3}\d+[A-Z]?", upper):
            return False
        if not re.search(r"[A-Z]", upper) or not re.search(r"\d", upper):
            return False
        if re.fullmatch(r"\d+(?:\.\d+)?(?:UF|NF|PF|UH|KHZ|MHZ|V|A|R|K|OHM)", upper):
            return False
        return bool(re.fullmatch(r"[A-Z0-9_./+-]+", upper))

    pages = PdfReader(path).pages
    total_pages = len(pages)
    for page_number, page in enumerate(pages, start=1):
        text = page.extract_text() or ""
        lines = [_clean(line) for line in text.splitlines() if _clean(line)]
        page_refs: set[str] = set()
        for marker in re.finditer(r"\bCO([A-Z]{1,3}\d+)[A-Z]?\b", text, re.I):
            page_refs.add(base_ref(marker.group(1)))
        for line in lines:
            if re.fullmatch(r"[A-Z]{1,3}\d+[A-Z]?", line, re.I):
                page_refs.add(base_ref(line))
        for ref in page_refs:
            pages_by_ref.setdefault(ref, set()).add(page_number)
            pin_pattern = re.compile(rf"\bPI{re.escape(ref)}0([A-Z0-9]+)\b", re.I)
            pins_by_ref.setdefault(ref, set()).update(match.group(1).upper() for match in pin_pattern.finditer(text))

        for index, line in enumerate(lines):
            if not re.fullmatch(r"[A-Z]{1,3}\d+[A-Z]?", line, re.I):
                continue
            ref = base_ref(line)
            start = max(0, index - 3)
            stop = min(len(lines), index + 4)
            for candidate_index in range(start, stop):
                candidate = lines[candidate_index]
                if not candidate_name(candidate):
                    continue
                score = 8 - min(abs(candidate_index - index), 3)
                if "-" in candidate or "_" in candidate:
                    score += 2
                names_by_ref.setdefault(ref, {})[candidate] = names_by_ref.setdefault(ref, {}).get(candidate, 0) + score
        if progress:
            progress(page_number, total_pages)

    components: list[dict[str, object]] = []
    for ref, pages in pages_by_ref.items():
        pins = pins_by_ref.get(ref, set())
        # Only integrated-circuit references are eligible for schematic-PDF
        # symbol reconstruction.  Nets, power ports and discrete references
        # must never be promoted into OLB package candidates.
        if len(pins) < 3 or not re.fullmatch(r"U\d+", ref):
            continue
        ranked = names_by_ref.get(ref, {})
        part_name = max(ranked, key=ranked.get) if ranked else "未辨识料号"
        components.append({
            "refdes": ref,
            "part_name": part_name,
            "pin_count": len(pins),
            "pages": sorted(pages),
            "pin_numbers": sorted(pins),
        })
    components.sort(key=lambda item: (0 if str(item["refdes"]).startswith("U") else 1, -int(item["pin_count"]), str(item["refdes"])))
    return components


def extract_orcad_symbol_spec_from_schematic(
    schematic_pdf: str | Path,
    component_refdes: str,
    *,
    component: dict[str, object] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> OrcadSymbolSpec:
    """Reconstruct one Capture symbol directly from an EDA-exported PDF.

    This is intentionally independent of a datasheet.  It uses the PDF's
    component/pin markers as the physical-pin authority and the visible symbol
    labels as pin names.  Heterogeneous ``CO<refdes>A/B/...`` markers preserve
    the original Part split instead of inventing a new one.
    """

    path = Path(schematic_pdf).resolve()
    require_readable_document(path)
    refdes = component_refdes.strip().upper()
    selected = component
    if selected is None:
        selected = next((item for item in inspect_schematic_components(path) if item["refdes"] == refdes), None)
    if selected is None:
        raise ValueError(f"原理图中找不到所选元件 {refdes}。")

    selected_pages = {int(page) for page in selected.get("pages", [])}
    pin_numbers = {str(number).upper() for number in selected.get("pin_numbers", [])}
    if not pin_numbers:
        raise ValueError(f"{refdes} 没有可验证的实体Pin编号。")

    names: dict[str, tuple[str, int]] = {}
    sections: dict[str, str] = {}
    net_names: dict[str, str] = {}
    pages = PdfReader(path).pages
    work_pages = [(index, page) for index, page in enumerate(pages, start=1) if not selected_pages or index in selected_pages]
    pin_marker = re.compile(rf"\bPI{re.escape(refdes)}0([A-Z0-9]+)\b", re.I)
    marker_stream = re.compile(
        rf"\bPI{re.escape(refdes)}0([A-Z0-9]+)\b|\bCO{re.escape(refdes)}([A-Z])\b",
        re.I,
    )

    def usable_visible_name(value: str) -> bool:
        upper = value.upper()
        return bool(
            1 <= len(value) <= 80
            and re.fullmatch(r"[A-Z][A-Z0-9_./#+-]*", upper)
            and not upper.startswith(("PI", "CO", "NL", "PO"))
            and upper not in {refdes, str(selected.get("part_name", "")).upper()}
        )

    # Prefer the actual rendered symbol geometry.  EDA PDFs often concatenate
    # two adjacent pins in plain-text extraction; word coordinates preserve the
    # nearest visible label on the left or right of each pin number.
    geometry_names: dict[str, tuple[float, str, int]] = {}
    geometry_pins: dict[str, tuple[float, str, float, str, int]] = {}
    part_aspect_ratios: dict[str, float] = {}
    part_source_sizes: dict[str, list[float]] = {}
    geometry_source_pages: set[int] = set()
    with pdfplumber.open(path) as geometry_pdf:
        for page_number, geometry_page in enumerate(geometry_pdf.pages, start=1):
            if selected_pages and page_number not in selected_pages:
                continue
            words = geometry_page.extract_words() or []
            rects = geometry_page.rects or []
            for reference_word in words:
                reference_match = re.fullmatch(
                    rf"{re.escape(refdes)}([A-Z])",
                    str(reference_word.get("text", "")),
                    re.I,
                )
                if not reference_match:
                    continue
                section = reference_match.group(1).upper()
                candidates = [
                    rect for rect in rects
                    if float(rect.get("width", 0)) >= 20
                    and float(rect.get("height", 0)) >= 20
                    and abs(float(rect["x0"]) - float(reference_word["x0"])) <= 3
                    and float(reference_word["bottom"]) - 2 <= float(rect["top"]) <= float(reference_word["bottom"]) + 12
                ]
                if not candidates:
                    continue
                body = min(candidates, key=lambda rect: float(rect["width"]) * float(rect["height"]))
                width = float(body["width"])
                height = float(body["height"])
                part_aspect_ratios[section] = height / width
                part_source_sizes[section] = [width, height]
                geometry_source_pages.add(page_number)
                center_x = (float(body["x0"]) + float(body["x1"])) / 2
                for pin_word in words:
                    number = str(pin_word.get("text", "")).upper()
                    if number not in pin_numbers:
                        continue
                    pin_center_x = (float(pin_word["x0"]) + float(pin_word["x1"])) / 2
                    pin_center_y = (float(pin_word["top"]) + float(pin_word["bottom"])) / 2
                    if not float(body["top"]) - 5 <= pin_center_y <= float(body["bottom"]) + 5:
                        continue
                    edge_distance = min(abs(pin_center_x - float(body["x0"])), abs(pin_center_x - float(body["x1"])))
                    if edge_distance > 45:
                        continue
                    side = "left" if pin_center_x < center_x else "right"
                    y_ratio = min(1.0, max(0.0, (pin_center_y - float(body["top"])) / height))
                    previous = geometry_pins.get(number)
                    if previous is None or edge_distance < previous[0]:
                        geometry_pins[number] = (edge_distance, side, y_ratio, section, page_number)
            for number_word in words:
                number = str(number_word.get("text", "")).upper()
                if number not in pin_numbers:
                    continue
                nearby: list[tuple[float, str]] = []
                for candidate_word in words:
                    candidate = str(candidate_word.get("text", "")).upper()
                    if len(candidate) < 2 or not usable_visible_name(candidate) or candidate in pin_numbers:
                        continue
                    if abs(float(candidate_word["top"]) - float(number_word["top"])) > 4.0:
                        continue
                    if float(candidate_word["x0"]) >= float(number_word["x1"]):
                        gap = float(candidate_word["x0"]) - float(number_word["x1"])
                    elif float(candidate_word["x1"]) <= float(number_word["x0"]):
                        gap = float(number_word["x0"]) - float(candidate_word["x1"])
                    else:
                        continue
                    if 1.0 <= gap <= 80.0:
                        nearby.append((gap, candidate))
                if nearby:
                    gap, candidate = min(nearby, key=lambda item: item[0])
                    score = gap - min(len(candidate), 24) * 0.35 - candidate.count("_") * 2.0
                    previous = geometry_names.get(number)
                    if previous is None or score < previous[0]:
                        geometry_names[number] = (score, candidate, page_number)
    for number, (_score, candidate, page_number) in geometry_names.items():
        names[number] = (candidate, page_number)

    line_names: set[str] = set()
    for completed, (page_number, page) in enumerate(work_pages, start=1):
        text = page.extract_text() or ""
        page_pins = {match.group(1).upper() for match in pin_marker.finditer(text)} & pin_numbers

        # Visible EDA symbol labels commonly extract as NAME A2 or NAMEA2.
        # Stop at the machine-marker appendix so PI/CO/NL tokens cannot become
        # accidental pin names.
        visible_text = re.split(rf"\bPI{re.escape(refdes)}0", text, maxsplit=1, flags=re.I)[0]
        for raw_line in visible_text.splitlines():
            compact = re.sub(r"\s+", "", raw_line).upper()
            for number in sorted(page_pins, key=len, reverse=True):
                if not compact.endswith(number) or len(compact) <= len(number):
                    continue
                candidate = compact[:-len(number)]
                # PDF text extraction may concatenate adjacent pins on one
                # row, e.g. ``VDDIO_RFA5WL_VDDCC3``.  The last preceding
                # physical pin token is the boundary of the current label.
                boundaries = [
                    position + len(other)
                    for other in pin_numbers
                    if other != number and (position := candidate.rfind(other)) >= 0
                ]
                if boundaries:
                    candidate = candidate[max(boundaries):]
                if usable_visible_name(candidate) and number not in line_names:
                    names[number] = (candidate, page_number)
                    line_names.add(number)
                break

        # Net names are valid schematic evidence and are a conservative
        # fallback only when the visible symbol label could not be extracted.
        for match in re.finditer(
            rf"\bPI{re.escape(refdes)}0([A-Z0-9]+)\b\s+NL([A-Z0-9]+)",
            text,
            re.I,
        ):
            number = match.group(1).upper()
            if number in pin_numbers:
                net_names.setdefault(number, re.sub(r"0+", "_", match.group(2).upper()).strip("_"))

        pending: list[str] = []
        for marker in marker_stream.finditer(text):
            number, section = marker.group(1), marker.group(2)
            if number:
                number = number.upper()
                if number in pin_numbers and number not in sections and number not in pending:
                    pending.append(number)
            elif section:
                for number in pending:
                    sections[number] = section.upper()
                pending = []
        if progress:
            progress(completed, len(work_pages))

    unresolved = sorted(pin_numbers - set(names))
    for number in unresolved:
        fallback = net_names.get(number) or f"PIN_{number}"
        names[number] = (fallback, min(selected_pages) if selected_pages else 1)

    def physical_pin_key(value: str) -> tuple[object, ...]:
        if value.isdigit():
            return (0, int(value))
        match = re.fullmatch(r"([A-Z]+)(\d+)", value)
        if match:
            return (1, match.group(1), int(match.group(2)))
        return (2, value)

    ordered_numbers = sorted(pin_numbers, key=physical_pin_key)
    pins: list[OrcadPin] = []
    for order, number in enumerate(ordered_numbers, start=1):
        name, evidence_page = names[number]
        electrical_type = normalize_electrical_type("", name)
        geometry = geometry_pins.get(number)
        pins.append(OrcadPin(
            number=number,
            name=name,
            electrical_type=electrical_type,
            description=f"Schematic PDF visible pin for {refdes}",
            evidence_page=evidence_page,
            section=sections.get(number, geometry[3] if geometry else "A"),
            functional_group=_assign_section(OrcadPin(number, name, electrical_type, "", evidence_page)),
            symbol_side=geometry[1] if geometry else "",
            symbol_order=round(geometry[2] * 10000) if geometry else order,
            source_y_ratio=geometry[2] if geometry else -1.0,
        ))

    # Preserve a real heterogeneous split only when the PDF supplies it.
    # Otherwise one symbol remains one Part regardless of pin count.
    if not sections:
        for pin in pins:
            pin.section = "A"

    part_name = _clean(selected.get("part_name"))
    if not part_name or part_name == "未辨识料号":
        part_name = refdes
    return OrcadSymbolSpec(
        part_name=part_name,
        manufacturer="SCHEMATIC PDF",
        datasheet_revision="SCHEMATIC-EXTRACTED",
        package_variant="SCHEMATIC PDF",
        pcb_footprint="",
        source_pdf=str(path),
        pins=pins,
        schematic_context_source=str(path),
        context_matched_pins=len(pins),
        schematic_component_refdes=refdes,
        part_aspect_ratios=part_aspect_ratios,
        part_source_sizes=part_source_sizes,
        geometry_source_pages=sorted(geometry_source_pages),
    )


def apply_schematic_context(
    spec: OrcadSymbolSpec,
    schematic_pdf: str | Path,
    component_refdes: str = "",
) -> int:
    """Select mux-aware visible names from an actual schematic design.

    Datasheets define the legal function set but cannot reveal which mux a board
    enables.  A schematic exported from an EDA tool provides that missing design
    intent through the symbol pin label and connected net context.  Only labels
    that match the physical ball and one of the datasheet functions are accepted.
    """

    source = Path(schematic_pdf).resolve()
    evidence: dict[str, list[tuple[int, str]]] = {}
    part_assignments: dict[str, str] = {}
    by_number = {pin.number.upper(): pin for pin in spec.pins}
    selected_pages: set[int] = set()
    selected_part_name = ""
    if component_refdes:
        selected = next(
            (item for item in inspect_schematic_components(source) if item["refdes"] == component_refdes.upper()),
            None,
        )
        if selected is None:
            raise ValueError(f"原理图中找不到所选元件 {component_refdes}，请重新载入原理图。")
        selected_pages = set(selected["pages"])
        selected_part_name = str(selected.get("part_name") or "")

    def normalized(value: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", value.upper())

    def valid_label(pin: OrcadPin, candidate: str) -> bool:
        candidate_key = normalized(candidate)
        aliases = [fn.name for fn in pin.functions if fn.name and fn.name != "-"]
        if pin.gpio_number and normalized(f"GPIO{pin.gpio_number}") not in candidate_key:
            return False
        return any(normalized(alias) and normalized(alias) in candidate_key for alias in aliases)

    def parse_symbol_line(line: str) -> list[tuple[str, OrcadPin, str]]:
        """Split PDF text lines that may contain several adjacent symbol pins."""

        memo: dict[int, list[tuple[str, OrcadPin, str]] | None] = {}

        def visit(start: int) -> list[tuple[str, OrcadPin, str]] | None:
            while start < len(line) and line[start].isspace():
                start += 1
            if start == len(line):
                return []
            if start in memo:
                return memo[start]
            best: list[tuple[str, OrcadPin, str]] | None = None
            for number, pin in by_number.items():
                search_from = start + 1
                while True:
                    index = line.upper().find(number, search_from)
                    if index < 0:
                        break
                    candidate = line[start:index].strip()
                    if candidate and len(candidate) <= 80 and valid_label(pin, candidate):
                        tail = visit(index + len(number))
                        if tail is not None:
                            option = [(number, pin, candidate), *tail]
                            if best is None or len(option) > len(best):
                                best = option
                    search_from = index + 1
            memo[start] = best
            return best

        return visit(0) or []

    for page_number, page in enumerate(PdfReader(source).pages, start=1):
        if selected_pages and page_number not in selected_pages:
            continue
        block_pins: list[tuple[str, OrcadPin, str]] = []
        awaiting_reference = False
        pending_reference = ""
        for raw_line in (page.extract_text() or "").splitlines():
            line = _clean(raw_line)
            if not line:
                continue
            # A repeated selected part name delimits a symbol body in EDA PDF
            # exports.  This is intentionally device-neutral; Apollo510 is a
            # regression sample, not a trigger condition.
            line_key = normalized(line)
            part_key = normalized(selected_part_name)
            if part_key and part_key != normalized("未辨识料号") and part_key in line_key:
                if pending_reference:
                    for number, _pin, _candidate in block_pins:
                        part_assignments[number] = pending_reference
                    block_pins = []
                    pending_reference = ""
                    continue
                awaiting_reference = True
                continue
            reference_pattern = (
                rf"{re.escape(component_refdes)}([A-Z])"
                if component_refdes
                else r"[A-Z]{1,3}\d+([A-Z])"
            )
            reference_match = re.fullmatch(reference_pattern, line, re.I)
            if reference_match:
                section = reference_match.group(1).upper()
                if awaiting_reference:
                    for number, _pin, _candidate in block_pins:
                        part_assignments[number] = section
                    block_pins = []
                    awaiting_reference = False
                else:
                    pending_reference = section
                continue
            pairs = parse_symbol_line(line)
            block_pins.extend(pairs)
            for number, _pin, candidate in pairs:
                evidence.setdefault(number, []).append((page_number, candidate))

    for number, choices in evidence.items():
        pin = by_number[number]
        page_number, chosen = max(choices, key=lambda item: (item[1].count("_"), len(item[1])))
        pin.name = chosen
        pin.design_context_name = chosen
        pin.design_context_page = page_number
        pin.electrical_type = normalize_electrical_type(pin.electrical_type, chosen)
    if len(part_assignments) == len(spec.pins):
        for number, section in part_assignments.items():
            by_number[number].section = section
    elif evidence:
        apply_symbol_partition_policy(spec.pins)
    spec.schematic_context_source = str(source)
    spec.context_matched_pins = len(evidence)
    spec.schematic_component_refdes = component_refdes.upper()
    return len(evidence)



def _table_columns(table: list[list[object]]) -> tuple[int, dict[str, int]] | None:
    """Find a pin-number/name header, including two- or three-row PDF headers."""

    if not table:
        return None
    width = max(len(row) for row in table[:3])
    for depth in range(1, min(3, len(table)) + 1):
        mapping: dict[str, int] = {}
        headers: list[str] = []
        for column in range(width):
            header = " ".join(_clean(table[row][column]) if column < len(table[row]) else "" for row in range(depth))
            headers.append(header)
            role = _header_role(header)
            if role and role not in mapping:
                mapping[role] = column
        if "name" in mapping and "number" not in mapping:
            for column, header in enumerate(headers):
                normalized = re.sub(r"[^a-z0-9]+", " ", header.lower()).strip()
                if column != mapping["name"] and (
                    normalized in {"no", "pin no", "pi no", "number"}
                    or re.search(r"\b(?:pin|pi|ball|terminal)?\s*no\b", normalized)
                ):
                    mapping["number"] = column
                    break
            # TI Chinese tables commonly group 名称 + package-designator under 引脚.
            if "number" not in mapping and any(token in headers[mapping["name"]] for token in ("引脚", "引腳", "管脚")):
                adjacent = mapping["name"] + 1
                if adjacent < width:
                    mapping["number"] = adjacent
        if "number" in mapping and "name" in mapping and mapping["number"] != mapping["name"]:
            next_row = table[depth] if depth < len(table) else []
            possible_number_columns = set(range(mapping['name'])) | {mapping['number']}
            next_name = _clean(next_row[mapping['name']]) if mapping['name'] < len(next_row) else ''
            next_is_data = bool(next_name and not _header_role(next_name) and any(
                _pin_numbers(_clean(next_row[column]))
                for column in possible_number_columns if column < len(next_row)))
            # I/O is both a header spelling and a legitimate data-cell value.
            # A row with a physical number and signal name is data; do not
            # consume the first pin on every continuation page as header row 3.
            if not next_is_data and depth < min(3, len(table)) and any(
                _header_role(table[depth][column] if column < len(table[depth]) else "")
                for column in range(width)
            ):
                continue
            return depth, mapping
    return None



def _pin_number_sort_key(value: str) -> tuple[tuple[int, object], ...]:
    """Sort numeric, BGA and mixed pin designators deterministically."""

    return tuple(
        (0, int(token)) if token.isdigit() else (1, token.upper())
        for token in re.findall(r"\d+|\D+", value)
    )



def _apply_datasheet_diagram_geometry(
    spec: OrcadSymbolSpec,
    path: Path,
    evidence_pages: Iterable[int],
) -> None:
    """Use a datasheet application/block diagram as symbol-layout evidence."""

    wanted = {pin.name.upper().lstrip("/"): pin for pin in spec.pins if pin.name}
    best: tuple[tuple[int, int, float], dict[str, object], list[dict[str, object]], int] | None = None
    with pdfplumber.open(path) as pdf:
        for page_number in sorted(set(evidence_pages)):
            if not 1 <= page_number <= len(pdf.pages):
                continue
            page = pdf.pages[page_number - 1]
            words = page.extract_words() or []
            for rect in page.rects or []:
                width = float(rect.get("width", 0))
                height = float(rect.get("height", 0))
                if width < 35 or height < 70 or not 0.7 <= height / width <= 6.0:
                    continue
                matched_words = [
                    word for word in words
                    if str(word.get("text", "")).upper().lstrip("/") in wanted
                    and float(rect["top"]) - 18 <= (float(word["top"]) + float(word["bottom"])) / 2 <= float(rect["bottom"]) + 18
                    and float(rect["x0"]) - 75 <= (float(word["x0"]) + float(word["x1"])) / 2 <= float(rect["x1"]) + 75
                ]
                unique_names = {str(word["text"]).upper().lstrip("/") for word in matched_words}
                part_inside = any(
                    re.sub(r"[^A-Z0-9]", "", spec.part_name.upper()) in re.sub(r"[^A-Z0-9]", "", str(word.get("text", "")).upper())
                    and float(rect["x0"]) <= float(word["x0"]) <= float(rect["x1"])
                    and float(rect["top"]) <= float(word["top"]) <= float(rect["bottom"])
                    for word in words
                )
                score = (int(part_inside), len(unique_names), -(width * height))
                if len(unique_names) >= max(6, min(10, len(wanted) // 3)) and (best is None or score > best[0]):
                    best = (score, rect, matched_words, page_number)
    if best is None:
        return

    _score, body, matched_words, page_number = best
    # The current Capture symbol renderer deliberately supports readable
    # two-sided symbols. A reference schematic with many pins entering from
    # the top/bottom (common for LED arrays and analog front ends) cannot be
    # faithfully projected onto left/right edges. Reject that geometry and let
    # the generic functional balancer produce an even two-sided symbol.
    vertical_names: set[str] = set()
    horizontal_names: set[str] = set()
    for word in matched_words:
        center_x = (float(word["x0"]) + float(word["x1"])) / 2
        center_y = (float(word["top"]) + float(word["bottom"])) / 2
        distances = {
            "left": abs(center_x - float(body["x0"])),
            "right": abs(center_x - float(body["x1"])),
            "top": abs(center_y - float(body["top"])),
            "bottom": abs(center_y - float(body["bottom"])),
        }
        nearest_edge = min(distances, key=distances.get)  # type: ignore[arg-type]
        target = vertical_names if nearest_edge in {"top", "bottom"} else horizontal_names
        target.add(str(word["text"]).upper().lstrip("/"))
    edge_name_count = len(vertical_names | horizontal_names)
    if len(vertical_names) >= 3 and len(vertical_names) * 5 >= edge_name_count:
        return

    body_center = (float(body["x0"]) + float(body["x1"])) / 2
    word_positions: dict[str, list[tuple[float, str, float]]] = {}
    for word in matched_words:
        name = str(word["text"]).upper().lstrip("/")
        center_x = (float(word["x0"]) + float(word["x1"])) / 2
        center_y = (float(word["top"]) + float(word["bottom"])) / 2
        side = "left" if center_x < body_center else "right"
        body_edge = float(body["x0"]) if side == "left" else float(body["x1"])
        word_positions.setdefault(name, []).append((center_y, side, abs(center_x - body_edge)))
    projected_sides = [min(choices, key=lambda item: (item[2], item[0]))[1] for choices in word_positions.values()]
    projected_left = projected_sides.count("left")
    projected_right = projected_sides.count("right")
    if min(projected_left, projected_right) * 2 < max(projected_left, projected_right):
        return
    # A diagram label may represent several physical balls (for example three
    # SYS balls).  Preserve the label's normalized vertical anchor and spread
    # those physical pins narrowly around it instead of stacking them.
    pins_by_name: dict[str, list[OrcadPin]] = {}
    for pin in spec.pins:
        pins_by_name.setdefault(pin.name.upper().lstrip("/"), []).append(pin)
    body_top = float(body["top"])
    body_height = float(body["height"])
    for name, pins in pins_by_name.items():
        choices = word_positions.get(name)
        if not choices:
            continue
        # A typical-application drawing can repeat a name on a remote net or
        # connector.  The label nearest the IC body edge is the pin-position
        # evidence; choosing the highest occurrence reverses functional groups.
        center_y, side, _edge_distance = min(choices, key=lambda item: (item[2], item[0]))
        anchor = max(0.03, min(0.97, (center_y - body_top) / body_height))
        spread = min(0.035, 0.12 / max(1, len(pins) - 1))
        ordered_pins = sorted(pins, key=lambda pin: _pin_number_sort_key(pin.number))
        for index, pin in enumerate(ordered_pins):
            offset = (index - (len(ordered_pins) - 1) / 2) * spread
            pin.symbol_side = side
            pin.source_y_ratio = max(0.02, min(0.98, anchor + offset))
            pin.symbol_order = round(pin.source_y_ratio * 10_000)
            pin.architecture_evidence_page = page_number

    # Pins not explicitly printed in a simplified diagram still need stable
    # positions.  Interpolate them from the generic functional order on their
    # selected side.  This keeps the rule deterministic for every manufacturer
    # and avoids reverting to a square, arbitrarily packed symbol.
    for side in ("left", "right"):
        side_pins = sorted(
            (pin for pin in spec.pins if pin.symbol_side == side),
            key=lambda pin: (pin.symbol_order, _pin_number_sort_key(pin.number)),
        )
        for index, pin in enumerate(side_pins, start=1):
            if pin.source_y_ratio < 0:
                pin.source_y_ratio = index / (len(side_pins) + 1)
        # Cadence snaps coordinates to its drawing grid. Enforce enough
        # normalized separation before generation so adjacent evidence anchors
        # cannot collapse onto the same visible location after rounding.
        minimum_gap = min(0.04, 0.88 / max(1, len(side_pins) - 1))
        ordered_by_y = sorted(
            side_pins,
            key=lambda pin: (pin.source_y_ratio, _pin_number_sort_key(pin.number)),
        )
        for index in range(1, len(ordered_by_y)):
            ordered_by_y[index].source_y_ratio = max(
                ordered_by_y[index].source_y_ratio,
                ordered_by_y[index - 1].source_y_ratio + minimum_gap,
            )
        if ordered_by_y and ordered_by_y[-1].source_y_ratio > 0.98:
            ordered_by_y[-1].source_y_ratio = 0.98
            for index in range(len(ordered_by_y) - 2, -1, -1):
                ordered_by_y[index].source_y_ratio = min(
                    ordered_by_y[index].source_y_ratio,
                    ordered_by_y[index + 1].source_y_ratio - minimum_gap,
                )
        for pin in ordered_by_y:
            pin.source_y_ratio = max(0.02, min(0.98, pin.source_y_ratio))
            pin.symbol_order = round(pin.source_y_ratio * 10_000)
    spec.part_aspect_ratios["A"] = float(body["height"]) / float(body["width"])
    spec.geometry_source_pages = [page_number]


def infer_symbol_architecture(spec: OrcadSymbolSpec, pdf_path: str | Path) -> None:
    """Assign functional adjacency, symbol side and order from PDF evidence.

    This does not invent a board configuration.  It combines the verified pin
    table with named Functional Block Diagram / Typical Application evidence,
    then applies conservative electrical-flow rules.  All matching is driven
    by document structure and pin evidence; no manufacturer or part-number
    profile is permitted in this production path.
    """

    path = Path(pdf_path).resolve()
    evidence_pages: list[int] = []
    try:
        # Reuse the shared all-page Evidence Gate performed before either
        # Cadence backend is allowed to run.  This keeps OrCAD architecture
        # inference and PCB footprint extraction on the same document index.
        from .pdf_evidence import analyze_document_evidence

        profile = analyze_document_evidence(path)
        evidence_pages = sorted(set(
            profile.roles.get("functional_block_diagram", [])
            + profile.roles.get("typical_application", [])
        ))
        unreadable_diagrams = sorted(set(evidence_pages) & set(profile.unreadable_text_pages))
        if unreadable_diagrams:
            spec.review_warnings.append(
                "架构图文字层无法可靠读取，需图像/OCR复核页：" + ",".join(map(str, unreadable_diagrams))
                + "；本次未把这些图面当成已确认的Pin位置。"
            )
            evidence_pages = [p for p in evidence_pages if p not in unreadable_diagrams]
    except Exception:  # malformed test fixtures or image-only PDFs keep pin-table evidence
        evidence_pages = []
    spec.architecture_evidence_pages = evidence_pages

    left_order = right_order = 0
    for pin in spec.pins:
        name = pin.name.upper().lstrip("/")
        suffix = int((re.search(r"(\d+)$", name) or [0, 0])[1] or 0)
        if re.fullmatch(r"(?:IN|VIN|VBUS|DCIN|ACIN)", name):
            group, side, order = "POWER INPUT", "left", 10 + suffix
        elif re.fullmatch(r"(?:CE|EN|ENABLE|CHG_EN)", name):
            group, side, order = "ENABLE / CHARGE CONTROL", "left", 20 + suffix
        elif re.fullmatch(r"VPU|GPIO\d+", name):
            group, side, order = "DIGITAL CONTROL", "left", 30 + suffix
        elif re.search(r"ADC|THERM", name):
            group, side, order = "ADC / THERMAL", "left", 40 + suffix
        elif re.fullmatch(r"(?:INT|IRQ|MR|RESET|RST|SCL|SDA|MOSI|MISO|SCK|CS)\d*", name):
            group, side, order = "HOST INTERFACE", "left", 50 + suffix
        elif (match := re.fullmatch(r"VIN(?:LS|LDO)(\d+)", name)):
            group, side, order = f"LDO{match.group(1)}", "left", 60 + int(match.group(1))
        elif (match := re.fullmatch(r"(?:LS)?LDO(\d+)", name)):
            group, side, order = f"LDO{match.group(1)}", "right", 30 + int(match.group(1))
        elif re.fullmatch(r"SYS|VSYS|SYSTEM", name):
            group, side, order = "SYSTEM RAIL", "right", 10 + suffix
        elif re.fullmatch(r"BAT|VBAT|TS|NTC", name):
            group, side, order = "BATTERY / THERMAL", "right", 20 + suffix
        elif re.fullmatch(r"(?:BK|BUCK)(?:OUT|SW\d*|GND)", name):
            group, side, order = "BUCK", "right", 40 + suffix
        elif re.fullmatch(r"(?:BB|BBS|BUCKBOOST)(?:OUT|SW\d*|GND)", name):
            group, side, order = "BUCK-BOOST", "right", 50 + suffix
        else:
            is_ground = bool(re.search(r"(?:^|_)(?:GND|VSS|AGND|DGND)", name))
            is_output = pin.electrical_type == "Output" or bool(re.search(r"OUT|SW\d*$|SYS|VREF", name))
            is_input = pin.electrical_type == "Input" or bool(re.search(r"^(?:VIN|VBUS|IN|EN|CE|RESET|MR)", name))
            if is_ground:
                group, side = "GROUND", "right"
            elif pin.electrical_type == "Power" and not is_input:
                group, side = "POWER OUTPUT", "right"
            elif is_output:
                group, side = "OUTPUT", "right"
            elif _INTERFACE_RE.search(name) or "GPIO" in name:
                group, side = "CONTROL / INTERFACE", "left"
            elif is_input:
                group, side = "INPUT", "left"
            else:
                group, side = "GENERAL", "left" if left_order <= right_order else "right"
            if side == "left":
                left_order += 1
                order = 100 + left_order
            else:
                right_order += 1
                order = 100 + right_order
        pin.functional_group = group
        pin.symbol_side = side
        pin.symbol_order = order
        pin.architecture_evidence_page = 0
    if evidence_pages:
        _apply_datasheet_diagram_geometry(spec, path, evidence_pages)


def _text_pin_rows(text: str) -> list[tuple[list[str], str, str]]:
    """Recover simple text-based Pin No./Mnemonic tables when ruling lines confuse OCR."""

    if not re.search(r"Pin\s+No\.?\s+Mnemonic\s+Description", text, re.I):
        return []
    rows: list[tuple[list[str], str, str]] = []
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Z]{0,3}\d{1,4}(?:\s*[,;]\s*[A-Z]{0,3}\d{1,4})*)\s+([A-Z][A-Z0-9_/#-]*)\s+(.+?)\s*$", line, re.I)
        if not match:
            continue
        numbers = _pin_numbers(match.group(1))
        if numbers:
            rows.append((numbers, match.group(2), match.group(3)))
    return rows


def _generic_section(name: str, electrical_type: str) -> str:
    if electrical_type == "Power" or _POWER_RE.search(name):
        return "D"
    if _CLOCK_ANALOG_RE.search(name):
        return "C"
    if _INTERFACE_RE.search(name):
        return "B"
    return "A"


def _compact_sections(pins: list[OrcadPin]) -> None:
    """OrCAD heterogeneous parts must be contiguous even when a category is empty."""

    used = [letter for letter in "ABCD" if any(pin.section == letter for pin in pins)]
    mapping = {source: target for source, target in zip(used, "ABCD")}
    for pin in pins:
        pin.section = mapping[pin.section]


def _require_complete_declared_pin_set(spec: OrcadSymbolSpec) -> None:
    """Check structural package headers before layout/native generation.

    Rotated PDF table headers can be emitted backwards. Decode only a complete
    recognized package-family/count token, never a part number or filename.
    Extra documented EP pins are not silently removed to fit the body count.
    """
    label = re.sub(r'[^A-Z0-9]', '', spec.package_variant.partition('::MODEL=')[0].upper())
    pattern = r'(?:TSSOP|SSOP|SOIC|SOP|MSOP|UFQFPN|UQFN|VQFN|QFN|LQFP|TQFP|QFP|DFN|WSON|SON|WLCSP|[A-Z]*BGA|LGA)([1-9][0-9]{0,3})'
    match = re.fullmatch(pattern, label) or re.fullmatch(pattern, label[::-1])
    if not match:
        return
    count = int(match[1])
    numbers = {pin.number for pin in spec.pins}
    missing = sorted(set(map(str, range(1, count + 1))) - numbers, key=int) if all(n.isdigit() for n in numbers) else []
    if len(numbers) < count or missing:
        raise ValueError(f'Physical pin count conflict: package header {spec.package_variant} declares {count}, '
                         f'extracted {len(numbers)} unique pins; missing numbered pins={missing}. '
                         'Complete the source pin table; no pins are synthesized.')


def extract_generic_pin_table_spec(
    pdf_path: str | Path,
    *,
    package_variant: str | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> OrcadSymbolSpec:
    """Extract a conservative physical-pin IR from common datasheet pin tables.

    A table is accepted only when distinct pin-number and signal/function columns
    are present. Repeated rows become alternate-function evidence for the same
    physical pin; conflicting evidence remains in the manifest for EE review.
    """

    path = Path(pdf_path).resolve()
    from .structured_pin_tables import read_scoped_pin_tables

    scoped_rows = read_scoped_pin_tables(path, package_variant, progress)
    column_warnings = []
    if not scoped_rows:
        from .column_pin_tables import read_column_pin_tables, read_ball_map_labels
        scoped_rows = read_column_pin_tables(path)
        by_number = {}
        for row in scoped_rows:
            by_number.setdefault(row['pin_number'], []).append(row)
        conflicts = {n:rows for n,rows in by_number.items() if len({r['pin_name'] for r in rows})>1}
        if conflicts:
            ball_map = read_ball_map_labels(path)
            for number,rows in conflicts.items():
                if number not in ball_map:
                    raise ValueError(f'Pin定义冲突：{number}；没有独立Ball Map佐证，不能自动选取。')
                mapped=ball_map[number]
                exact=next((r for r in rows if r['pin_name']==mapped['name']),None)
                replacement=dict(exact or rows[0],pin_name=mapped['name'],raw_type=exact['raw_type'] if exact else '')
                scoped_rows=[r for r in scoped_rows if r['pin_number']!=number]+[replacement]
                column_warnings.append(f"Pin {number}: 表格定义冲突 {[(r['pin_name'],r['page']) for r in rows]}；采用第{mapped['page']}页Ball Map名称 {mapped['name']}，电气类型仍须复核。")
    if scoped_rows:
        physical: dict[str, OrcadPin] = {}
        for row in scoped_rows:
            number, name = str(row["pin_number"]), str(row["pin_name"])
            if number in physical:
                if physical[number].name != name:
                    raise ValueError(
                        f"Pin定义冲突：{number}，第{physical[number].evidence_page}页为"
                        f"{physical[number].name}，第{row['page']}页为{name}。"
                        "请核对条件型号、封装或厂家勘误；不能当作复用功能合并。"
                    )
                continue
            functions=[PinFunction('', name, str(row['description']), str(row['raw_type']))]
            functions += [PinFunction('', fn,
                str(row.get('function_evidence',{}).get(fn,{}).get('description','')),
                str(row.get('function_evidence',{}).get(fn,{}).get('type','')))
                for fn in row.get('functions',[]) if fn!=name]
            display_name = _select_name(functions, '')[0] if len(functions)>1 else name
            physical[number] = OrcadPin(number, display_name,
                normalize_electrical_type(str(row["raw_type"]), name),
                str(row["description"]), int(row["page"]),
                functions=functions)
        pins = list(physical.values())
        apply_symbol_partition_policy(pins)
        stem = re.sub(r"[^A-Za-z0-9_]+", "_", path.stem).strip("_").upper()
        spec = OrcadSymbolSpec(stem[:64] or "DATASHEET_PART", "EE_REVIEW_REQUIRED", "PDF_SOURCE",
            package_variant or "PIN_TABLE_EXTRACTED", "", str(path), pins)
        spec.review_warnings.extend(column_warnings)
        _require_complete_declared_pin_set(spec)
        infer_symbol_architecture(spec, path)
        return spec
    unique: dict[str, OrcadPin] = {}
    candidate_tables = 0
    with pdfplumber.open(path) as pdf:
        total_pages = len(pdf.pages)
        for page_number, page in enumerate(pdf.pages, start=1):
            if progress:
                progress(page_number, total_pages)
            text = page.extract_text() or ""
            lower = text.lower()
            if not any(token in lower for token in ("pin", "ball", "terminal", "引脚", "引腳", "管脚", "接脚", "接腳")):
                continue
            if not any(token in lower for token in ("name", "signal", "function", "description", "名称", "名稱", "功能", "说明", "說明")):
                continue
            for table in page.extract_tables() or []:
                columns = _table_columns(table)
                if columns is None:
                    continue
                candidate_tables += 1
                header_rows, mapping = columns
                if package_variant:
                    variant_key = re.sub(r"[^A-Z0-9]+", "", package_variant.upper())
                    width = max(len(row) for row in table[:3])
                    for column in range(width):
                        header = " ".join(_clean(row[column]) if column < len(row) else "" for row in table[:3])
                        if variant_key and variant_key in re.sub(r"[^A-Z0-9]+", "", header.upper()):
                            mapping["number"] = column
                            break
                active_numbers: list[str] = []
                for raw_row in table[header_rows:]:
                    row = [_clean(cell) for cell in raw_row]
                    def cell(role: str) -> str:
                        index = mapping.get(role, -1)
                        return row[index] if 0 <= index < len(row) else ""

                    numbers = _pin_numbers(cell("number"))
                    name = cell("name").strip(" -")
                    description = cell("description")
                    raw_type = cell("type")
                    if numbers:
                        active_numbers = numbers
                    elif not active_numbers:
                        continue
                    if not name or _header_role(name):
                        continue
                    for number in active_numbers:
                        function = PinFunction("", name, description, raw_type)
                        if number in unique:
                            existing = unique[number]
                            if all(item.name != name for item in existing.functions):
                                existing.functions.append(function)
                            continue
                        electrical = normalize_electrical_type(raw_type, name)
                        unique[number] = OrcadPin(
                            number=number,
                            name=name,
                            electrical_type=electrical,
                            description=description,
                            evidence_page=page_number,
                            functions=[function],
                            section=_generic_section(name, electrical),
                        )
                    if numbers:
                        active_numbers = []

            # Some visually valid tables (for example ADAU7112 Table 6) are
            # extracted by PDF engines as a one-column box.  Use the page's
            # aligned text only when an explicit Pin No./Mnemonic header is
            # present; this remains stricter than free-form OCR guessing.
            for numbers, name, description in _text_pin_rows(text):
                candidate_tables += 1
                for number in numbers:
                    function = PinFunction("", name, description, "")
                    if number in unique:
                        existing = unique[number]
                        if all(item.name != name for item in existing.functions):
                            existing.functions.append(function)
                        continue
                    electrical = normalize_electrical_type("", name)
                    unique[number] = OrcadPin(
                        number=number,
                        name=name,
                        electrical_type=electrical,
                        description=description,
                        evidence_page=page_number,
                        functions=[function],
                    )

    if not unique:
        raise ValueError(
            "未找到同时包含Pin Number与Pin Name/Signal栏位的可验证Pin Table；已停止生成，请提供Pin表所在页或更清晰的Datasheet。"
        )
    if candidate_tables == 0:
        raise ValueError("未找到结构化Pin Table，无法安全建立原理图零件。")
    pins = list(unique.values())
    apply_symbol_partition_policy(pins)
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", path.stem).strip("_").upper() or "DATASHEET_PART"
    part_name = stem[:64]
    spec = OrcadSymbolSpec(
        part_name=part_name,
        manufacturer="EE_REVIEW_REQUIRED",
        datasheet_revision="PDF_SOURCE",
        package_variant=package_variant or "PIN_TABLE_EXTRACTED",
        pcb_footprint="",
        source_pdf=str(path),
        pins=pins,
    )
    _require_complete_declared_pin_set(spec)
    infer_symbol_architecture(spec, path)
    return spec


def extract_orcad_symbol_spec(
    pdf_path: str | Path,
    *,
    schematic_context_pdf: str | Path | None = None,
    package_variant: str | None = None,
    schematic_component_refdes: str = "",
    progress: Callable[[int, int], None] | None = None,
) -> OrcadSymbolSpec:
    """Use document structure only; file names never select device algorithms."""
    path = Path(pdf_path).resolve()
    require_readable_document(path)
    spec = extract_mux_table_spec(path, package_variant=package_variant, progress=progress)
    if spec is None:
        spec = extract_generic_pin_table_spec(path, package_variant=package_variant, progress=progress)
    if not spec.architecture_evidence_pages:
        infer_symbol_architecture(spec, path)
    if schematic_context_pdf:
        apply_schematic_context(spec, schematic_context_pdf, schematic_component_refdes)
    return spec


_PIN_ENUM = {
    "Input": 0,
    "Bidirectional": 1,
    "Output": 2,
    "OpenCollector": 3,
    "Passive": 4,
    "3State": 5,
    "Power": 7,
}


def _tcl_braced(value: object) -> str:
    text = str(value).replace("\\", "/").replace("}", "\\}")
    return "{" + text + "}"


@lru_cache(maxsize=4096)
def _visible_text_width(text: str) -> int:
    # Measure the same Arial face used by Capture. Keep the conservative
    # character estimate as a floor when font metrics are unavailable.
    estimate = len(text) * _CAPTURE_TEXT_UNIT_PER_CHAR
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 12)
        return max(estimate, int(font.getlength(text) + 1))
    except (OSError, ImportError):
        return estimate


def _symbol_body_dimensions(
    pins: list[OrcadPin],
    *,
    part_name: str,
    aspect_ratio: float = 0.0,
    source_size: list[float] | None = None,
) -> tuple[int, int]:
    """Return a body large enough for both pin columns and visible values.

    Capture renders left and right pin names inward from the two body edges.
    Therefore a source rectangle can have the correct aspect ratio yet still
    be unreadable after reconstruction when its coordinate scale is too small
    for Capture's fixed symbol font.  Scale the whole source rectangle up until
    the longest opposing labels fit, preserving its evidenced aspect ratio.
    """

    left = [pin for pin in pins if pin.symbol_side == "left"]
    right = [pin for pin in pins if pin.symbol_side == "right"]
    if not left and not right:
        midpoint = (len(pins) + 1) // 2
        left, right = pins[:midpoint], pins[midpoint:]
    longest_left = max((len(pin.name) for pin in left), default=0)
    longest_right = max((len(pin.name) for pin in right), default=0)
    opposing_lengths = [
        len(left_pin.name) + len(right_pin.name)
        for left_pin in left
        for right_pin in right
        if left_pin.source_y_ratio < 0
        or right_pin.source_y_ratio < 0
        or abs(left_pin.source_y_ratio - right_pin.source_y_ratio) <= 0.05
    ]
    collision_length = max(opposing_lengths, default=max(longest_left, longest_right))
    # Start at single-label width. fit_actual_rows expands only overlapping
    # baselines, avoiding inflated bodies for staggered Golden pin placement.
    required_text_width = max(80,
                              max((_visible_text_width(p.name) for p in pins), default=0) + _SYMBOL_TEXT_GUTTER,
                              len(part_name) * _CAPTURE_TEXT_UNIT_PER_CHAR + 16)
    rows_per_side = max(len(left), len(right))
    minimum_height = max(80, rows_per_side * _MIN_CAPTURE_PIN_SPACING + 40)
    def fit_actual_rows(width: int, height: int) -> tuple[int, int]:
        for _ in range(4):
            ys = _pin_y_positions(left, height) | _pin_y_positions(right, height)
            required = max((
                _visible_text_width(a.name) + _visible_text_width(b.name) + _SYMBOL_TEXT_GUTTER
                for a in left for b in right
                if abs(ys[id(a)] - ys[id(b)]) < _MIN_CAPTURE_PIN_SPACING
            ), default=width)
            if required <= width:
                break
            width = required
            if aspect_ratio > 0:
                height = max(height, round(width * aspect_ratio))
        return width, height
    if aspect_ratio > 0:
        source_width = round(source_size[0] * _CAPTURE_UNITS_PER_PDF_POINT) if source_size else 0
        source_height = round(source_size[1] * _CAPTURE_UNITS_PER_PDF_POINT) if source_size else 0
        feasible_width = max(required_text_width, round(minimum_height / aspect_ratio))
        # A PDF drawing scale is not a minimum symbol size. Bound excessive
        # whitespace while retaining the evidenced shape and readable font.
        source_width = min(source_width, round(feasible_width * MAX_SOURCE_WHITESPACE_SCALE))
        source_height = min(source_height, round(max(minimum_height, feasible_width * aspect_ratio) * MAX_SOURCE_WHITESPACE_SCALE))
        width = max(required_text_width, source_width, round(minimum_height / aspect_ratio))
        height = max(minimum_height, round(width * aspect_ratio))
        if source_height > height:
            height = source_height
            width = max(width, round(height / aspect_ratio))
        return fit_actual_rows(width, height)
    width = max(120, required_text_width)
    height = max(minimum_height, round(width * 1.35) if len(pins) > 8 else 80)
    return fit_actual_rows(width, height)


def _pin_y_positions(pins: list[OrcadPin], body_height: int) -> dict[int, int]:
    """Preserve source order while enforcing Capture's readable pin pitch."""

    if not pins:
        return {}
    ordered = sorted(pins, key=lambda pin: (pin.source_y_ratio if pin.source_y_ratio >= 0 else pin.symbol_order, pin.number))
    desired = [20 + round(pin.source_y_ratio * (body_height - 40)) if pin.source_y_ratio >= 0 else 30 + index * _MIN_CAPTURE_PIN_SPACING for index, pin in enumerate(ordered)]
    positions: list[int] = []
    for value in desired:
        positions.append(max(20 if not positions else positions[-1] + _MIN_CAPTURE_PIN_SPACING, value))
    if positions[-1] > body_height - 20:
        positions[-1] = body_height - 20
        for index in range(len(positions) - 2, -1, -1):
            positions[index] = min(positions[index], positions[index + 1] - _MIN_CAPTURE_PIN_SPACING)
    return {id(pin): position for pin, position in zip(ordered, positions)}


def write_symbol_manifest(spec: OrcadSymbolSpec, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = spec.to_dict()
    data["electrical_evidence_review"] = [
        {"pin": p.number, "page": p.evidence_page, "generated_type": p.electrical_type,
         "raw_types": sorted({f.pin_type for f in p.functions if f.pin_type.strip()}),
         "status": "source_type_present_requires_semantic_review" if any(f.pin_type.strip() for f in p.functions) else "unresolved_no_explicit_type"}
        for p in spec.pins
    ]
    data["net_semantic_status"] = "unresolved_requires_native_connectivity_and_datasheet_type_review"
    data["visual_acceptance_status"] = "native_geometry_checks_do_not_replace_capture_gui_review"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_olb_generator_tcl(
    spec: OrcadSymbolSpec,
    olb_path: str | Path,
    script_path: str | Path,
    *,
    cadence_root: str | Path,
    log_path: str | Path,
    append: bool = False,
) -> Path:
    _require_complete_declared_pin_set(spec)
    # Enforce the same Gate for callers that construct an IR directly rather
    # than going through a PDF extractor.
    if spec.context_matched_pins != len(spec.pins):
        apply_symbol_partition_policy(spec.pins)
    olb = Path(olb_path).resolve()
    script = Path(script_path)
    log = Path(log_path).resolve()
    dll = Path(cadence_root) / "tools" / "bin" / "orDb_Dll_Tcl64.dll"
    grouped = {letter: [pin for pin in spec.pins if pin.section == letter] for letter in _SECTION_IDS}

    lines = [
        f"load {_tcl_braced(dll)} DboTclWriteBasic",
        f"set outPath {_tcl_braced(olb)}",
        f"set logPath {_tcl_braced(log)}",
        "set log [open $logPath w]",
        "proc fail {message} { global log; puts $log \"ERROR: $message\"; close $log; exit 2 }",
        "proc cs {value} { return [DboTclHelper_sMakeCString $value] }",
        "proc check {status context} { if {[$status Failed]} { fail $context } }",
        "set session [DboTclHelper_sCreateSession]",
        "catch {DboSession -this $session}",
        "set status [DboState]",
        "set referencePropName [cs {Part Reference}]",
        "set valuePropName [cs {Value}]",
        (
            "set lib [$session GetLib [cs $outPath] $status]"
            if append
            else "set lib [$session CreateLib [cs $outPath] $status]"
        ),
        "check $status open_library" if append else "check $status create_library",
        "set font [DboTclHelper_sMakeLOGFONT {Arial} 8 0 0 0 400 0 0 0 0 7 0 1 16]",
        "for {set i 0} {$i < 24} {incr i} { $lib SetDefaultFont $i $font }",
        f"set package [$lib NewPackage [cs {_tcl_braced(spec.part_name)}] $status]",
        "check $status create_package",
        "$package SetReferenceTemplate [cs {U}]",
        f"$package SetPCBFootprint [cs {_tcl_braced(spec.pcb_footprint)}]",
    ]

    populated = [letter for letter in _SECTION_IDS if grouped[letter]]
    section_ordinals = {letter: index for index, letter in enumerate(populated)}
    for letter in _SECTION_IDS:
        pins = grouped[letter]
        if not pins:
            continue
        has_pdf_geometry = letter in spec.part_aspect_ratios
        # With source geometry, preserve the PDF's side and vertical order.
        # Without it, keep functional families together and balance both edges.
        if has_pdf_geometry:
            sort_key = lambda pin: (  # noqa: E731
                pin.source_y_ratio if pin.source_y_ratio >= 0 else pin.symbol_order,
                pin.functional_group or _assign_section(pin),
                pin.number,
            )
        else:
            sort_key = lambda pin: (  # noqa: E731
                pin.functional_group or _assign_section(pin),
                pin.symbol_order,
                pin.number,
            )
        ordered = sorted(pins, key=sort_key)
        if has_pdf_geometry:
            left = [pin for pin in ordered if pin.symbol_side == "left"]
            right = [pin for pin in ordered if pin.symbol_side == "right"]
            for pin in (item for item in ordered if item.symbol_side not in {"left", "right"}):
                (left if len(left) <= len(right) else right).append(pin)
        else:
            left = []
            right = []
            left_limit = (len(ordered) + 1) // 2
            right_limit = len(ordered) // 2
            for pin in ordered:
                preferred = pin.symbol_side
                if preferred == "left" and len(left) < left_limit:
                    left.append(pin)
                elif preferred == "right" and len(right) < right_limit:
                    right.append(pin)
                elif len(left) < left_limit and (len(left) <= len(right) or len(right) >= right_limit):
                    left.append(pin)
                else:
                    right.append(pin)
        # Device pin positions and physical pin-number mapping share this exact
        # sequence; never sort again after creating the graphical pins.
        pins = left + right
        for pin in left:
            pin.symbol_side = "left"
        for pin in right:
            pin.symbol_side = "right"
        section_index = section_ordinals[letter]
        aspect_ratio = spec.part_aspect_ratios.get(letter, 0.0)
        body_width, body_height = _symbol_body_dimensions(
            pins,
            part_name=spec.part_name,
            aspect_ratio=aspect_ratio,
            source_size=spec.part_source_sizes.get(letter),
        )
        y_positions = _pin_y_positions(left, body_height) | _pin_y_positions(right, body_height)
        cell_name = f"{spec.part_name}_{letter}"
        part_name = f"{cell_name}.Normal"
        lines.extend(
            [
                f"set cell{letter} [$lib NewCell [cs {_tcl_braced(cell_name)}] $status]",
                f"check $status create_cell_{letter}",
                f"set part{letter} [$lib NewPart [cs {_tcl_braced(part_name)}] $status]",
                f"check $status create_part_{letter}",
                f"$cell{letter} AddPart $part{letter}",
                f"$part{letter} SetCellPtr $cell{letter}",
                f"$part{letter} SetPackagePtr $package",
                f"$part{letter} SetBoundingBox [DboTclHelper_sMakeCRect 0 0 {body_width} {body_height}]",
                f"$part{letter} SetColor 48",
                f"$part{letter} SetPinNamesAreVisible 1",
                f"$part{letter} SetPinNumbersAreVisible 1",
                f"$part{letter} SetPinNamesAreRotated 0",
                f"$part{letter} SetPartValue [cs {_tcl_braced(spec.part_name)}]",
                f"$part{letter} SetReference [cs {{U}}]",
                f"set referenceLocation{letter} [DboTclHelper_sMakeCPoint 8 -18]",
                f"set referenceDisplay{letter} [$part{letter} NewDisplayProp $status $referencePropName $referenceLocation{letter} 0 $font 48]",
                f"check $status create_reference_display_{letter}",
                f"$referenceDisplay{letter} SetHorizontalTextJustification 0",
                f"$referenceDisplay{letter} SetDisplayType 1",
                f"set valueLocation{letter} [DboTclHelper_sMakeCPoint 8 {body_height + 14}]",
                f"set valueDisplay{letter} [$part{letter} NewDisplayProp $status $valuePropName $valueLocation{letter} 0 $font 48]",
                f"check $status create_value_display_{letter}",
                f"$valueDisplay{letter} SetHorizontalTextJustification 0",
                f"$valueDisplay{letter} SetDisplayType 1",
                f"$part{letter} NewLine $status [DboTclHelper_sMakeCPoint 0 0] [DboTclHelper_sMakeCPoint {body_width} 0] 0 0",
                f"$part{letter} NewLine $status [DboTclHelper_sMakeCPoint {body_width} 0] [DboTclHelper_sMakeCPoint {body_width} {body_height}] 0 0",
                f"$part{letter} NewLine $status [DboTclHelper_sMakeCPoint {body_width} {body_height}] [DboTclHelper_sMakeCPoint 0 {body_height}] 0 0",
                f"$part{letter} NewLine $status [DboTclHelper_sMakeCPoint 0 {body_height}] [DboTclHelper_sMakeCPoint 0 0] 0 0",
                f"$part{letter} NewUserProp [cs {{Manufacturer}}] [cs {_tcl_braced(spec.manufacturer)}] $status",
                f"$part{letter} NewUserProp [cs {{Datasheet Revision}}] [cs {_tcl_braced(spec.datasheet_revision)}] $status",
                f"$part{letter} NewUserProp [cs {{Package Variant}}] [cs {_tcl_braced(spec.package_variant)}] $status",
                f"$part{letter} NewUserProp [cs {{PDF Source}}] [cs {_tcl_braced(Path(spec.source_pdf).name)}] $status",
            ]
        )
        for pin_index, pin in enumerate(pins, start=1):
            side_left = pin_index <= len(left)
            row_index = pin_index - 1 if side_left else pin_index - len(left) - 1
            y = y_positions[id(pin)]
            start_x, hot_x = (0, -10) if side_left else (body_width, body_width + 10)
            pin_type = _PIN_ENUM.get(pin.electrical_type, 4)
            lines.extend(
                [
                    f"set pin{letter}{pin_index} [$part{letter} NewSymbolPinScalar $status [cs {_tcl_braced(pin.name)}] {pin_type} [DboTclHelper_sMakeCPoint {start_x} {y}] [DboTclHelper_sMakeCPoint {hot_x} {y}] 1 {pin_index}]",
                    f"check $status create_pin_{letter}_{pin_index}",
                ]
            )
        lines.extend(
            [
                f"set device{letter} [$package NewDevice [cs {{{letter}}}] {section_index} $cell{letter} $status]",
                f"check $status create_device_{letter}",
            ]
        )
        for pin_index, pin in enumerate(pins, start=1):
            lines.extend(
                [
                    f"set pos{letter}{pin_index} [DboTclHelper_sMakeInt {pin_index}]",
                    f"set status [$device{letter} NewPinNumber [cs {_tcl_braced(pin.number)}] $pos{letter}{pin_index}]",
                    f"check $status map_pin_{letter}_{pin_index}",
                ]
            )

    lines.extend(
        [
            "set status [$lib SavePackageAll $package]",
            "check $status save_package",
            "$session MarkAllLibForSave $lib",
            "set status [$session SaveLib $lib]",
            "check $status save_library",
            f"puts $log \"OK package={spec.part_name} pins={len(spec.pins)} sections={sum(bool(v) for v in grouped.values())}\"",
            "close $log",
            "catch {$session RemoveLib $lib}",
            "catch {DboTclHelper_sDeleteSession $session}",
            "exit 0",
        ]
    )
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return script


def run_capture_tcl(script_path: str | Path, *, cadence_root: str | Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    tclsh = Path(cadence_root) / "tools" / "bin" / "tclsh.exe"
    if not tclsh.is_file():
        raise FileNotFoundError(f"Cadence Tcl runtime not found: {tclsh}")
    return subprocess.run(
        [str(tclsh), str(Path(script_path).resolve())],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def write_olb_readback_tcl(
    olb_path: str | Path,
    script_path: str | Path,
    report_path: str | Path,
    *,
    cadence_root: str | Path,
) -> Path:
    dll = Path(cadence_root) / "tools" / "bin" / "orDb_Dll_Tcl64.dll"
    lines = [
        f"load {_tcl_braced(dll)} DboTclWriteBasic",
        "proc cs {value} { return [DboTclHelper_sMakeCString $value] }",
        "proc value {cstring} { return [DboTclHelper_sGetConstCharPtr $cstring] }",
        "set session [DboTclHelper_sCreateSession]",
        "catch {DboSession -this $session}",
        "set status [DboState]",
        f"set lib [$session GetLib [cs {_tcl_braced(Path(olb_path).resolve())}] $status]",
        f"set report [open {_tcl_braced(Path(report_path).resolve())} w]",
        "puts $report \"package\tsection\tpin_number\tpin_name\tpin_type\tpart_reference\tpart_value\treference_display\tvalue_display\tbody_width\tbody_height\tpin_x\tpin_y\"",
        "set packages [$lib NewPackagesIter $status]",
        "set package [$packages NextPackage $status]",
        "while {$package != \"NULL\"} {",
        "  set packageName [cs {}]",
        "  $package GetName $packageName",
        "  set parts [$package NewPartsIter $status]",
        "  set part [$parts NextPart $status]",
        "  set section 0",
        "  while {$part != \"NULL\"} {",
        "    set device [$package GetDevice $section $status]",
        "    set partReference [cs {}]",
        "    $part GetReference $partReference",
        "    set partValue [cs {}]",
        "    $part GetPartValue $partValue",
        "    set referenceDisplay [$part GetDisplayProp [cs {Part Reference}] $status]",
        "    set valueDisplay [$part GetDisplayProp [cs {Value}] $status]",
        "    set hasReferenceDisplay [expr {$referenceDisplay != \"NULL\"}]",
        "    set hasValueDisplay [expr {$valueDisplay != \"NULL\"}]",
        "    set partBBox [$part GetBoundingBox]",
        "    set partTopLeft [DboTclHelper_sGetCRectTopLeft $partBBox]",
        "    set partBottomRight [DboTclHelper_sGetCRectBottomRight $partBBox]",
        "    set bodyWidth [expr {abs([DboTclHelper_sGetCPointX $partBottomRight] - [DboTclHelper_sGetCPointX $partTopLeft])}]",
        "    set bodyHeight [expr {abs([DboTclHelper_sGetCPointY $partBottomRight] - [DboTclHelper_sGetCPointY $partTopLeft])}]",
        "    set pins [$part NewLPinsIter $status]",
        "    set pin [$pins NextPin $status]",
        "    while {$pin != \"NULL\"} {",
        "      set pinName [cs {}]",
        "      $pin GetPinName $pinName",
        "      set pinPosition [$pin GetPinPosition $status]",
        "      set pinNumber [cs {}]",
        "      $device GetPinNumber $pinPosition $pinNumber",
        "      set pinType [$pin GetPinType $status]",
        "      set pinStart [$pin GetStartPoint $status]",
        "      set pinX [DboTclHelper_sGetCPointX $pinStart]",
        "      set pinY [DboTclHelper_sGetCPointY $pinStart]",
        "      puts $report \"[value $packageName]\t[expr {$section + 1}]\t[value $pinNumber]\t[value $pinName]\t$pinType\t[value $partReference]\t[value $partValue]\t$hasReferenceDisplay\t$hasValueDisplay\t$bodyWidth\t$bodyHeight\t$pinX\t$pinY\"",
        "      set pin [$pins NextPin $status]",
        "    }",
        "    catch {delete_DboLibPartPinsIter $pins}",
        "    set part [$parts NextPart $status]",
        "    incr section",
        "  }",
        "  catch {delete_DboPackagePartsIter $parts}",
        "  set package [$packages NextPackage $status]",
        "}",
        "catch {delete_DboLibPackagesIter $packages}",
        "close $report",
        "catch {$session RemoveLib $lib}",
        "catch {DboTclHelper_sDeleteSession $session}",
        "exit 0",
    ]
    path = Path(script_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_olb_report(report_path: str | Path) -> list[dict[str, str]]:
    with Path(report_path).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def verify_readback(spec: OrcadSymbolSpec, actual_rows: Iterable[dict[str, str]]) -> list[str]:
    actual_rows = list(actual_rows)
    populated = [letter for letter in _SECTION_IDS if any(pin.section == letter for pin in spec.pins)]
    section_number = {letter: str(index + 1) for index, letter in enumerate(populated)}
    expected = {(pin.number, pin.name, section_number[pin.section]) for pin in spec.pins}
    actual = {(row["pin_number"], row["pin_name"], row["section"]) for row in actual_rows}
    errors: list[str] = []
    if len(actual) != len(actual_rows):
        errors.append("duplicate native pin records")
    expected_types = {(p.number, section_number[p.section]): str(_PIN_ENUM.get(p.electrical_type, 4)) for p in spec.pins}
    for row in actual_rows:
        key = (row.get("pin_number"), row.get("section"))
        if key in expected_types and row.get("pin_type") != expected_types[key]:
            errors.append(f"pin {key} electrical type expected {expected_types[key]}, got {row.get('pin_type')}")
    for missing in sorted(expected - actual):
        errors.append(f"missing pin {missing[0]} {missing[1]} section {missing[2]}")
    for extra in sorted(actual - expected):
        errors.append(f"unexpected pin {extra[0]} {extra[1]} section {extra[2]}")
    for section in sorted({row.get("section", "") for row in actual_rows}):
        section_rows = [row for row in actual_rows if row.get("section") == section]
        if not section_rows:
            continue
        row = section_rows[0]
        # Check actual opposing baselines, not only planned source ratios.
        for i, first in enumerate(section_rows):
            if not all(first.get(k) for k in ("pin_x", "pin_y", "body_width")):
                continue
            width = float(first["body_width"])
            if _visible_text_width(first["pin_name"]) + 12 > width:
                errors.append(f"section {section} pin {first['pin_number']} text exceeds body")
            for second in section_rows[:i]:
                if not all(second.get(k) for k in ("pin_x", "pin_y")):
                    continue
                opposite = (float(first["pin_x"]) <= width / 2) != (float(second["pin_x"]) <= width / 2)
                if opposite and abs(float(first["pin_y"]) - float(second["pin_y"])) < 12:
                    if _visible_text_width(first["pin_name"]) + _visible_text_width(second["pin_name"]) + 24 > width:
                        errors.append(f"section {section} opposing text collision {first['pin_number']}/{second['pin_number']}")
        if all(p.get("pin_x") is not None and p.get("pin_y") is not None for p in section_rows):
            for side_x in {p["pin_x"] for p in section_rows}:
                side_rows = sorted((p for p in section_rows if p["pin_x"] == side_x), key=lambda p: float(p["pin_y"]))
                for first, second in zip(side_rows, side_rows[1:]):
                    if float(second["pin_y"]) - float(first["pin_y"]) < _MIN_CAPTURE_PIN_SPACING:
                        errors.append(f"section {section} pin spacing too small: {first['pin_number']}/{second['pin_number']}")
        if "part_reference" in row and row.get("part_reference") != "U":
            errors.append(f"section {section} Part Reference expected U, got {row.get('part_reference', '')}")
        if "part_value" in row and row.get("part_value") != spec.part_name:
            errors.append(f"section {section} Value expected {spec.part_name}, got {row.get('part_value', '')}")
        if "reference_display" in row and row.get("reference_display") != "1":
            errors.append(f"section {section} visible U? reference is missing")
        if "value_display" in row and row.get("value_display") != "1":
            errors.append(f"section {section} visible IC Value is missing")
        letter = populated[int(section) - 1] if section.isdigit() and 0 < int(section) <= len(populated) else ""
        expected_ratio = spec.part_aspect_ratios.get(letter, 0.0)
        if letter and row.get("body_width") and row.get("body_height"):
            section_pins = [pin for pin in spec.pins if pin.section == letter]
            minimum_width, minimum_height = _symbol_body_dimensions(
                section_pins,
                part_name=spec.part_name,
                aspect_ratio=expected_ratio,
                source_size=spec.part_source_sizes.get(letter),
            )
            if float(row["body_width"]) < minimum_width:
                errors.append(
                    f"section {section} body width {row['body_width']} cannot contain visible pin text; expected at least {minimum_width}"
                )
            if float(row["body_height"]) < minimum_height:
                errors.append(
                    f"section {section} body height {row['body_height']} cannot preserve readable pin spacing; expected at least {minimum_height}"
                )
        if expected_ratio and row.get("body_width") and row.get("body_height"):
            actual_ratio = float(row["body_height"]) / max(1.0, float(row["body_width"]))
            if abs(actual_ratio - expected_ratio) / expected_ratio > 0.03:
                errors.append(
                    f"section {section} PDF aspect ratio expected {expected_ratio:.3f}, got {actual_ratio:.3f}"
                )
        if expected_ratio and row.get("pin_x") and row.get("body_width"):
            expected_by_number = {pin.number: pin for pin in spec.pins if pin.section == letter}
            occupied: dict[tuple[str, str], list[str]] = {}
            for actual_pin in section_rows:
                expected_pin = expected_by_number.get(actual_pin.get("pin_number", ""))
                if expected_pin is None or expected_pin.symbol_side not in {"left", "right"}:
                    continue
                occupied.setdefault(
                    (actual_pin.get("pin_x", ""), actual_pin.get("pin_y", "")),
                    [],
                ).append(expected_pin.number)
                actual_side = "left" if float(actual_pin["pin_x"]) <= float(actual_pin["body_width"]) / 2 else "right"
                if actual_side != expected_pin.symbol_side:
                    errors.append(
                        f"section {section} pin {expected_pin.number} side expected {expected_pin.symbol_side}, got {actual_side}"
                    )
            for position, pin_numbers in occupied.items():
                if len(pin_numbers) > 1:
                    errors.append(
                        f"section {section} overlapping pins {','.join(sorted(pin_numbers))} at {position[0]},{position[1]}"
                    )
    return errors
