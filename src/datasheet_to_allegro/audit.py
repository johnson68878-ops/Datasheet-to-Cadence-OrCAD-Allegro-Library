from __future__ import annotations

from dataclasses import asdict, dataclass

from .geometry import FootprintGeometry
from .models import FootprintSpec, Measurement


ARRAY_FAMILIES = {"BGA", "WLCSP", "LGA"}


def measurement_value(item: Measurement | None) -> float | None:
    if item is None:
        return None
    if item.value_mm is not None:
        return item.value_mm
    if item.nominal_mm is not None:
        return item.nominal_mm
    if item.min_mm is not None and item.max_mm is not None:
        return (item.min_mm + item.max_mm) / 2
    return item.min_mm if item.min_mm is not None else item.max_mm


def measurement_label(symbol: str, item: Measurement | None, qualifier: str = "") -> str | None:
    if item is None:
        return None
    suffix = f" {qualifier}" if qualifier else ""
    if item.min_mm is not None and item.nominal_mm is not None and item.max_mm is not None:
        return f"{symbol}={item.min_mm:g}/{item.nominal_mm:g}/{item.max_mm:g} mm MIN/NOM/MAX{suffix}"
    if item.min_mm is not None and item.max_mm is not None:
        return f"{symbol}={item.min_mm:g}..{item.max_mm:g} mm{suffix}"
    value = measurement_value(item)
    return f"{symbol}={value:g} mm{suffix}" if value is not None else None


@dataclass(frozen=True)
class GeometryAuditFinding:
    code: str
    status: str
    expected: object
    actual: object
    message: str


