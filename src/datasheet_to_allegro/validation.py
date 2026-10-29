from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from .models import FootprintSpec, Measurement, UnresolvedItem, VerificationResult


ARRAY_FAMILIES = {"BGA", "WLCSP", "LGA"}
LAND_PATTERN_CUE = re.compile(
    r"recommended\s+(?:pcb\s+)?land|land\s+pattern|example\s+board\s+layout|recommended\s+footprint",
    re.IGNORECASE,
)


def _result(
    check_id: str,
    status: str,
    message: str,
    fields: list[str] | None = None,
    pages: list[int] | None = None,
) -> VerificationResult:
    return VerificationResult(
        check_id=check_id,
        status=status,
        message=message,
        fields=fields or [],
        evidence_pages=sorted(set(pages or [])),
    )


def _measurement_state(name: str, item: Measurement | None) -> tuple[VerificationResult, UnresolvedItem | None]:
    if item is None:
        return (
            _result(f"TRACE_{name.upper()}", "fail", f"{name}缺失，无法追溯。", [name]),
            UnresolvedItem(
                code=f"{name.upper()}_UNRESOLVED",
                severity="blocking",
                message=f"{name}缺少可追溯尺寸。",
                required_action="从原始Datasheet图表中标注该尺寸并记录页码、图名和原文。",
            ),
        )
    pages = [ev.page for ev in item.evidence]
    if not item.evidence or any(not ev.figure_or_table.strip() or not ev.raw_text.strip() for ev in item.evidence):
        return (
            _result(f"TRACE_{name.upper()}", "fail", f"{name}缺少页码、图表名或原文。", [name], pages),
            UnresolvedItem(
                code=f"{name.upper()}_EVIDENCE_INCOMPLETE",
                severity="blocking",
                message=f"{name}的来源证据不完整。",
                required_action="补齐来源页码、图表名称、原始文字和提取方式。",
            ),
        )
    values = [value for value in (item.min_mm, item.nominal_mm, item.max_mm) if value is not None]
    if item.value_mm is not None:
        values.append(item.value_mm)
    if any(not math.isfinite(value) or value <= 0 for value in values) or (
        item.min_mm is not None and item.max_mm is not None and item.min_mm > item.max_mm
    ) or (
        item.nominal_mm is not None and item.min_mm is not None and item.nominal_mm < item.min_mm
    ) or (
        item.nominal_mm is not None and item.max_mm is not None and item.nominal_mm > item.max_mm
    ):
        return (
            _result(f"RANGE_{name.upper()}", "fail", f"{name}的Min/Nom/Max关系矛盾。", [name], pages),
            UnresolvedItem(
                code="DIMENSION_CONTRADICTION",
                severity="blocking",
                message=f"{name}的最小值、标称值和最大值互相矛盾。",
                required_action="回到原始PDF核对表头、单位、视图和OCR结果。",
            ),
        )
    weak_ocr = [ev for ev in item.evidence if ev.extraction_method == "ocr" and ev.confidence < 0.90]
    if weak_ocr:
        return (
            _result(f"OCR_{name.upper()}", "fail", f"{name}仅有低可信度OCR证据。", [name], pages),
            UnresolvedItem(
                code="OCR_CONFIDENCE_TOO_LOW",
                severity="blocking",
                message=f"{name}的OCR可信度低于0.90。",
                required_action="人工对照原图确认，或以表格/矢量文字重新提取。",
            ),
        )
    return _result(f"TRACE_{name.upper()}", "pass", f"{name}来源证据完整。", [name], pages), None


