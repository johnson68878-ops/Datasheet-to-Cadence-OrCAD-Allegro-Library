from __future__ import annotations

import json
import re
from pathlib import Path

from .naming import cadence_ascii_text

from .geometry import FootprintGeometry
from .models import (
    ActualLayerObjectIR,
    ActualPadIR,
    AllegroActualIR,
    BoundingBoxIR,
    DiffFinding,
    FootprintSpec,
    SpecActualDiff,
)


REQUIRED_REVIEW_LAYERS = (
    "PACKAGE GEOMETRY/PLACE_BOUND_TOP",
    "PACKAGE GEOMETRY/SILKSCREEN_TOP",
)


def write_actual_export_skill(dra_path: Path, target_tsv: Path, target_il: Path) -> Path:
    """Write a read-only Allegro SKILL exporter for a fresh package session."""

    safe_target = target_tsv.resolve().as_posix().replace('"', "")
    safe_dra = dra_path.resolve().as_posix().replace('"', "")
    lines = [
        "; Read-only fresh-session actual database exporter",
        f'; source_dra={safe_dra}',
        "procedure(ds2aExportActual()",
        "  prog((designObj portObj pinObj padObj pinBox padName textObj textObjs lineObj lineObjs shapeObj shapeObjs layerName layerObj layerCount polyObj) ",
        "    axlDBTransactionCommit(axlDBTransactionStart())",
        "    designObj = axlDBGetDesign()",
        "    unless(designObj error(\"No Allegro design is open\\n\"))",
        f'    portObj = outfile("{safe_target}")',
        "    unless(portObj error(\"Cannot create actual export file\\n\"))",
        '    fprintf(portObj "SCHEMA\\t1.0.0\\n")',
        '    fprintf(portObj "UNIT\\tMM\\n")',
        '    fprintf(portObj "FRESH_SESSION\\t1\\n")',
        '    fprintf(portObj "PIN_COUNT\\t%d\\n" length(designObj->pins))',
        "    foreach(pinObj designObj->pins",
        '      padObj = axlDBGetPad(pinObj "PIN/TOP" "REGULAR")',
        "      pinBox = if(padObj then padObj->bBox else pinObj->bBox)",
        "      padName = if(padObj && padObj->parent then padObj->parent->name else \"\")",
        '      fprintf(portObj "PIN\\t%L\\t%.8f\\t%.8f\\t%.8f\\t%.8f\\t%.3f\\t%L\\n"',
        "        pinObj->number",
        "        car(pinObj->xy) cadr(pinObj->xy)",
        "        (car(cadr(pinBox)) - car(car(pinBox)))",
        "        (cadr(cadr(pinBox)) - cadr(car(pinBox)))",
        "        pinObj->rotation padName)",
        '      foreach(polyObj axlPolyFromDB(pinObj ?layer "ETCH/TOP") fprintf(portObj "COPPER_POLYGON\\t%L\\t%L\\t%L\\n" pinObj->number polyObj->vertices polyObj->holes))',
        '      foreach(layerName list("PIN/SOLDERMASK_TOP" "PIN/PASTEMASK_TOP")',
        '        padObj = axlDBGetPad(pinObj layerName "REGULAR")',
        '        fprintf(portObj "MASK\\t%L\\t%s\\t%L\\n" pinObj->number layerName if(padObj then padObj->bBox else nil))',
        '      )',
        "    )",
    ]
    for layer in (
        "PACKAGE GEOMETRY/ASSEMBLY_TOP",
        "PACKAGE GEOMETRY/BODY_CENTER",
        "PACKAGE GEOMETRY/PLACE_BOUND_TOP",
        "PACKAGE GEOMETRY/DIMENSION",
        "PACKAGE GEOMETRY/SILKSCREEN_TOP",
        "PACKAGE GEOMETRY/PASTEMASK_TOP",
        "REF DES/SILKSCREEN_TOP",
        "COMPONENT VALUE/SILKSCREEN_TOP",
        "REF DES/ASSEMBLY_TOP",
        "COMPONENT VALUE/ASSEMBLY_TOP",
    ):
        lines += [
            f'    layerName = "{layer}"',
            "    layerObj = axlLayerGet(layerName)",
            "    if(layerObj then",
            "      textObjs = if(designObj->text then setof(textObj designObj->text equal(textObj->layer layerName)) else nil)",
            "      axlClearSelSet()",
            "      axlVisibleDesign(nil)",
            "      axlVisibleLayer(layerName t)",
            "      axlVisibleUpdate(nil)",
            '      axlSetFindFilter(?enabled list("noall" "lines") ?onButtons list("noall" "lines"))',
            "      lineObjs = axlGetSelSet(axlAddSelectAll())",
            "      axlClearSelSet()",
            "      shapeObjs = axlDBGetShapes(layerName)",
            "      layerCount = length(textObjs) + length(lineObjs) + length(shapeObjs)",
            '      foreach(lineObj lineObjs fprintf(portObj "GEOMETRY\\t%L\\t%L\\t%L\\n" layerName lineObj->objType lineObj->bBox))',
            '      foreach(shapeObj shapeObjs fprintf(portObj "GEOMETRY\\t%L\\t%L\\t%L\\n" layerName shapeObj->objType shapeObj->bBox))',
            "    else",
            "      layerCount = 0",
            "    )",
            '    fprintf(portObj "LAYER\\t%s\\t%d\\n" layerName layerCount)',
            '    fprintf(portObj "GEOMETRY_INVENTORY\\t%L\\n" layerName)',
        ]
    lines += [
        '    foreach(textObj designObj->text fprintf(portObj "TEXT\\t%L\\t%L\\t%L\\t%L\\n" textObj->layer textObj->text textObj->xy textObj->bBox))',
        '    foreach(shapeObj axlDBGetShapes("PACKAGE GEOMETRY/PASTEMASK_TOP")',
        '      foreach(polyObj axlPolyFromDB(shapeObj) fprintf(portObj "PASTE_POLYGON\\tTOP\\t%L\\t%L\\n" polyObj->vertices polyObj->holes))',
        '    )',
        '    shapeObjs = axlDBGetShapes("PACKAGE GEOMETRY/PLACE_BOUND_TOP")',
        "    foreach(shapeObj shapeObjs",
        '      fprintf(portObj "PLACE_BOUND\\t%L\\t%L\\n" shapeObj->bBox axlDBGetProperties(shapeObj))',
        "    )",
        "    axlVisibleDesign(t)",
        "    axlVisibleUpdate(nil)",
        '    fprintf(portObj "DRC_COUNT\\t%d\\n" axlDRCGetCount())',
        '    fprintf(portObj "COMPLETE\\t1\\n")',
        "    close(portObj)",
        '    printf("Actual database exported to %s\\n" "' + safe_target + '")',
        "    t",
        "  )",
        ")",
        "ds2aExportActual()",
        "",
    ]
    target_il.parent.mkdir(parents=True, exist_ok=True)
    target_il.write_text("\n".join(cadence_ascii_text(line) for line in lines), encoding="ascii")
    return target_il