def audit_geometry(spec: FootprintSpec, geometry: FootprintGeometry, *, gate_mode: str = 'release') -> list[GeometryAuditFinding]:
    if gate_mode not in {'release', 'review'}:
        raise ValueError('Unknown geometry audit mode')
    findings: list[GeometryAuditFinding] = []
    peripheral = [pad for pad in geometry.pads if pad.role == "peripheral"]
    actual_numbers = [pad.number for pad in peripheral]
    findings.append(GeometryAuditFinding(
        "PAD_COUNT", "pass" if len(peripheral) == spec.pin_count else "fail",
        spec.pin_count, len(peripheral), "实际铜盘数量必须与Datasheet Pin/Ball数量一致。",
    ))
    findings.append(GeometryAuditFinding(
        "PAD_NUMBER_UNIQUE", "pass" if len(actual_numbers) == len(set(actual_numbers)) else "fail",
        "全部唯一", len(set(actual_numbers)), "Pad编号不得重复。",
    ))
    land = spec.recommended_land_pattern.value if isinstance(spec.recommended_land_pattern.value, dict) else {}
    expected_w = float(land.get("pad_width_mm", 0) or 0)
    expected_l = float(land.get("pad_length_mm", 0) or 0)
    size_ok = bool(peripheral and expected_w and expected_l) and all(
        abs(pad.width_mm - expected_w) <= 1e-6 and abs(pad.length_mm - expected_l) <= 1e-6
        for pad in peripheral
    )
    findings.append(GeometryAuditFinding(
        "LAND_SIZE", "pass" if size_ok else "fail", [expected_w, expected_l],
        [peripheral[0].width_mm, peripheral[0].length_mm] if peripheral else None,
        "实际铜盘尺寸必须与footprint_spec中的Land Pattern一致。",
    ))
    expected_body = [measurement_value(spec.body_width), measurement_value(spec.body_length)]
    body_ok = all(value is not None for value in expected_body) and (
        abs(geometry.body_width_mm - float(expected_body[0])) <= 1e-6
        and abs(geometry.body_length_mm - float(expected_body[1])) <= 1e-6
    )
    missing_body_review = (
        gate_mode == 'review' and spec.package_type.upper() in ARRAY_FAMILIES
        and geometry.assembly_outline_kind == 'ball_land_envelope_review'
        and (spec.body_width is None or spec.body_length is None)
        and all(source is None or (expected is not None and abs(actual - expected) <= 1e-6)
                for source, expected, actual in zip(
                    (spec.body_width, spec.body_length), expected_body,
                    (geometry.body_width_mm, geometry.body_length_mm)))
    )
    findings.append(GeometryAuditFinding(
        "BODY_SIZE", "pass" if body_ok else 'warning' if missing_body_review else "fail", expected_body,
        [geometry.body_width_mm, geometry.body_length_mm],
        ('草稿省略未证实的本体外框；焊盘外包范围只用于暂存边界，不是Datasheet本体尺寸。'
         if missing_body_review else "Assembly本体外框必须与Datasheet D/E标称值一致。"),
    ))
    expected_pitch = measurement_value(spec.pitch)
    pitch_candidates: list[float] = []
    if expected_pitch:
        for first_index, first in enumerate(peripheral):
            for second in peripheral[first_index + 1:]:
                if abs(first.x_mm - second.x_mm) <= 1e-6:
                    delta = abs(first.y_mm - second.y_mm)
                elif abs(first.y_mm - second.y_mm) <= 1e-6:
                    delta = abs(first.x_mm - second.x_mm)
                else:
                    continue
                if delta > 1e-6:
                    pitch_candidates.append(delta)
    actual_pitch = min(pitch_candidates) if pitch_candidates else None
    if expected_pitch is not None:
        findings.append(GeometryAuditFinding(
            "PAD_PITCH", "pass" if actual_pitch is not None and abs(actual_pitch - expected_pitch) <= 1e-6 else "fail",
            expected_pitch, actual_pitch, "相邻同行/同列Pad中心距必须与Datasheet Pitch一致。",
        ))
    expected_ep = [measurement_value(spec.exposed_pad_width), measurement_value(spec.exposed_pad_length)]
    if all(value is not None for value in expected_ep):
        exposed = [pad for pad in geometry.pads if pad.role == "exposed_pad"]
        ep_ok = len(exposed) == 1 and (
            abs(exposed[0].width_mm - float(expected_ep[0])) <= 1e-6
            and abs(exposed[0].length_mm - float(expected_ep[1])) <= 1e-6
        )
        findings.append(GeometryAuditFinding(
            "EXPOSED_PAD", "pass" if ep_ok else "fail", expected_ep,
            [exposed[0].width_mm, exposed[0].length_mm] if exposed else None,
            "中心Exposed Pad必须存在且与Datasheet D2/E2标称值一致。",
        ))
    mask_value = spec.solder_mask_expansion.value if isinstance(spec.solder_mask_expansion.value, dict) else {}
    expected_mask_expansion = float(mask_value.get("expansion_mm", geometry.solder_mask_expansion_mm))
    findings.append(GeometryAuditFinding(
        "SOLDER_MASK_EXPANSION",
        "pass" if abs(geometry.solder_mask_expansion_mm - expected_mask_expansion) <= 1e-6 else "fail",
        expected_mask_expansion, geometry.solder_mask_expansion_mm,
        "阻焊开窗必须按已绑定制造规则在铜盘两轴分别扩展。",
    ))
    findings.append(GeometryAuditFinding(
        "PIN1_SILKSCREEN_MARKER", "pass" if geometry.pin1_marker is not None else "fail",
        "存在", "存在" if geometry.pin1_marker is not None else None,
        "Pin 1丝印圆点必须由GeometryIR生成并避让铜盘。",
    ))
    if spec.package_type.upper() in ARRAY_FAMILIES:
        expected = {
            f"{row}{column + 1}"
            for row in spec.row_names for column in range(spec.column_count or 0)
        } - {item.upper() for item in spec.missing_positions}
        findings.append(GeometryAuditFinding(
            "BALL_MAP", "pass" if set(actual_numbers) == expected else "fail",
            sorted(expected), sorted(actual_numbers), "缺球映射、行名和列号必须逐点一致。",
        ))
    if spec.pin_1_position:
        expected_pin1 = "A1" if spec.package_type.upper() in ARRAY_FAMILIES else "1"
        findings.append(GeometryAuditFinding(
            "PIN1_PRESENT", "pass" if expected_pin1 in actual_numbers else "fail",
            expected_pin1, expected_pin1 if expected_pin1 in actual_numbers else None,
            "Pin 1/A1必须存在于实际几何。",
        ))
    return findings