def validate_professional_spec(spec: FootprintSpec, source_path: Path | None = None) -> FootprintSpec:
    """Apply evidence, view and source-precedence checks without inventing geometry."""
    checks: list[VerificationResult] = []
    new_items: list[UnresolvedItem] = []
    family = spec.package_type.upper()
    scan = spec.document_scan
    full_scan = bool(
        scan.get("full_document_scanned")
        and scan.get("page_count")
        and scan.get("page_count") == scan.get("scanned_page_count")
    )
    if source_path is not None or scan:
        checks.append(_result(
            "FULL_DOCUMENT_SCAN", "pass" if full_scan else "fail",
            f"已扫描全部{scan.get('page_count', 0)}页并建立跨页证据索引。" if full_scan else "尚未完成整份PDF扫描，禁止调用Allegro。",
            ["document_scan"],
        ))
        if not full_scan:
            new_items.append(UnresolvedItem(
                code="FULL_DOCUMENT_SCAN_INCOMPLETE",
                severity="blocking",
                message="尚未逐页扫描整份PDF并合并封装相关证据。",
                required_action="完成全部页面的文字、表格、图形和OCR索引后重新生成。",
            ))
    # Array body outlines are just as physical as perimeter bodies. Skipping
    # these let a weak OCR / neighboring-package rectangle look verified.
    required = ["terminal_width_min", "body_width", "body_length", "package_height"]
    if family in ARRAY_FAMILIES:
        required.extend(["pitch_x", "pitch_y"])
    else:
        required.extend(["pitch", "terminal_length_min"])
    for name in required:
        check, unresolved = _measurement_state(name, getattr(spec, name))
        checks.append(check)
        if unresolved:
            new_items.append(unresolved)

    pin1_pages = [ev.page for ev in spec.source_evidence.get("pin_1_position", [])]
    if spec.pin_1_position and pin1_pages:
        checks.append(_result("PIN1_TRACE", "pass", "Pin 1位置具有独立证据。", ["pin_1_position"], pin1_pages))
    else:
        checks.append(_result("PIN1_TRACE", "fail", "Pin 1位置未独立验证。", ["pin_1_position"], pin1_pages))
        new_items.append(UnresolvedItem(code="PIN1_UNVERIFIED", message="Pin 1位置未独立验证。", required_action="对照Pin配置图和封装图确认Top/Bottom View中的Pin 1。"))

    numbering_pages = [ev.page for ev in spec.source_evidence.get("pin_numbering", [])]
    if spec.pin_numbering and numbering_pages:
        checks.append(_result("PIN_NUMBERING_TRACE", "pass", "Pin编号方向具有独立证据。", ["pin_numbering"], numbering_pages))
    else:
        checks.append(_result("PIN_NUMBERING_TRACE", "fail", "Pin编号方向未验证。", ["pin_numbering"], numbering_pages))
        new_items.append(UnresolvedItem(code="PIN_NUMBERING_UNVERIFIED", message="Pin编号方向未验证。", required_action="确认Component Top View或Terminal Bottom View及顺逆时针方向。"))

    land = spec.recommended_land_pattern
    land_pages = [ev.page for ev in land.evidence]
    if land.status == "manufacturer_recommended":
        has_geometry = isinstance(land.value, dict) and bool(land.value.get("pad_width_mm")) and bool(land.value.get("pad_length_mm"))
        has_cue = any(LAND_PATTERN_CUE.search(ev.raw_text + " " + ev.figure_or_table) for ev in land.evidence)
        if has_geometry and has_cue:
            checks.append(_result("LAND_PATTERN_IDENTITY", "pass", "已确认原厂Recommended Land Pattern及焊盘几何。", ["recommended_land_pattern"], land_pages))
        else:
            checks.append(_result("LAND_PATTERN_IDENTITY", "fail", "原厂Land Pattern身份或几何不完整。", ["recommended_land_pattern"], land_pages))
            new_items.append(UnresolvedItem(code="LAND_PATTERN_SOURCE_AMBIGUOUS", message="不能证明提取值来自Recommended Land Pattern，而不是Package Outline。", required_action="在同一原厂图页确认Land Pattern标题、铜盘尺寸和适用封装代码。"))
    elif land.status == "calculated":
        checks.append(_result("LAND_PATTERN_IDENTITY", "warning", "PCB Land为版本化计算值，不是原厂推荐值。", ["recommended_land_pattern"], land_pages))
    else:
        checks.append(_result("LAND_PATTERN_IDENTITY", "warning", "未找到原厂Recommended Land Pattern。", ["recommended_land_pattern"], land_pages))

    if spec.exposed_pad_width and spec.exposed_pad_length:
        if spec.exposed_pad_number:
            ep_status = "pass" if spec.source_evidence.get("exposed_pad_number") else "warning"
            checks.append(_result("EXPOSED_PAD_NUMBER", ep_status, "Exposed Pad编号已记录；仍需确认其来源与网络。", ["exposed_pad_number"]))
        else:
            checks.append(_result("EXPOSED_PAD_NUMBER", "fail", "Exposed Pad编号缺失。", ["exposed_pad_number"]))
            new_items.append(UnresolvedItem(code="EXPOSED_PAD_NUMBER_UNVERIFIED", message="Exposed Pad编号或网络未验证。", required_action="从Pin表/原理图确认EP编号和电气连接。"))

    inferred_view = spec.view_convention
    if inferred_view == "unresolved":
        numbering_view = (spec.pin_numbering or "").lower()
        pin1_view = (spec.pin_1_position or "").lower()
        if "top view" in numbering_view:
            inferred_view = "component_top"
        elif "bottom view" in numbering_view:
            inferred_view = "terminal_bottom"
        elif "top view" in pin1_view:
            inferred_view = "component_top"
        elif "bottom view" in pin1_view:
            inferred_view = "terminal_bottom"
    checks.append(_result(
        "VIEW_CONVENTION",
        "pass" if inferred_view != "unresolved" else "fail",
        f"视图约定：{inferred_view}。" if inferred_view != "unresolved" else "Top/Bottom View未解析。",
        ["view_convention"],
    ))
    if inferred_view == "unresolved":
        new_items.append(UnresolvedItem(code="VIEW_CONVENTION_UNRESOLVED", message="无法确定图面是Component Top View还是Terminal Bottom View。", required_action="人工确认视图；禁止在未确认时镜像Pin阵列。"))

    existing = {item.code: item for item in spec.unresolved_items}
    for item in new_items:
        existing.setdefault(item.code, item)
    source_hash = spec.source_sha256
    if source_path and source_path.is_file():
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    return spec.model_copy(update={
        "source_sha256": source_hash,
        "view_convention": inferred_view,
        "verification_results": checks,
        "unresolved_items": list(existing.values()),
    })
