from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .models import FootprintSpec, Measurement


@dataclass(frozen=True)
class Pad:
    number: str
    x_mm: float
    y_mm: float
    width_mm: float
    length_mm: float
    shape: str = "RECTANGLE"
    drill_mm: float | None = None
    role: str = "peripheral"
    rotation_deg: float = 0.0


@dataclass(frozen=True)
class Pin1Marker:
    """Manufacturing silkscreen marker derived from the actual Pin 1 land.

    The marker is intentionally part of neutral GeometryIR.  EDA backends must
    not invent their own fixed offset because that can overlap copper on small
    packages such as 2 x 2 mm DFN.
    """

    x_mm: float
    y_mm: float
    radius_mm: float
    line_width_mm: float
    copper_clearance_mm: float
    reference_pin: str


@dataclass(frozen=True)
class FootprintGeometry:
    family: str
    body_width_mm: float
    body_length_mm: float
    pads: tuple[Pad, ...]
    courtyard_clearance_mm: float
    solder_mask_expansion_mm: float
    paste_scale: float
    paste_width_mm: float | None = None
    paste_length_mm: float | None = None
    paste_shape: str | None = None
    paste_corner_radius_mm: float | None = None
    assembly_outline_kind: str = "manufacturer_body"
    exposed_pad_paste_scale: float = 0.75
    exposed_pad_paste_area_coverage: float = 0.625
    exposed_pad_paste_rows: int = 2
    exposed_pad_paste_columns: int = 2
    exposed_pad_paste_gap_mm: float = 0.20
    pin1_marker: Pin1Marker | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _mm(value: Measurement | None) -> float | None:
    if value is None:
        return None
    if value.value_mm is not None:
        return value.value_mm
    if value.nominal_mm is not None:
        return value.nominal_mm
    if value.min_mm is not None and value.max_mm is not None:
        return (value.min_mm + value.max_mm) / 2
    return value.min_mm or value.max_mm


def _rule_number(spec: FootprintSpec, rule: str, key: str, fallback: float) -> float:
    value = getattr(spec, rule).value
    return float(value.get(key, fallback)) if isinstance(value, dict) else fallback


def _land_size(spec: FootprintSpec) -> tuple[float, float]:
    value = spec.recommended_land_pattern.value
    if isinstance(value, dict) and value.get("pad_width_mm") and value.get("pad_length_mm"):
        return float(value["pad_width_mm"]), float(value["pad_length_mm"])
    raise ValueError("Recommended Land Pattern未包含可验证的焊盘宽度和长度")


def _exposed_land_size(spec: FootprintSpec) -> tuple[float | None, float | None]:
    value = spec.recommended_land_pattern.value
    if isinstance(value, dict):
        land_w = value.get("exposed_pad_width_mm")
        land_l = value.get("exposed_pad_length_mm")
        if land_w and land_l:
            return float(land_w), float(land_l)
    return _mm(spec.exposed_pad_width), _mm(spec.exposed_pad_length)


def _array(spec: FootprintSpec, width: float, length: float, pad_w: float, pad_l: float) -> tuple[Pad, ...]:
    pitch_x, pitch_y = _mm(spec.pitch_x or spec.pitch), _mm(spec.pitch_y or spec.pitch)
    if not pitch_x or not pitch_y or not spec.row_names or not spec.column_count:
        raise ValueError("阵列封装缺少行列、Pitch或缺球映射")
    result = []
    missing = {item.upper() for item in spec.missing_positions}
    for row_index, row in enumerate(spec.row_names):
        for col in range(spec.column_count):
            number = f"{row}{col + 1}"
            if number.upper() in missing:
                continue
            result.append(Pad(
                number=number,
                x_mm=(col - (spec.column_count - 1) / 2) * pitch_x,
                y_mm=((len(spec.row_names) - 1) / 2 - row_index) * pitch_y,
                width_mm=pad_w,
                length_mm=pad_l,
                shape="CIRCLE" if abs(pad_w - pad_l) < 1e-6 else "RECTANGLE",
            ))
    if len(result) != spec.pin_count:
        raise ValueError("阵列行列数与Pin/Ball数量不一致；可能存在缺球，必须提供ball map")
    return tuple(result)


