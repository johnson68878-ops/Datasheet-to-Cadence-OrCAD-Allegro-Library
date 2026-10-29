from __future__ import annotations

import html
import json
from pathlib import Path

from .models import FootprintSpec, Measurement
from .geometry import FootprintGeometry, build_geometry
from .engineering_ir import build_dimension_ir
from .naming import cadence_safe_component


def _mm(measurement: Measurement | None) -> float | None:
    if measurement is None:
        return None
    return measurement.value_mm if measurement.value_mm is not None else measurement.nominal_mm


def render_svg(spec: FootprintSpec, target: Path) -> None:
    try:
        render_geometry_svg(spec, build_geometry(spec), target)
        return
    except ValueError:
        pass
    width, length = _mm(spec.body_width), _mm(spec.body_length)
    px, py = _mm(spec.pitch_x or spec.pitch), _mm(spec.pitch_y or spec.pitch)
    if None in (width, length, px, py) or not spec.row_names or not spec.column_count:
        raise ValueError("Not enough verified geometry to render preview")
    scale, margin = 100.0, 55.0
    canvas_w, canvas_h = width * scale + margin * 2, length * scale + margin * 2
    cx, cy = canvas_w / 2, canvas_h / 2
    terminal = spec.terminal_width_min
    dia = ((terminal.min_mm or terminal.value_mm or 0.2) + (terminal.max_mm or terminal.value_mm or 0.2)) / 2
    start_x = cx - (spec.column_count - 1) * px * scale / 2
    start_y = cy - (len(spec.row_names) - 1) * py * scale / 2
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_w:.0f}" height="{canvas_h:.0f}" viewBox="0 0 {canvas_w:.2f} {canvas_h:.2f}">',
             '<rect width="100%" height="100%" fill="#101820"/>',
             f'<rect x="{cx-width*scale/2:.2f}" y="{cy-length*scale/2:.2f}" width="{width*scale:.2f}" height="{length*scale:.2f}" fill="none" stroke="#55d6be" stroke-width="2"/>']
    for r, row in enumerate(spec.row_names):
        for c in range(spec.column_count):
            x, y = start_x + c * px * scale, start_y + r * py * scale
            pin = f"{row}{c+1}"
            cls = "#ffcc66" if pin == "A1" else "#70a1ff"
            parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{dia*scale/2:.2f}" fill="none" stroke="{cls}" stroke-width="1.4"><title>{html.escape(pin)}</title></circle>')
    parts += [f'<text x="12" y="24" fill="#ffffff" font-family="Arial" font-size="15">{html.escape(spec.part_number)} — {html.escape(spec.package_type)} (component/top view)</text>',
              f'<text x="12" y="{canvas_h-12:.2f}" fill="#ff8f8f" font-family="Arial" font-size="12">DRAFT: component terminals only; PCB land pattern unresolved</text>', '</svg>']
    target.write_text("\n".join(parts), encoding="utf-8")


