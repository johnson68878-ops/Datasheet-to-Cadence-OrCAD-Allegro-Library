from __future__ import annotations
import math

from .models import FootprintSpec, Measurement, UnresolvedItem


ARRAYS = {"BGA", "WLCSP", "LGA"}
QUADS = {"QFN", "QFP", "LCC", "PLCC"}
DUALS = {"DFN", "SON", "SOIC", "SOP", "SSOP", "TSSOP", "MSOP", "SOT", "SOJ"}
THT = {"THT", "CONNECTOR", "DIP", "PGA", "SIP"}
SUPPORTED = ARRAYS | QUADS | DUALS | THT | {"DIODE", "TO"}


def _has_value(item: Measurement | None) -> bool:
    return bool(item and any(value is not None for value in (
        item.value_mm, item.nominal_mm, item.min_mm, item.max_mm
    )))


def _has_datasheet_evidence(item: Measurement | None) -> bool:
    if not _has_value(item) or item is None:
        return False
    if "POC ASSUMPTION" in (item.original_value or "").upper():
        return False
    return bool(item.evidence)


def _positive_land_dimensions(land: object) -> bool:
    """Return whether the rule carries finite positive PCB copper dimensions."""
    if not isinstance(land, dict):
        return False
    return all(
        isinstance(land.get(name), (int, float))
        and math.isfinite(land[name])
        and land[name] > 0
        for name in ("pad_width_mm", "pad_length_mm")
    )


def _release_approved_land(spec: FootprintSpec) -> bool:
    """Accept only a manufacturer recommendation or an approved company rule.

    A package ball/bump diameter, a reference-board opening, and a formula
    calculated from either are useful engineering evidence, but none of them is
    automatically a production PCB land recommendation.
    """
    rule = spec.recommended_land_pattern
    land = rule.value
    if not _positive_land_dimensions(land):
        return False
    if rule.status == "manufacturer_recommended":
        return (
            rule.source_tier in {
                "device_datasheet",
                "manufacturer_package_document",
                "manufacturer_application_note",
            }
            and any(evidence.evidence_scope == "land_pattern" for evidence in rule.evidence)
        )
    if rule.status == "company_rule":
        return (
            rule.source_tier == "company_rule"
            and bool(rule.source_revision)
            and isinstance(land, dict)
            and bool(land.get("profile_id"))
            and land.get("approval_status") == "approved"
        )
    return False


def _traceable_review_land(spec: FootprintSpec) -> bool:
    """Permit an isolated array review only when the non-release land is auditable."""
    rule = spec.recommended_land_pattern
    land = rule.value
    if not _positive_land_dimensions(land) or not isinstance(land, dict):
        return False
    if rule.status == "reference_design":
        return bool(rule.evidence and land.get("origin"))
    if rule.status == "calculated":
        inputs = land.get("calculation_inputs")
        return (
            bool(rule.evidence)
            and bool(land.get("profile_id"))
            and land.get("approval_status") == "not_approved"
            and land.get("role") == "pcb_copper_land"
            and isinstance(inputs, dict)
            and inputs.get("source_role") == "component_terminal"
            and bool(inputs.get("terminal_evidence_pages"))
            and bool(inputs.get("pitch_evidence_pages"))
        )
    if rule.status == "company_rule":
        return bool(land.get("profile_id") and rule.source_revision)
    return False