def _dual(spec: FootprintSpec, width: float, pad_w: float, pad_l: float) -> tuple[Pad, ...]:
    pitch = _mm(spec.pitch)
    if not pitch or not spec.pin_count or spec.pin_count % 2:
        raise ValueError("双边封装需要偶数Pin数量和Pitch")
    side = spec.pin_count // 2
    rule_value = spec.recommended_land_pattern.value if isinstance(spec.recommended_land_pattern.value, dict) else {}
    if "pad_center_x_mm" in rule_value:
        x = float(rule_value["pad_center_x_mm"])
    else:
        outset = _rule_number(spec, "recommended_land_pattern", "pad_center_outset_mm", pad_l / 2)
        x = width / 2 + outset
    result = []
    for index in range(side):
        y = (side - 1) * pitch / 2 - index * pitch
        result.append(Pad(str(index + 1), -x, y, pad_w, pad_l, rotation_deg=90.0))
        result.append(Pad(str(spec.pin_count - index), x, y, pad_w, pad_l, rotation_deg=90.0))
    ep_w, ep_l = _exposed_land_size(spec)
    if ep_w and ep_l and spec.exposed_pad_number:
        result.append(Pad(spec.exposed_pad_number, 0.0, 0.0, ep_w, ep_l, role="exposed_pad"))
    return tuple(sorted(result, key=lambda pad: int(pad.number)))


def _quad(spec: FootprintSpec, width: float, length: float, pad_w: float, pad_l: float) -> tuple[Pad, ...]:
    pitch = _mm(spec.pitch)
    if not pitch or not spec.pin_count:
        raise ValueError("四边封装需要Pin数量和Pitch有效")
    counts = spec.side_pin_counts
    if len(counts) != 4 or sum(counts) != spec.pin_count:
        raise ValueError("四边封装需要四边Pin数量映射且总数必须匹配")
    left, bottom, right, top = counts
    rule_value = spec.recommended_land_pattern.value if isinstance(spec.recommended_land_pattern.value, dict) else {}
    if "pad_center_x_mm" in rule_value and "pad_center_y_mm" in rule_value:
        x, y = float(rule_value["pad_center_x_mm"]), float(rule_value["pad_center_y_mm"])
        if not all(math.isfinite(v) and v > 0 for v in (x,y)):
            raise ValueError("Explicit pad center distances must be finite and positive")
    elif "pad_center_outset_mm" in rule_value:
        x = width / 2 + float(rule_value["pad_center_outset_mm"])
        y = length / 2 + float(rule_value["pad_center_outset_mm"])
    else:
        inset = _rule_number(spec, "recommended_land_pattern", "pad_center_inset_mm", -pad_l / 2)
        x = width / 2 - inset
        y = length / 2 - inset
    pads: list[Pad] = []
    for i in range(left):
        offset = (left - 1) * pitch / 2 - i * pitch
        pads.append(Pad(str(i + 1), -x, offset, pad_w, pad_l, rotation_deg=90.0))
    for i in range(bottom):
        offset = (bottom - 1) * pitch / 2 - i * pitch
        pads.append(Pad(str(left + i + 1), -offset, -y, pad_w, pad_l, rotation_deg=0.0))
    for i in range(right):
        offset = (right - 1) * pitch / 2 - i * pitch
        pads.append(Pad(str(left + bottom + i + 1), x, -offset, pad_w, pad_l, rotation_deg=90.0))
    for i in range(top):
        offset = (top - 1) * pitch / 2 - i * pitch
        pads.append(Pad(str(left + bottom + right + i + 1), offset, y, pad_w, pad_l, rotation_deg=0.0))
    ep_w, ep_l = _exposed_land_size(spec)
    if ep_w and ep_l and spec.exposed_pad_number:
        pads.append(Pad(spec.exposed_pad_number, 0.0, 0.0, ep_w, ep_l, role="exposed_pad"))
    return tuple(sorted(pads, key=lambda pad: int(pad.number)))