def dimension_annotations(spec: FootprintSpec, geometry: FootprintGeometry) -> list[str]:
    family = spec.package_type.upper()
    labels: list[str] = []
    if spec.body_width and (label := measurement_label("D", spec.body_width)):
        labels.append(label)
    if spec.body_length and (label := measurement_label("E", spec.body_length)):
        labels.append(label)
    if spec.package_height and (label := measurement_label("A", spec.package_height)):
        labels.append(label)
    if family in ARRAY_FAMILIES and not spec.body_width and not spec.body_length:
        labels.append("D/E=UNRESOLVED - NO PACKAGE BODY OUTLINE DRAWN")
    pitch_x = spec.pitch_x or spec.pitch
    pitch_y = spec.pitch_y or spec.pitch
    if family in ARRAY_FAMILIES:
        if label := measurement_label("eX", pitch_x, "BSC"):
            labels.append(label)
        if label := measurement_label("eY", pitch_y, "BSC"):
            labels.append(label)
        px, py = measurement_value(pitch_x), measurement_value(pitch_y)
        if px and spec.column_count:
            labels.append(f"BALL GRID X={(spec.column_count - 1) * px:g} mm DERIVED FROM eX")
        if py and spec.row_names:
            labels.append(f"BALL GRID Y={(len(spec.row_names) - 1) * py:g} mm DERIVED FROM eY")
    elif label := measurement_label("e", spec.pitch, "BSC"):
        labels.append(label)
    if label := measurement_label("b", spec.terminal_width_min):
        labels.append(label)
    if family not in ARRAY_FAMILIES and (label := measurement_label("L", spec.terminal_length_min)):
        labels.append(label)
    if spec.exposed_pad_width and spec.exposed_pad_length:
        ep_w = measurement_label("EP-W", spec.exposed_pad_width)
        ep_l = measurement_label("EP-L", spec.exposed_pad_length)
        labels.extend(item for item in (ep_w, ep_l) if item)
    land = spec.recommended_land_pattern.value if isinstance(spec.recommended_land_pattern.value, dict) else {}
    if land.get("pad_width_mm") and land.get("pad_length_mm"):
        origin = {
            "manufacturer_recommended": "MFR",
            "reference_design": "MFR REFERENCE",
        }.get(spec.recommended_land_pattern.status, "CALCULATED PROFILE")
        page = spec.recommended_land_pattern.evidence[0].page if spec.recommended_land_pattern.evidence else None
        page_suffix = f" P{page or '?'}" if spec.recommended_land_pattern.status in {"manufacturer_recommended", "reference_design"} else ""
        labels.append(f"PCB LAND={float(land['pad_width_mm']):g}x{float(land['pad_length_mm']):g} mm {origin}{page_suffix}")
        if land.get("exposed_pad_width_mm") and land.get("exposed_pad_length_mm"):
            labels.append(
                f"EP LAND={float(land['exposed_pad_width_mm']):g}x"
                f"{float(land['exposed_pad_length_mm']):g} mm {origin}{page_suffix}"
            )
    mask_value = spec.solder_mask_expansion.value if isinstance(spec.solder_mask_expansion.value, dict) else {}
    mask_origin = "MFR" if spec.solder_mask_expansion.status == "manufacturer_recommended" else "STAGING PROFILE"
    mask_page = spec.solder_mask_expansion.evidence[0].page if spec.solder_mask_expansion.evidence else None
    mask_page_suffix = f" P{mask_page or '?'}" if spec.solder_mask_expansion.status == "manufacturer_recommended" else ""
    mask_style = str(mask_value.get("style", "")).upper()
    labels.append(f"SOLDER MASK={mask_style + ' ' if mask_style else ''}+{geometry.solder_mask_expansion_mm:g} mm/side {mask_origin}{mask_page_suffix}")
    paste_value = spec.paste_mask_rule.value if isinstance(spec.paste_mask_rule.value, dict) else {}
    if geometry.paste_width_mm and geometry.paste_length_mm:
        paste_origin = "MFR" if spec.paste_mask_rule.status == "manufacturer_recommended" else "STAGING PROFILE"
        paste_page = spec.paste_mask_rule.evidence[0].page if spec.paste_mask_rule.evidence else "?"
        radius = f" R{geometry.paste_corner_radius_mm:g}" if geometry.paste_corner_radius_mm else ""
        labels.append(f"PASTE={geometry.paste_width_mm:g}x{geometry.paste_length_mm:g}{radius} mm {paste_origin} P{paste_page}")
    labels.append(f"COURTYARD=+{geometry.courtyard_clearance_mm:g} mm STAGING PROFILE")
    return labels


def audit_as_dict(findings: list[GeometryAuditFinding]) -> list[dict[str, object]]:
    return [asdict(item) for item in findings]