def generation_blockers(spec: FootprintSpec) -> list[UnresolvedItem]:
    """Return conditions that prevent a verifiable Allegro generation.

    Manufacturing-rule warnings do not block staging.  Missing or invented
    package geometry/topology does, because Allegro faithfully creating a wrong
    input is not a valid closed loop.
    """
    family = spec.package_type.upper()
    result: list[UnresolvedItem] = []

    def add(code: str, message: str, action: str) -> None:
        if code not in {item.code for item in result}:
            result.append(UnresolvedItem(
                code=code,
                severity="blocking",
                message=message,
                required_action=action,
            ))

    if family not in SUPPORTED:
        add("GEOMETRY_BACKEND_UNSUPPORTED", f"尚无{family or '未知'}封装的已验证几何后端。", "选择正确封装族或增加经过测试的通用几何后端。")
    if spec.pin_count <= 0:
        add("PIN_COUNT_UNRESOLVED", "Pin/Ball数量未从Datasheet证实。", "标注Pin Map或封装表中的Pin/Ball数量。")
    for field in ("body_width", "body_length", "package_height", "pitch", "pitch_x", "pitch_y", "terminal_width_min", "terminal_width_max", "terminal_length_min", "terminal_length_max", "exposed_pad_width", "exposed_pad_length"):
        measurement = getattr(spec, field)
        if measurement is None:
            continue
        values = [getattr(measurement, key) for key in ("min_mm", "nominal_mm", "max_mm") if getattr(measurement, key) is not None]
        extra = [measurement.value_mm] if measurement.value_mm is not None else []
        if any(not math.isfinite(v) or v <= 0 for v in values + extra) or values != sorted(values):
            add("INVALID_DIMENSION", f"{field}包含非有限值、非正尺寸或MIN/NOM/MAX矛盾。", "回到尺寸表核對欄位與單位。")
    for item in spec.verification_results:
        if item.status == "fail":
            add("EVIDENCE_VERIFICATION_FAILED", item.message, "先修正證據檢查失敗項，禁止生成。")
    if any("POC ASSUMPTION" in str(value).upper() for value in (spec.pin_1_position,spec.pin_numbering)):
        add("TOPOLOGY_ASSUMPTION", "Pin方向或編號仍是猜測值。", "以來源Pin Map證明方向與編號。")
    assumption_codes={'ARRAY_TOPOLOGY_REBUILT_FOR_RISK_STAGING','EXPOSED_PAD_NUMBER_ASSIGNED_FOR_POC','SIDE_PIN_COUNTS_INFERRED_FOR_POC'}
    if any(item.code in assumption_codes for item in spec.unresolved_items):
        add('TOPOLOGY_ASSUMPTION','行列、缺球、邊數或EP編號仍由PoC推算。','以來源確認拓撲；舊PoC函式不能繞過輸出門檻。')
    if spec.view_convention != "component_top":
        add("VIEW_CONVENTION_UNRESOLVED", "Component Top/Terminal Bottom视图未证实。", "在原始封装图上确认视图方向。")
    if not spec.pin_1_position:
        add("PIN1_UNVERIFIED", "Pin 1/A1方向未从Datasheet独立证实。", "标注Pin 1/A1角及视图方向。")
    if not spec.pin_numbering:
        add("PIN_NUMBERING_UNVERIFIED", "Pin/Ball编号顺序未证实。", "标注顺逆时针方向或完整Ball Map。")

    # All 52 Layout-approved production Golden DRA samples carry an explicit
    # PACKAGE_HEIGHT_MAX on PLACE_BOUND_TOP. Missing evidence may still enter
    # the isolated review flow, but it can never pass the formal release gate.
    if not _has_datasheet_evidence(spec.package_height):
        add("PACKAGE_HEIGHT_UNPROVEN", "封装最大高度未绑定Datasheet原始证据。", "在封装尺寸表或侧视图中标注最大高度A，并写入PACKAGE_HEIGHT_MAX。")

    # Golden Place Bound is the manufacturer body D/E, never a pad, marker,
    # silkscreen or courtyard envelope. This also applies to array packages.
    for code, label, measurement in (
        ("BODY_WIDTH_UNPROVEN", "本体宽度", spec.body_width),
        ("BODY_LENGTH_UNPROVEN", "本体长度", spec.body_length),
    ):
        if not _has_datasheet_evidence(measurement):
            add(code, f"{label}未绑定Datasheet原始证据。", f"在原始封装图或尺寸表中标注{label}。")

    land = spec.recommended_land_pattern.value
    if family in {"THT", "DIP", "PGA", "SIP", "CONNECTOR", "TO", "DIODE"} and not (isinstance(land, dict) and land.get("pads")):
        add("EXPLICIT_TERMINAL_MAP_REQUIRED", "通孔/连接器的坐标与编号映射尚未明确。", "提供逐端子坐标、编号与钻孔；不可假定按行编号。")
    if not isinstance(land, dict) or not land.get("pad_width_mm") or not land.get("pad_length_mm"):
        add("LAND_PATTERN_NOT_COMPUTABLE", "没有原厂Land Pattern，且证据尺寸不足以执行版本化规则计算。", "补齐端子/球径及Pitch，或选择公司批准的Padstack Profile。")
    if isinstance(land, dict):
        for name in ('pad_width_mm','pad_length_mm','drill_mm','exposed_pad_width_mm','exposed_pad_length_mm'):
            value=land.get(name)
            if value is not None and (not isinstance(value,(int,float)) or not math.isfinite(value) or value <= 0):
                add('INVALID_LAND_DIMENSION',f'{name}不是有限正尺寸。','核對焊盤尺寸，禁止輸出無效幾何。')
        if land.get('exposed_pad_width_mm') and land.get('exposed_pad_length_mm') and not spec.exposed_pad_number:
            add('EXPOSED_PAD_NUMBER_UNVERIFIED','Land Pattern有EP但其電氣編號未確認。','由EE確認EP編號，不能自動使用N+1。')
        if _positive_land_dimensions(land) and not _release_approved_land(spec):
            add(
                "LAND_PATTERN_NOT_RELEASE_APPROVED",
                "PCB铜盘不是带Land Pattern证据的原厂推荐值，也不是已审批且有版本的公司规则。",
                "保留为隔离审查输出；正式Library须取得原厂Recommended Land Pattern或已审批公司Padstack Profile。",
            )

    pitch_x = spec.pitch_x or spec.pitch
    pitch_y = spec.pitch_y or spec.pitch
    if family in ARRAYS:
        if len(set(spec.row_names)) != len(spec.row_names) or len(set(spec.missing_positions)) != len(spec.missing_positions):
            add("DUPLICATE_ARRAY_ADDRESS", "行名或缺球地址重複。", "核對完整球位表。")
        valid_addresses = {f"{row}{column}" for row in spec.row_names for column in range(1, (spec.column_count or 0)+1)}
        if set(spec.missing_positions) - valid_addresses:
            add("INVALID_MISSING_POSITION", "缺球地址不在已確認陣列內。", "核對缺球行列位置。")
        if not _has_datasheet_evidence(pitch_x) or not _has_datasheet_evidence(pitch_y):
            add("ARRAY_PITCH_UNPROVEN", "阵列X/Y Pitch未绑定Datasheet证据。", "标注阵列Pitch尺寸和页码。")
        if not spec.row_names or not spec.column_count:
            add("BALL_MAP_UNPROVEN", "阵列行名、列数或缺球映射未证实。", "从Ball Map提取完整行列与缺球位置。")
        elif len(spec.row_names) * spec.column_count - len(spec.missing_positions) != spec.pin_count:
            add("BALL_MAP_COUNT_MISMATCH", "Ball Map与Pin/Ball数量不一致。", "逐点核对缺球位置和总Ball数。")
    elif not (isinstance(land, dict) and land.get("pads")):
        for code, label, measurement in (
            ("PITCH_UNPROVEN", "Pitch", spec.pitch),
            ("TERMINAL_WIDTH_UNPROVEN", "端子宽度", spec.terminal_width_min),
            ("TERMINAL_LENGTH_UNPROVEN", "端子长度", spec.terminal_length_min),
        ):
            if not _has_datasheet_evidence(measurement):
                add(code, f"{label}未绑定Datasheet原始证据。", f"在原始封装图或尺寸表中标注{label}。")

    if family in QUADS and (len(spec.side_pin_counts) != 4 or sum(spec.side_pin_counts) != spec.pin_count):
        add("SIDE_PIN_MAP_UNPROVEN", "四边Pin数量映射未证实。", "从图纸提取每边Pin数，不能按长宽比例猜测。")
    if spec.exposed_pad_width and spec.exposed_pad_length and not spec.exposed_pad_number:
        add("EXPOSED_PAD_NUMBER_UNVERIFIED", "Exposed Pad存在，但编号/网络属性未证实。", "由EE确认EP编号及电气连接。")

    # Carry parser blockers into the closed-loop gate, excluding only missing
    # manufacturer land pattern when a traceable calculation now exists.
    non_blocking_after_calculation = {
        "NO_RECOMMENDED_LAND_PATTERN",
        "SOLDER_MASK_RULE_REQUIRED",
        "PASTE_RULE_REQUIRED",
        "COURTYARD_RULE_REQUIRED",
    }
    for item in spec.unresolved_items:
        if (item.severity == "blocking" and item.code not in non_blocking_after_calculation
                and item.code not in {existing.code for existing in result}):
            result.append(item)
    return result