def write_actual_replay(export_skill: Path, target_scr: Path) -> Path:
    lines = [f'skill load("{export_skill.resolve().as_posix()}")', "exit", "fillin no"]
    target_scr.write_text("\n".join(cadence_ascii_text(line) for line in lines) + "\n", encoding="ascii")
    return target_scr


def _clean_skill_atom(value: str) -> str:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('\\"', '"')
    return value


def parse_actual_export(path: Path, source_dra: Path, *, extraction_log: str | None = None) -> AllegroActualIR:
    pins: list[ActualPadIR] = []
    layers: list[ActualLayerObjectIR] = []
    fresh = False
    complete = False
    drc_count: int | None = None
    place_bound_bbox: BoundingBoxIR | None = None
    package_height_max_mm: float | None = None
    masks: dict[str, dict[str, list[float] | None]] = {}
    copper_polygons = {}
    paste_polygons = []
    polygon_errors = []
    texts = []
    layer_geometry = []
    geometry_inventory_layers = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = raw_line.rstrip().split("\t")
        if not fields:
            continue
        if fields[0] == "FRESH_SESSION" and len(fields) > 1:
            fresh = fields[1] == "1"
        elif fields[0] == "COMPLETE" and len(fields) > 1:
            complete = fields[1] == "1"
        elif fields[0] == "DRC_COUNT" and len(fields) > 1:
            drc_count = int(fields[1])
        elif fields[0] == "PIN" and len(fields) >= 8:
            pins.append(ActualPadIR(
                number=_clean_skill_atom(fields[1]),
                x_mm=float(fields[2]),
                y_mm=float(fields[3]),
                width_mm=abs(float(fields[4])),
                length_mm=abs(float(fields[5])),
                rotation_deg=float(fields[6]),
                padstack=_clean_skill_atom(fields[7]) or None,
            ))
        elif fields[0] == "MASK" and len(fields) >= 4:
            values = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", fields[3])]
            size = [abs(values[2]-values[0]), abs(values[3]-values[1])] if len(values) == 4 else None
            masks.setdefault(_clean_skill_atom(fields[1]), {})[fields[2]] = size
        elif fields[0] in {"COPPER_POLYGON", "PASTE_POLYGON"} and len(fields) >= 4:
            values = [float(v) for v in re.findall(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", fields[2])]
            if len(values) < 9 or len(values) % 3 or any(abs(v) > 1e-9 for v in values[2::3]) or fields[3].strip() != "nil":
                polygon_errors.append(f"{fields[0]}:{fields[1]} arcs/holes or malformed polygon require native review")
            else:
                points = [[values[i], values[i+1]] for i in range(0, len(values), 3)]
                if fields[0] == "PASTE_POLYGON":
                    paste_polygons.append(points)
                else:
                    copper_polygons.setdefault(_clean_skill_atom(fields[1]), []).append(points)
        elif fields[0] == "GEOMETRY" and len(fields) >= 4:
            layer_geometry.append(dict(
                layer=_clean_skill_atom(fields[1]), object_type=_clean_skill_atom(fields[2]),
                bbox=[float(v) for v in re.findall(
                    r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", fields[3])]))
        elif fields[0] == "GEOMETRY_INVENTORY" and len(fields) == 2:
            geometry_inventory_layers.append(_clean_skill_atom(fields[1]))
        elif fields[0] == "TEXT" and len(fields) >= 5:
            texts.append(dict(layer=_clean_skill_atom(fields[1]), text=_clean_skill_atom(fields[2]),
                              xy=[float(v) for v in re.findall(r"[-+]?(?:\d*\.\d+|\d+)", fields[3])],
                              bbox=[float(v) for v in re.findall(r"[-+]?(?:\d*\.\d+|\d+)", fields[4])]))
        elif fields[0] == "LAYER" and len(fields) >= 3:
            layers.append(ActualLayerObjectIR(layer=fields[1], object_type="line_or_shape", count=int(fields[2])))
        elif fields[0] == "PLACE_BOUND" and len(fields) >= 2:
            values = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", fields[1])]
            if len(values) >= 4:
                place_bound_bbox = BoundingBoxIR(
                    x_min=values[0], y_min=values[1], x_max=values[2], y_max=values[3],
                    coordinate_system="allegro_mm",
                )
            properties = fields[2] if len(fields) >= 3 else ""
            height_match = re.search(r'PACKAGE_HEIGHT_MAX\s+"?([0-9.]+)\s*([^\s"\)]*)', properties, re.I)
            if height_match:
                value = float(height_match.group(1))
                unit = (height_match.group(2) or "MM").upper()
                package_height_max_mm = value * 25.4 if unit in {"IN", "INCH", "INCHES"} else value
    for pin in pins:
        pin.mask_apertures = masks.get(pin.number, {})
        pin.copper_polygons = copper_polygons.get(pin.number, [])
    return AllegroActualIR(
        source_dra=str(source_dra),
        texts=texts,
        layer_geometry=layer_geometry,
        geometry_inventory_layers=geometry_inventory_layers,
        paste_polygons=paste_polygons,
        polygon_review_errors=polygon_errors,
        extracted_in_fresh_session=fresh,
        pins=pins,
        layers=layers,
        place_bound_bbox=place_bound_bbox,
        package_height_max_mm=package_height_max_mm,
        drc_count=drc_count,
        extraction_log=extraction_log,
        # Allegro may return exit code 0 and run the exporter against a new,
        # empty design after a password-protected file fails to open.
        complete=complete and fresh and bool(pins) and not bool(re.search(
            r"Open\s+Cancel(?:ed|led)|No Password was provided|SPMHOD-(?:29|39)|Unable to open design",
            extraction_log or "", re.I,
        )),
    )


def compare_spec_to_actual(
    spec: FootprintSpec,
    geometry: FootprintGeometry,
    actual: AllegroActualIR,
    *,
    tolerance_mm: float = 0.001,
    gate_mode: str = 'release',
) -> SpecActualDiff:
    if gate_mode not in {'release', 'review'}:
        raise ValueError('gate_mode must be release or review')
    findings: list[DiffFinding] = []

    def finding(check_id: str, ok: bool, expected: object, got: object, message: str, *, warning: bool = False) -> None:
        findings.append(DiffFinding(
            check_id=check_id,
            status="pass" if ok else ("warning" if warning else "fail"),
            expected=expected,
            actual=got,
            tolerance_mm=tolerance_mm if isinstance(expected, (float, list, dict)) else None,
            message=message,
        ))

    expected_pads = {pad.number: pad for pad in geometry.pads}
    actual_pads = {pad.number: pad for pad in actual.pins}
    finding("EXPECTED_PIN_IDENTITIES_UNIQUE", len(expected_pads) == len(geometry.pads), len(geometry.pads), len(expected_pads), "生成计划中的编号不得重复，EP也必须保留独立编号。")
    finding("ACTUAL_PIN_IDENTITIES_UNIQUE", len(actual_pads) == len(actual.pins), len(actual.pins), len(actual_pads), "原生回读不得用字典去重掩盖重复Pad编号；有意共享编号须另行建模审核。")
    finding("FRESH_SESSION", actual.extracted_in_fresh_session, True, actual.extracted_in_fresh_session, "实际值必须来自重新打开DRA的新Allegro会话。")
    finding("ACTUAL_EXPORT_COMPLETE", actual.complete, True, actual.complete, "Allegro实际数据库导出必须完整结束。")
    finding("PIN_COUNT", len(expected_pads) == len(actual_pads), len(expected_pads), len(actual_pads), "Allegro实际Pin数量必须与GeometryIR一致。")
    finding("PIN_SET", set(expected_pads) == set(actual_pads), sorted(expected_pads), sorted(actual_pads), "Pin/Ball编号集合必须逐项一致。")
    pin_sources = spec.document_scan.get("pin_reconciliation")
    if pin_sources:
        map_pins = {p["number"] for p in pin_sources["pins"] if p["map_kind"] != "unpopulated"}
        finding("INDEPENDENT_PIN_MAP_SET", map_pins == set(actual_pads), sorted(map_pins), sorted(actual_pads),
                "实际DRA须与独立Pin Configuration图逐球一致。")
        finding("PIN_SOURCE_RECONCILIATION", pin_sources["status"] == "pass", [], pin_sources["issues"],
                "图表差异保留待审；MAP_ONLY表示球位和名称来自图，未伪造Pin List行。", warning=True)
    for number, expected in expected_pads.items():
        got = actual_pads.get(number)
        if got is None:
            continue
        position_ok = abs(expected.x_mm - got.x_mm) <= tolerance_mm and abs(expected.y_mm - got.y_mm) <= tolerance_mm
        finding(f"PIN_POSITION:{number}", position_ok, [expected.x_mm, expected.y_mm], [got.x_mm, got.y_mm], f"Pin {number}实际坐标必须与计划坐标一致。")
        from .engineering_checks import rotated_bounds
        expected_x, expected_y = rotated_bounds(expected.width_mm, expected.length_mm, expected.rotation_deg,
                                                circle=expected.shape.upper() in {"CIRCLE", "ROUND"})
        angle_error = abs((expected.rotation_deg - got.rotation_deg + 180) % 360 - 180)
        finding(f"PIN_ROTATION:{number}", angle_error <= 0.01, expected.rotation_deg, got.rotation_deg,
                "旋转角须独立核对；包围盒一致不代表局部几何一致。")
        size_ok = abs(expected_x - got.width_mm) <= tolerance_mm and abs(expected_y - got.length_mm) <= tolerance_mm
        finding(f"PIN_SIZE:{number}", size_ok, [expected_x, expected_y], [got.width_mm, got.length_mm], f"Pin {number}实际铜盘包围盒必须与计划尺寸一致。")
        finding(f"PADSTACK:{number}", bool(got.padstack), "non-empty", got.padstack, f"Pin {number}必须绑定实际Padstack。")
        ep_verified = False
        if expected.role == "exposed_pad":
            from .engineering_checks import audit_ep_polygons, assign_ep_windows
            eps = [p for p in geometry.pads if p.role == "exposed_pad"]
            ownership = assign_ep_windows({str(p.number): actual_pads[str(p.number)].copper_polygons
                                           for p in eps if str(p.number) in actual_pads}, actual.paste_polygons)
            native_errors = [e for e in actual.polygon_review_errors if e.startswith("PASTE_POLYGON") or e.startswith(f'COPPER_POLYGON:"{number}"')]
            if ownership["status"] == "pass" and len(got.copper_polygons) == 1 and not native_errors:
                try:
                    review = audit_ep_polygons(got.copper_polygons[0], ownership["windows"].get(number, []),
                                               coverage=geometry.exposed_pad_paste_area_coverage,
                                               min_gap=geometry.exposed_pad_paste_gap_mm,
                                               rotation=got.rotation_deg, origin=(got.x_mm, got.y_mm))
                except ValueError as exc:
                    review = {"status": "fail", "errors": [str(exc)]}
            else:
                review = {"status": "unresolved", "errors": native_errors or ownership["errors"] or ["missing or multipart copper polygon"]}
            ep_verified = review["status"] == "pass"
            finding(f"EP_POLYGON_GEOMETRY:{number}", ep_verified,
                    {"coverage": geometry.exposed_pad_paste_area_coverage, "gap": geometry.exposed_pad_paste_gap_mm},
                    review, "以原生多边形核对EP面积、分窗间距、重叠和越界；缺少证据保持HOLD。",
                    warning=review["status"] == "unresolved")
        # Newly exported files carry an explicit entry even when the aperture
        # is absent. Legacy IR without entries remains readable.
        mask_rule = spec.solder_mask_expansion.value
        paste_rule = spec.paste_mask_rule.value
        for layer, rule, key in (("PIN/SOLDERMASK_TOP", mask_rule, "expansion_mm"), ("PIN/PASTEMASK_TOP", paste_rule, "scale")):
            if layer not in got.mask_apertures or not isinstance(rule, dict) or key not in rule:
                continue
            if key == "scale" and expected.role == "exposed_pad":
                # EP stencil windows are package geometry, not necessarily a
                # single padstack aperture. Do not compare their envelope to area.
                if not ep_verified:
                    finding(f"EP_PASTE_REVIEW:{number}", False, "verified window area and geometry", "requires review", "EP锡膏分窗必须另核面积、间距与实际图形；Padstack包围盒不能证明分窗通过。", warning=True)
                continue
            value = float(rule[key])
            wanted = [expected_x + 2*value, expected_y + 2*value] if key == "expansion_mm" else [expected_x*value, expected_y*value]
            aperture = got.mask_apertures[layer]
            ok = aperture is not None and all(abs(a-b) <= tolerance_mm for a,b in zip(wanted, aperture))
            finding(f"MASK_SIZE:{number}:{layer}", ok, wanted, aperture, "实际阻焊/锡膏开窗须与所选规则一致，不能只检查铜盘。")
    layer_counts = {item.layer: item.count for item in actual.layers}
    for layer in REQUIRED_REVIEW_LAYERS:
        finding(f"LAYER:{layer}", layer_counts.get(layer, 0) > 0, ">0", layer_counts.get(layer, 0), f"{layer}必须存在实际数据库对象。")
    finding(
        "NO_DIMENSION_GRAPHICS",
        layer_counts.get("PACKAGE GEOMETRY/DIMENSION", 0) == 0,
        0,
        layer_counts.get("PACKAGE GEOMETRY/DIMENSION", 0),
        "Layout核准Golden库的正式Package Symbol不得保留DIMENSION评审图形；尺寸证据保存在IR与报告。",
    )
    finding(
        "TEXT_LAYER:REF DES/SILKSCREEN_TOP",
        layer_counts.get("REF DES/SILKSCREEN_TOP", 0) > 0,
        ">0",
        layer_counts.get("REF DES/SILKSCREEN_TOP", 0),
        "正式Package Symbol必须包含可编辑的RefDes丝印占位文字。",
    )
    bound = actual.place_bound_bbox
    bound_size = None if bound is None else [abs(bound.x_max - bound.x_min), abs(bound.y_max - bound.y_min)]
    bound_ok = bool(
        bound_size
        and abs(bound_size[0] - geometry.body_width_mm) <= tolerance_mm
        and abs(bound_size[1] - geometry.body_length_mm) <= tolerance_mm
    )
    finding(
        "PLACE_BOUND_BODY_SIZE",
        bound_ok,
        [geometry.body_width_mm, geometry.body_length_mm],
        bound_size,
        "Place Bound必须严格等于当前Datasheet本体D/E，不得使用焊盘、丝印或Courtyard补偿包络。",
    )
    expected_height = None
    if spec.package_height is not None:
        expected_height = next((value for value in (
            spec.package_height.max_mm,
            spec.package_height.nominal_mm,
            spec.package_height.value_mm,
            spec.package_height.min_mm,
        ) if value is not None), None)
    finding(
        "PACKAGE_HEIGHT_MAX",
        expected_height is not None
        and actual.package_height_max_mm is not None
        and abs(actual.package_height_max_mm - expected_height) <= tolerance_mm,
        expected_height,
        actual.package_height_max_mm,
        "Place Bound必须带有与Datasheet最大高度A一致的PACKAGE_HEIGHT_MAX（MM）。",
        warning=(gate_mode == 'review' and spec.package_height is None
                 and actual.package_height_max_mm is None),
    )
    if spec.package_type.upper() in {"BGA", "WLCSP", "LGA"}:
        for label_layer in ("PACKAGE GEOMETRY/SILKSCREEN_TOP", "PACKAGE GEOMETRY/ASSEMBLY_TOP"):
            labels = {t['text'].strip() for t in actual.texts if t['layer'] == label_layer}
            expected_labels = set(spec.row_names) | {str(i) for i in range(1, int(spec.column_count or 0)+1)}
            finding(f"ARRAY_LABEL_CONTENT:{label_layer}", bool(expected_labels) and expected_labels <= labels,
                    sorted(expected_labels), sorted(labels),
                    "逐项检查行列文字内容；对象数量足够不能证明A…T、1…16标签完整。")
        expected_axis_count = len(spec.row_names) + int(spec.column_count or 0)
        finding(
            "ARRAY_AXIS_LABELS:SILKSCREEN",
            layer_counts.get("PACKAGE GEOMETRY/SILKSCREEN_TOP", 0) >= expected_axis_count + 2,
            f">={expected_axis_count + 2}",
            layer_counts.get("PACKAGE GEOMETRY/SILKSCREEN_TOP", 0),
            "Silkscreen必须包含完整外框、Pin 1标识及全部行列文字。",
        )
        finding(
            "BODY_CENTER",
            layer_counts.get("PACKAGE GEOMETRY/BODY_CENTER", 0) >= 2,
            ">=2",
            layer_counts.get("PACKAGE GEOMETRY/BODY_CENTER", 0),
            "阵列封装必须包含水平与垂直BODY_CENTER中心线。",
        )
        for layer in (
            "COMPONENT VALUE/SILKSCREEN_TOP",
            "REF DES/ASSEMBLY_TOP",
            "COMPONENT VALUE/ASSEMBLY_TOP",
        ):
            finding(
                f"TEXT_LAYER:{layer}",
                layer_counts.get(layer, 0) > 0,
                ">0",
                layer_counts.get(layer, 0),
                f"{layer}必须包含可编辑的占位文字。",
            )
    marker_layer = "PACKAGE GEOMETRY/SILKSCREEN_TOP"
    finding(
        "PIN1_SILKSCREEN_MARKER",
        geometry.pin1_marker is not None and layer_counts.get(marker_layer, 0) > 0,
        "Pin 1圆点存在于PACKAGE GEOMETRY/SILKSCREEN_TOP",
        layer_counts.get(marker_layer, 0),
        "Pin 1丝印标记必须由GeometryIR生成，并在Allegro实际数据库中存在。",
    )
    if geometry.assembly_outline_kind == "manufacturer_body":
        layer = "PACKAGE GEOMETRY/ASSEMBLY_TOP"
        minimum = len(spec.row_names) + int(spec.column_count or 0) + 2 if spec.package_type.upper() in {"BGA", "WLCSP", "LGA"} else 1
        finding(f"LAYER:{layer}", layer_counts.get(layer, 0) >= minimum, f">={minimum}", layer_counts.get(layer, 0), "Datasheet定义了本体D/E时必须存在Assembly外框；阵列封装还必须包含Pin 1及行列标识。")
    elif spec.package_type.upper() in {"BGA", "WLCSP", "LGA"}:
        layer = "PACKAGE GEOMETRY/ASSEMBLY_TOP"
        objects = [obj for obj in actual.layer_geometry if obj["layer"] == layer]
        text_count = sum(t["layer"] == layer for t in actual.texts)
        marker = geometry.pin1_marker
        marker_bbox = None
        if marker:
            radius = marker.radius_mm + marker.line_width_mm / 2
            marker_bbox = [marker.x_mm-radius, marker.y_mm-radius,
                           marker.x_mm+radius, marker.y_mm+radius]
        marker_only = bool(marker_bbox) and len(objects) == 1 and (
            len(objects[0].get("bbox", [])) == 4
            and all(abs(a-b) <= tolerance_mm
                    for a, b in zip(objects[0]["bbox"], marker_bbox))
            and objects[0].get("object_type") in {"line", "path", "arc", "circle"}
        )
        inventory_complete = (
            layer in actual.geometry_inventory_layers
            and layer_counts.get(layer) == len(objects) + text_count
        )
        finding("NO_PSEUDO_BODY", inventory_complete and marker_only,
                {"geometry": "only source-independent Pin-1 marker", "bbox": marker_bbox},
                {"inventory_complete": inventory_complete, "objects": objects},
                "D/E未定义时允许行列文字与位置/大小一致的Pin 1标记；任何额外图形或缺失图形清单仍拒绝。")
    finding("DRC_COUNT", actual.drc_count == 0, 0, actual.drc_count, "新会话必须成功取得DRC数量，且数量必须为0。")
    has_fail = any(item.status == "fail" for item in findings)
    has_warning = any(item.status == "warning" for item in findings)
    return SpecActualDiff(
        part_number=spec.part_number,
        source_dra=actual.source_dra,
        findings=findings,
        status="fail" if has_fail else ("warning" if has_warning else "pass"),
        publish_allowed=gate_mode == 'release' and not has_fail and not has_warning and spec.status == "engineer_approved",
    )


def write_actual_reports(actual: AllegroActualIR, diff: SpecActualDiff, output_dir: Path) -> None:
    (output_dir / "allegro_actual_ir.json").write_text(actual.model_dump_json(indent=2), encoding="utf-8")
    (output_dir / "spec_vs_allegro_actual.json").write_text(diff.model_dump_json(indent=2), encoding="utf-8")
    rows = [
        "# Datasheet Spec与Allegro实际数据库比对",
        "",
        f"- 总结：`{diff.status.upper()}`",
        f"- 新会话回读：`{actual.extracted_in_fresh_session}`",
        f"- 正式Library发布：`{'ALLOW' if diff.publish_allowed else 'HOLD'}`",
        "",
        "| 检查 | 结果 | 期望 | 实际 | 说明 |",
        "|---|---|---|---|---|",
    ]
    for item in diff.findings:
        rows.append(f"| `{item.check_id}` | {item.status.upper()} | `{json.dumps(item.expected, ensure_ascii=False)}` | `{json.dumps(item.actual, ensure_ascii=False)}` | {item.message} |")
    (output_dir / "spec_vs_allegro_actual.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