def _tht(spec: FootprintSpec, pad_w: float, pad_l: float) -> tuple[Pad, ...]:
    pitch_x = _mm(spec.pitch_x or spec.pitch)
    pitch_y = _mm(spec.pitch_y or spec.pitch)
    if not pitch_x or not spec.pin_count:
        raise ValueError("THT/连接器缺少Pin数量或Pitch")
    drill = _rule_number(spec, "recommended_land_pattern", "drill_mm", 0.0)
    rows, cols = spec.grid_rows, spec.grid_columns
    if not rows or not cols or rows * cols != spec.pin_count or drill <= 0:
        raise ValueError("通孔几何需要明确的行列、Pin数和钻孔尺寸，不能推测")
    pitch_y = pitch_y or pitch_x
    pads: list[Pad] = []
    for index in range(spec.pin_count):
        row, col = divmod(index, cols)
        pads.append(Pad(
            str(index + 1),
            (col - (cols - 1) / 2) * pitch_x,
            ((rows - 1) / 2 - row) * pitch_y,
            pad_w,
            pad_l,
            "CIRCLE",
            drill,
        ))
    return tuple(pads)


def _pad_half_extents(pad: Pad) -> tuple[float, float]:
    if pad.shape == "CIRCLE" or pad.role == "exposed_pad":
        return pad.width_mm / 2, pad.length_mm / 2
    if int(pad.rotation_deg) % 180 == 90:
        return pad.length_mm / 2, pad.width_mm / 2
    return pad.width_mm / 2, pad.length_mm / 2


def _circle_to_pad_clearance(x: float, y: float, radius: float, pad: Pad) -> float:
    half_x, half_y = _pad_half_extents(pad)
    dx = max(abs(x - pad.x_mm) - half_x, 0.0)
    dy = max(abs(y - pad.y_mm) - half_y, 0.0)
    return math.hypot(dx, dy) - radius


def _pin1_marker(pads: tuple[Pad, ...], courtyard_clearance_mm: float) -> Pin1Marker:
    """Place a visible Pin 1 dot outside copper without package-size constants."""

    pin = next((item for item in pads if item.number.upper() == "A1"), None)
    pin = pin or next((item for item in pads if item.number == "1"), pads[0])
    half_x, half_y = _pad_half_extents(pin)
    sign_x = -1.0 if pin.x_mm <= 0 else 1.0
    sign_y = 1.0 if pin.y_mm >= 0 else -1.0
    radius = min(0.12, max(0.07, min(half_x * 2, half_y * 2) * 0.22))
    line_width = 0.03
    required_clearance = max(0.06, min(0.10, courtyard_clearance_mm * 0.32))
    diagonal = (required_clearance + radius + line_width / 2) / math.sqrt(2)
    edge_x = pin.x_mm + sign_x * half_x
    edge_y = pin.y_mm + sign_y * half_y
    x = edge_x + sign_x * diagonal
    y = edge_y + sign_y * diagonal

    # Move farther along the same outward diagonal if another land is closer.
    # This is topology-driven and applies equally to arrays, perimeter SMD and
    # through-hole packages.
    step = 0.02
    for _ in range(100):
        if all(_circle_to_pad_clearance(x, y, radius + line_width / 2, item) >= required_clearance - 1e-9 for item in pads):
            break
        x += sign_x * step
        y += sign_y * step
    return Pin1Marker(
        x_mm=x,
        y_mm=y,
        radius_mm=radius,
        line_width_mm=line_width,
        copper_clearance_mm=required_clearance,
        reference_pin=pin.number,
    )