def render_geometry_svg(spec: FootprintSpec, geometry: FootprintGeometry, target: Path) -> None:
    scale, margin = 70.0, 85.0
    pad_span_x = max((abs(pad.x_mm) + (pad.length_mm if int(pad.rotation_deg) % 180 == 90 else pad.width_mm) / 2 for pad in geometry.pads), default=geometry.body_width_mm / 2)
    pad_span_y = max((abs(pad.y_mm) + (pad.width_mm if int(pad.rotation_deg) % 180 == 90 else pad.length_mm) / 2 for pad in geometry.pads), default=geometry.body_length_mm / 2)
    half_w = max(geometry.body_width_mm / 2, pad_span_x) + geometry.courtyard_clearance_mm
    half_h = max(geometry.body_length_mm / 2, pad_span_y) + geometry.courtyard_clearance_mm
    canvas_w, canvas_h = half_w * 2 * scale + margin * 2, half_h * 2 * scale + margin * 2
    cx, cy = canvas_w / 2, canvas_h / 2
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_w:.0f}" height="{canvas_h:.0f}" viewBox="0 0 {canvas_w:.2f} {canvas_h:.2f}">',
        '<rect width="100%" height="100%" fill="#08111d"/>',
        f'<rect x="{cx-half_w*scale:.2f}" y="{cy-half_h*scale:.2f}" width="{half_w*2*scale:.2f}" height="{half_h*2*scale:.2f}" fill="none" stroke="#566b7f" stroke-dasharray="5 4"/>',
    ]
    if geometry.assembly_outline_kind == "manufacturer_body":
        parts.append(
            f'<rect x="{cx-geometry.body_width_mm*scale/2:.2f}" y="{cy-geometry.body_length_mm*scale/2:.2f}" width="{geometry.body_width_mm*scale:.2f}" height="{geometry.body_length_mm*scale:.2f}" fill="#111f2e" stroke="#6c8299"/>'
        )
    for pad in geometry.pads:
        x, y = cx + pad.x_mm * scale, cy - pad.y_mm * scale
        # Copper remains the same colour for every pin. Pin 1 is indicated by
        # the same external silkscreen marker used in the Allegro database;
        # changing the copper colour was a UI-only convention that confused
        # Layout comparison screenshots.
        color = "#3ca9ef"
        if pad.shape == "CIRCLE":
            parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{pad.width_mm*scale/2:.2f}" fill="{color}" stroke="#a9dcff"><title>Pin {html.escape(pad.number)}</title></circle>')
        else:
            rotated = int(pad.rotation_deg) % 180 == 90
            w = (pad.length_mm if rotated else pad.width_mm) * scale
            h = (pad.width_mm if rotated else pad.length_mm) * scale
            parts.append(f'<rect x="{x-w/2:.2f}" y="{y-h/2:.2f}" width="{w:.2f}" height="{h:.2f}" fill="{color}" stroke="#a9dcff"><title>Pin {html.escape(pad.number)}</title></rect>')
    if geometry.pin1_marker:
        marker = geometry.pin1_marker
        marker_x = cx + marker.x_mm * scale
        marker_y = cy - marker.y_mm * scale
        parts.append(
            f'<circle cx="{marker_x:.2f}" cy="{marker_y:.2f}" r="{marker.radius_mm*scale:.2f}" '
            f'fill="#f2aa3c" stroke="#fff1c7" stroke-width="{max(1.2, marker.line_width_mm*scale):.2f}">'
            f'<title>Pin 1 silkscreen marker; copper clearance {marker.copper_clearance_mm:.3f} mm</title></circle>'
        )
    parts += [
        f'<text x="16" y="27" fill="#ffffff" font-family="Arial" font-size="15">{html.escape(spec.part_number)} — {html.escape(geometry.family)} · Top View</text>',
        f'<text x="16" y="{canvas_h-14:.2f}" fill="#9fb0c4" font-family="Arial" font-size="12">Pin 1 external marker · {len(geometry.pads)} pads · unit mm' + (' · body D/E unresolved' if geometry.assembly_outline_kind != 'manufacturer_body' else '') + '</text>',
        '</svg>',
    ]
    target.write_text("\n".join(parts), encoding="utf-8")


def write_dimensions(spec: FootprintSpec, target: Path) -> None:
    rows = ["| Field | Normalized mm | Original | Evidence |", "|---|---:|---|---|"]
    for name in (
        "pitch", "body_width", "body_length", "package_height", "terminal_width_min",
        "terminal_length_min", "exposed_pad_width", "exposed_pad_length",
    ):
        value = getattr(spec, name)
        if value is None:
            rows.append(f"| {name} | unresolved | — | — |")
            continue
        normalized = value.value_mm if value.value_mm is not None else f"{value.min_mm}..{value.max_mm}"
        ev = value.evidence[0]
        rows.append(f"| {name} | {normalized} | {value.original_value} {value.original_unit} | p.{ev.page}, {ev.figure_or_table}; `{ev.raw_text}` |")
    rows += ["", "## Blocking unresolved items", ""] + [f"- `{x.code}`: {x.message} {x.required_action}" for x in spec.unresolved_items]
    target.write_text("\n".join(rows) + "\n", encoding="utf-8")