def _traceable_perimeter_draft(spec: FootprintSpec) -> bool:
    """Allow a reproducible, unapproved manufacturing calculation, not invented facts."""
    from .industry_defaults import NO_LEAD, GULLWING_DUAL, GULLWING_QUAD, _calculate_manufacturing_rules
    from .models import RuleStatus
    rule = spec.recommended_land_pattern
    land = rule.value
    if (spec.package_type.upper() not in NO_LEAD | GULLWING_DUAL | GULLWING_QUAD
            or rule.status != 'calculated' or not _positive_land_dimensions(land)
            or land.get('approval_status') != 'not_approved'
            or not isinstance(land.get('calculation_inputs'), dict)):
        return False
    for name in ('terminal_width_min','terminal_length_min','pitch','body_width','body_length'):
        measurement = getattr(spec, name)
        if not measurement or not any(e.page > 0 and e.document_kind == 'datasheet' for e in measurement.evidence):
            return False
    # Recompute using the actual source fields and current versioned goals.
    # Never accept a supplied pad size merely because it claims to be calculated.
    expected = _calculate_manufacturing_rules(spec.model_copy(update={
        'recommended_land_pattern': RuleStatus(status='missing')})).recommended_land_pattern
    return bool(rule.evidence and expected.status == 'calculated' and land == expected.value)