def build_geometry(spec: FootprintSpec) -> FootprintGeometry:
    width, length = _mm(spec.body_width), _mm(spec.body_length)
    pad_w, pad_l = _land_size(spec)
    family = spec.package_type.upper()
    has_manufacturer_body = bool(width and length)
    explicit = (spec.recommended_land_pattern.value or {}).get("pads")
    if explicit is not None:
        if not width or not length:
            raise ValueError("Explicit lands require verified body dimensions")
        pads = tuple(Pad(**item) for item in explicit)
        if len(pads) != spec.pin_count or len({p.number for p in pads}) != len(pads):
            raise ValueError("Explicit pad count/number mismatch")
        if len({(p.x_mm, p.y_mm) for p in pads}) != len(pads):
            raise ValueError("Duplicate explicit pad coordinates")
        for pad in pads:
            if not all(math.isfinite(v) for v in (pad.x_mm, pad.y_mm, pad.width_mm, pad.length_mm, pad.rotation_deg)) or min(pad.width_mm, pad.length_mm) <= 0:
                raise ValueError("Invalid explicit pad dimensions")
        for pad in pads:
            if pad.role not in {"peripheral", "exposed_pad"} or pad.shape not in {"RECTANGLE", "CIRCLE"}:
                raise ValueError("Unsupported explicit pad role or shape")
            if pad.shape == "CIRCLE" and abs(pad.width_mm - pad.length_mm) > 1e-9:
                raise ValueError("Circular pad requires equal width and length")
            if abs(pad.rotation_deg / 90 - round(pad.rotation_deg / 90)) > 1e-9:
                raise ValueError("Explicit pad rotation requires an orthogonal verified orientation")
            if pad.drill_mm is not None and (not math.isfinite(pad.drill_mm) or not 0 < pad.drill_mm < min(pad.width_mm, pad.length_mm)):
                raise ValueError("Invalid explicit pad drill")
    elif family in {"BGA", "WLCSP", "LGA"}:
        pads = _array(spec, width or 0.0, length or 0.0, pad_w, pad_l)
        has_manufacturer_body = bool(width and length)
        width = width or max(abs(pad.x_mm) + pad.width_mm / 2 for pad in pads) * 2
        length = length or max(abs(pad.y_mm) + pad.length_mm / 2 for pad in pads) * 2
    elif family in {"QFN", "QFP", "LCC", "PLCC"}:
        if not width or not length:
            raise ValueError("缺少已验证的本体宽度或长度")
        pads = _quad(spec, width, length, pad_w, pad_l)
    elif family in {"DFN", "SON", "SOIC", "SOP", "SSOP", "TSSOP", "MSOP", "SOT", "SOJ"}:
        if not width or not length:
            raise ValueError("缺少已验证的本体宽度或长度")
        pads = _dual(spec, width, pad_w, pad_l)
    elif family in {"THT", "CONNECTOR", "DIP", "PGA", "SIP"}:
        if not width or not length:
            raise ValueError("缺少已验证的本体宽度或长度")
        pads = _tht(spec, pad_w, pad_l)
    else:
        raise ValueError(f"初版尚无{family}几何后端")
    courtyard_clearance = _rule_number(spec, "courtyard", "clearance_mm", 0.25)
    return FootprintGeometry(
        family=family,
        body_width_mm=width,
        body_length_mm=length,
        pads=pads,
        courtyard_clearance_mm=courtyard_clearance,
        solder_mask_expansion_mm=_rule_number(spec, "solder_mask_expansion", "expansion_mm", 0.05),
        paste_scale=_rule_number(spec, "paste_mask_rule", "scale", 1.0),
        paste_width_mm=_rule_number(spec, "paste_mask_rule", "aperture_width_mm", 0.0) or None,
        paste_length_mm=_rule_number(spec, "paste_mask_rule", "aperture_length_mm", 0.0) or None,
        paste_shape=(spec.paste_mask_rule.value or {}).get("shape") if isinstance(spec.paste_mask_rule.value, dict) else None,
        paste_corner_radius_mm=_rule_number(spec, "paste_mask_rule", "corner_radius_mm", 0.0) or None,
        assembly_outline_kind=("manufacturer_body" if family not in {"BGA", "WLCSP", "LGA"} or has_manufacturer_body else "ball_land_envelope_review"),
        exposed_pad_paste_scale=_rule_number(spec, "paste_mask_rule", "exposed_pad_linear_scale", 0.75),
        exposed_pad_paste_area_coverage=_rule_number(spec, "paste_mask_rule", "exposed_pad_area_coverage", 0.625),
        exposed_pad_paste_rows=int(_rule_number(spec, "paste_mask_rule", "exposed_pad_window_rows", 2)),
        exposed_pad_paste_columns=int(_rule_number(spec, "paste_mask_rule", "exposed_pad_window_columns", 2)),
        exposed_pad_paste_gap_mm=_rule_number(spec, "paste_mask_rule", "exposed_pad_window_gap_mm", 0.20),
        pin1_marker=_pin1_marker(pads, courtyard_clearance),
    )