def write_datasheet_comparison(spec: FootprintSpec, geometry: FootprintGeometry, target: Path) -> None:
    """Compare datasheet evidence with the planned neutral geometry.

    This report is intentionally not labelled as an Allegro-actual comparison:
    the independent fresh-session database extraction contract is a separate
    verification stage and must not be simulated by reusing GeometryIR.
    """
    land = spec.recommended_land_pattern.value if isinstance(spec.recommended_land_pattern.value, dict) else {}
    term_w, term_l = _mm(spec.terminal_width_min), _mm(spec.terminal_length_min)
    ep_w, ep_l = _mm(spec.exposed_pad_width), _mm(spec.exposed_pad_length)
    body_w, body_l, body_h, pitch = _mm(spec.body_width), _mm(spec.body_length), _mm(spec.package_height), _mm(spec.pitch)
    evidence = spec.source_evidence.get("package_dimensions") or []
    page = evidence[0] if evidence else None
    page_label = f"p.{page.page} - {page.figure_or_table}" if page else "unresolved"
    if body_w and body_l:
        outline_conclusion = "Assembly计划几何采用Datasheet本体D/E；PCB铜盘按标明来源的Land Pattern生成。"
    else:
        outline_conclusion = "Datasheet未给出可用本体D/E；计划几何不创建制造商本体闭合外框，只生成有证据的铜盘阵列和评审尺寸。"
    rows = [
        "# Datasheet与Footprint生成计划逐项比对",
        "",
        f"- 元件: `{spec.part_number}`",
        f"- Datasheet证据: {page_label}",
        "- 视图约定: Component / Top View; Pin编号逆时针",
        f"- 结论: {outline_conclusion}",
        "- 验证范围: 本表比较Datasheet证据与GeometryIR计划值；Allegro实际数据库必须在新会话回读后另行比较。",
        "",
        "| 对象 | Datasheet标称值 | GeometryIR计划值 | 差异 | 来源/判定 |",
        "|---|---:|---:|---:|---|",
        f"| 本体D | {body_w:.3f} mm | {geometry.body_width_mm:.3f} mm | {geometry.body_width_mm-body_w:+.3f} mm | Datasheet一致 |" if body_w else "| 本体D | unresolved | - | - | 阻塞 |",
        f"| 本体E | {body_l:.3f} mm | {geometry.body_length_mm:.3f} mm | {geometry.body_length_mm-body_l:+.3f} mm | Datasheet一致 |" if body_l else "| 本体E | unresolved | - | - | 阻塞 |",
        f"| 本体高度A | {body_h:.3f} mm | Dimension层标注 {body_h:.3f} mm | +0.000 mm | Datasheet标称高度 |" if body_h else "| 本体高度A | unresolved | - | - | 待确认 |",
        f"| Pitch e | {pitch:.3f} mm | {pitch:.3f} mm | +0.000 mm | Datasheet BSC |" if pitch else "| Pitch e | unresolved | - | - | 阻塞 |",
        f"| 元件端子b | {term_w:.3f} mm | Assembly {term_w:.3f} mm | +0.000 mm | Datasheet标称端子 |" if term_w else "| 元件端子b | unresolved | - | - | 阻塞 |",
        f"| 元件端子L | {term_l:.3f} mm | Assembly {term_l:.3f} mm | +0.000 mm | Datasheet标称端子 |" if term_l else "| 元件端子L | unresolved | - | - | 阻塞 |",
        f"| EP D2 | {ep_w:.3f} mm | {ep_w:.3f} mm | +0.000 mm | Datasheet标称EP |" if ep_w else "| EP D2 | unresolved | - | - | 阻塞 |",
        f"| EP E2 | {ep_l:.3f} mm | {ep_l:.3f} mm | +0.000 mm | Datasheet标称EP |" if ep_l else "| EP E2 | unresolved | - | - | 阻塞 |",
        f"| PCB铜盘宽 | {term_w:.3f} mm端子 | {float(land.get('pad_width_mm', 0)):.3f} mm | {float(land.get('pad_width_mm', 0))-term_w:+.3f} mm | {land.get('origin', 'unresolved')} |" if term_w and land.get("pad_width_mm") else "| PCB铜盘宽 | - | unresolved | - | 阻塞 |",
        f"| PCB铜盘长 | {term_l:.3f} mm端子 | {float(land.get('pad_length_mm', 0)):.3f} mm | {float(land.get('pad_length_mm', 0))-term_l:+.3f} mm | {land.get('origin', 'unresolved')} |" if term_l and land.get("pad_length_mm") else "| PCB铜盘长 | - | unresolved | - | 阻塞 |",
        f"| Pin数量 | {spec.pin_count} | {len([p for p in geometry.pads if p.role == 'peripheral'])} | 0 | 一致 |",
        "",
        "## 重要说明",
        "",
        "Datasheet Package Outline描述的是元件本体和端子，不等于PCB Recommended Land Pattern。"
        "本文件把两者分别绘制和比较，禁止把计算铜盘冒充为原厂推荐值。",
        "本报告不能替代Allegro实际数据库回读；在ActualIR完成前，`.dra/.psm/.pad`仍需由Layout工程师在Allegro中复核。",
    ]
    target.write_text("\n".join(rows) + "\n", encoding="utf-8")