def review_generation_blockers(spec: FootprintSpec) -> list[UnresolvedItem]:
    """Block only geometry that cannot be built safely for isolated review.

    Formal publication still uses :func:`generation_blockers`.  A review DRA
    may omit an unproven package body/height for an otherwise fully evidenced
    array because the backend draws a labelled ball-land envelope instead of
    pretending that envelope is the manufacturer body.
    """
    # Height is not used to position 2D copper. Only a genuinely absent field
    # may be omitted; present weak/conflicting measurements remain blocking.
    if spec.package_height is None:
        spec = spec.model_copy(update={
            'verification_results': [check for check in spec.verification_results
                if not (check.status == 'fail' and check.check_id == 'TRACE_PACKAGE_HEIGHT'
                        and check.fields == ['package_height'])],
            'unresolved_items': [item for item in spec.unresolved_items
                if item.code not in {'PACKAGE_HEIGHT_UNPROVEN','PACKAGE_HEIGHT_UNRESOLVED'}],
        })
    if spec.package_type.upper() not in ARRAYS:
        blockers = generation_blockers(spec)
        if spec.package_height is None:
            blockers = [item for item in blockers if item.code != 'PACKAGE_HEIGHT_UNPROVEN']
        rule = spec.recommended_land_pattern
        land = rule.value
        expected_corners = {}
        offset = 0
        for count in spec.side_pin_counts:
            expected_corners[str(offset+1)] = [offset+1]
            expected_corners[str(offset+count)] = [offset+count]
            offset += count
        corners = land.get("numbered_corner_pads", {}) if isinstance(land, dict) else {}
        bound_values = {
            binding.get("value") for binding in land.get("pad_bindings", [])
            if isinstance(binding,dict) and len(binding.get("rectangle_bbox", []))==4
            and len(binding.get("tips", []))==2
        } if isinstance(land,dict) else set()
        # A fully dimensioned copper drawing can be inspected in staging even
        # when captioned "example", not "recommended". Unlike array review,
        # this path exempts NO body/numbering requirement. Absent height is
        # handled above; present contradictory evidence is never waived.
        drawn_reference = (
            rule.status == "reference_design"
            and rule.source_tier == "device_datasheet"
            and _positive_land_dimensions(land)
            and land.get("role") == "pcb_copper_land"
            and land.get("binding_method") == "vector_arrow_extension_lines"
            and land.get("side_pin_counts") == spec.side_pin_counts
            and len(spec.side_pin_counts) == 4
            and sum(spec.side_pin_counts) == spec.pin_count
            and isinstance(corners,dict)
            and len(expected_corners) == 8
            and {str(k):v for k,v in corners.items()} == expected_corners
            and {land["pad_width_mm"],land["pad_length_mm"]} <= bound_values
            and len(land.get("dimension_bindings", [])) >= 2
            and any(e.page > 0 and e.evidence_scope == "land_pattern" for e in rule.evidence)
        )
        return [b for b in blockers if not (
            (drawn_reference or _traceable_perimeter_draft(spec)) and b.code == "LAND_PATTERN_NOT_RELEASE_APPROVED"
        )]
    # Only absent envelope dimensions are review-only. A present but invalid
    # dimension, weak OCR, or unrelated failed verification remains blocking.
    missing = {field for field in ("body_width", "body_length", "package_height")
               if getattr(spec, field) is None}
    release_only = {f"{field.upper()}_{suffix}" for field in missing
                    for suffix in ("UNPROVEN", "UNRESOLVED")}
    review_spec = spec.model_copy(update={
        "verification_results": [check for check in spec.verification_results
            if not (check.status == "fail" and len(check.fields) == 1
                    and check.fields[0] in missing
                    and check.check_id == f"TRACE_{check.fields[0].upper()}")]
    })
    if _traceable_review_land(review_spec):
        release_only.add("LAND_PATTERN_NOT_RELEASE_APPROVED")
    return [item for item in generation_blockers(review_spec) if item.code not in release_only]