def write_verification_report(spec: FootprintSpec, target: Path) -> None:
    rows = [
        "# Footprint工程验证报告",
        "",
        f"- 元件：`{spec.part_number}`",
        f"- 来源文件：`{spec.source_file}`",
        f"- SHA-256：`{spec.source_sha256 or 'unresolved'}`",
        f"- 封装：`{spec.package_type}`",
        f"- 视图约定：`{spec.view_convention}`",
        f"- 规则来源层级：`{' > '.join(spec.rule_source_hierarchy)}`",
        "",
        "| 检查项 | 结果 | 说明 | 字段 | 证据页 |",
        "|---|---|---|---|---|",
    ]
    for check in spec.verification_results:
        label = {"pass": "PASS", "warning": "WARNING", "fail": "FAIL"}[check.status]
        pages = ", ".join(f"p.{page}" for page in check.evidence_pages) or "—"
        rows.append(
            f"| `{check.check_id}` | {label} | {check.message} | "
            f"{', '.join(check.fields) or '—'} | {pages} |"
        )
    rows += [
        "",
        "## 发布判定",
        "",
        "- `FAIL`：禁止写入正式Library；仅允许保留证据或评审草稿。",
        "- `WARNING`：制造规则或次要证据尚待确认；暂存封装必须由EE/Layout/SMT复核。",
        "- 全部`PASS`且公司规则齐全：才可进入正式Library审批流程。",
    ]
    target.write_text("\n".join(rows) + "\n", encoding="utf-8")


def write_bundle(spec: FootprintSpec, root: Path) -> Path:
    variant_identity = spec.package_code or f"{spec.package_type}{spec.pin_count or ''}"
    out = root / cadence_safe_component(
        f"{spec.part_number}_{variant_identity}", fallback="unknown_part"
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "footprint_spec.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    (out / "document_ir_summary.json").write_text(
        json.dumps(spec.document_scan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if spec.document_scan.get("pin_reconciliation"):
        from .pin_reconciliation import write_pin_reconciliation
        write_pin_reconciliation(spec.document_scan["pin_reconciliation"], out)
    write_dimensions(spec, out / "dimensions.md")
    write_verification_report(spec, out / "verification_report.md")
    try:
        geometry = build_geometry(spec)
        if spec.document_scan.get("pin_reconciliation"):
            from .pin_reconciliation import write_pin_reconciliation
            write_pin_reconciliation(spec.document_scan["pin_reconciliation"], out, geometry, spec)
        (out / "geometry.json").write_text(json.dumps(geometry.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "dimension_ir.json").write_text(
            json.dumps([item.model_dump(mode="json") for item in build_dimension_ir(spec, geometry)], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_datasheet_comparison(spec, geometry, out / "datasheet_comparison.md")
    except ValueError as exc:
        (out / "geometry_blocked.txt").write_text(str(exc) + "\n", encoding="utf-8")
    try:
        render_svg(spec, out / "preview.svg")
    except ValueError as exc:
        (out / "preview_blocked.txt").write_text(str(exc) + "\n", encoding="utf-8")
    return out
