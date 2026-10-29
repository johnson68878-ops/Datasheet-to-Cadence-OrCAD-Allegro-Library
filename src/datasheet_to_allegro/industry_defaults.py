from __future__ import annotations

from .models import FootprintSpec, Measurement, RuleStatus, SourceEvidence, UnresolvedItem


PROFILE_ID = "STAGING_MANUFACTURING_PROFILE_2026_08"
NO_LEAD = {"QFN", "DFN", "SON", "LCC"}
GULLWING_DUAL = {"SOIC", "SOP", "SSOP", "TSSOP", "MSOP", "SOT", "SOJ"}
GULLWING_QUAD = {"QFP", "PLCC"}
ARRAYS = {"BGA", "WLCSP", "LGA"}
THROUGH_HOLE = {"THT", "DIP", "PGA", "SIP", "CONNECTOR"}


def _value(item: Measurement | None) -> float | None:
    if item is None:
        return None
    if item.value_mm is not None:
        return item.value_mm
    if item.nominal_mm is not None:
        return item.nominal_mm
    if item.min_mm is not None and item.max_mm is not None:
        return (item.min_mm + item.max_mm) / 2
    return item.min_mm or item.max_mm


def _evidence(label: str, raw: str) -> SourceEvidence:
    return SourceEvidence(
        page=0,
        figure_or_table=label,
        raw_text=raw,
        extraction_method="visual_verified",
        confidence=0.93,
        document_kind="industry_standard",
        evidence_scope="land_pattern",
        source_title=PROFILE_ID,
    )


def _calculate_manufacturing_rules(spec: FootprintSpec) -> FootprintSpec:
    """Calculate manufacturing rules only; package facts are immutable here."""
    family = spec.package_type.upper()
    explicit_land = isinstance(spec.recommended_land_pattern.value, dict) and bool(spec.recommended_land_pattern.value.get("pads"))
    if family not in NO_LEAD | GULLWING_DUAL | GULLWING_QUAD | ARRAYS | THROUGH_HOLE and not explicit_land:
        return spec
    if not spec.pin_count:
        return spec
    if family not in ARRAYS and (_value(spec.body_width) is None or _value(spec.body_length) is None):
        return spec

    terminal_w = _value(spec.terminal_width_min)
    terminal_l = _value(spec.terminal_length_min)
    pitch = _value(spec.pitch_x or spec.pitch)
    existing = spec.recommended_land_pattern.value
    updates: dict[str, object] = {}
    manufacturer_land = spec.recommended_land_pattern.status == "manufacturer_recommended"
    extracted_land = spec.recommended_land_pattern.status in {"manufacturer_recommended", "reference_design"}

    if isinstance(existing, dict) and existing.get("pad_width_mm") and existing.get("pad_length_mm"):
        land = dict(existing)
        land.setdefault("profile_id", PROFILE_ID)
        land.setdefault("origin", "manufacturer_or_extracted_land_pattern")
        land.setdefault("role", "pcb_copper_land")
        evidence = list(spec.recommended_land_pattern.evidence)
    elif family in NO_LEAD:
        if terminal_w is None or terminal_l is None:
            return spec
        toe, heel = 0.10, 0.05
        pad_width = max(0.20 if pitch and pitch <= 0.40 else 0.0, terminal_w)
        pad_length = terminal_l + toe + heel
        land = {"pad_width_mm": round(pad_width, 4), "pad_length_mm": round(pad_length, 4), "toe_goal_mm": toe, "heel_goal_mm": heel, "side_goal_mm": 0.0, "pad_center_inset_mm": round(pad_length / 2 - toe, 4), "profile_id": PROFILE_ID, "origin": "industry_calculated_not_manufacturer", "role": "pcb_copper_land", "approval_status": "not_approved"}
        evidence = [_evidence("IPC public QFN/SON guidance", "Minimum Toe 0.10 mm; Heel 0.05 mm; Side 0.00 mm.")]
    elif family in GULLWING_DUAL | GULLWING_QUAD:
        if terminal_w is None or terminal_l is None:
            return spec
        toe, heel, side = 0.30, 0.10, 0.03
        pad_width = terminal_w + side * 2
        pad_length = terminal_l + toe + heel
        land = {"pad_width_mm": round(pad_width, 4), "pad_length_mm": round(pad_length, 4), "toe_goal_mm": toe, "heel_goal_mm": heel, "side_goal_mm": side, "pad_center_outset_mm": round((terminal_l + toe - heel) / 2, 4), "profile_id": PROFILE_ID, "origin": "industry_calculated_not_manufacturer", "role": "pcb_copper_land", "approval_status": "not_approved"}
        evidence = [_evidence("Generic gull-wing PoC goals", "Toe 0.30 mm; Heel 0.10 mm; Side 0.03 mm per side.")]
    elif family in ARRAYS:
        if terminal_w is None or pitch is None:
            return spec
        diameter = min(terminal_w * 0.80, pitch * 0.60)
        land = {"pad_width_mm": round(diameter, 4), "pad_length_mm": round(diameter, 4), "diameter_ratio_to_ball": 0.80, "max_diameter_ratio_to_pitch": 0.60, "profile_id": PROFILE_ID, "origin": "industry_calculated_not_manufacturer", "role": "pcb_copper_land", "approval_status": "not_approved"}
        land['calculation_inputs'] = {
            'terminal_width_mm': terminal_w, 'pitch_mm': pitch,
            'formula': 'min(terminal_width_mm * 0.80, pitch_mm * 0.60)',
            'source_role': 'component_terminal',
            'target_role': 'pcb_copper_land',
            'terminal_evidence_pages': sorted({e.page for e in spec.terminal_width_min.evidence}),
            'pitch_evidence_pages': sorted({e.page for e in (spec.pitch_x or spec.pitch).evidence}),
        }
        evidence = [_evidence("Array NSMD PoC rule", "Copper diameter=min(0.80×ball/bump diameter, 0.60×pitch).")]
    else:
        if terminal_w is None or pitch is None:
            return spec
        drill, annular = terminal_w + 0.20, 0.30
        pad_diameter = drill + annular * 2
        land = {"pad_width_mm": round(pad_diameter, 4), "pad_length_mm": round(pad_diameter, 4), "drill_mm": round(drill, 4), "annular_ring_mm": annular, "profile_id": PROFILE_ID, "origin": "industry_calculated_not_manufacturer", "role": "pcb_copper_land", "approval_status": "not_approved"}
        evidence = [_evidence("Through-hole PoC rule", "Finished hole=lead+0.20 mm; annular ring=0.30 mm per side.")]
    if (family in NO_LEAD | GULLWING_DUAL | GULLWING_QUAD
            and not (isinstance(existing, dict) and existing.get('pad_width_mm') and existing.get('pad_length_mm'))):
        land['calculation_inputs'] = {
            'method': 'no_lead_goals' if family in NO_LEAD else 'gullwing_goals',
            'source_role': 'component_terminal', 'target_role': 'pcb_copper_land',
            'terminal_width_mm': terminal_w, 'terminal_length_mm': terminal_l,
            'pitch_mm': pitch,
            'source_measurements': {name: getattr(spec, name).model_dump(mode='json')
                if getattr(spec, name) is not None else None for name in
                ('terminal_width_min','terminal_length_min','pitch','body_width','body_length')},
            'formula': ('width=max(profile_min_width,terminal_width); length=terminal_length+toe+heel; inset=length/2-toe'
                if family in NO_LEAD else
                'width=terminal_width+2*side; length=terminal_length+toe+heel; outset=(terminal_length+toe-heel)/2'),
        }
    updates.update({
        "recommended_land_pattern": RuleStatus(
            status=(spec.recommended_land_pattern.status if extracted_land else "calculated"),
            value=land,
            evidence=evidence,
            note=(
                "铜盘来自原厂；阻焊与钢网按各自证据状态执行，Courtyard仍需公司规则。"
                if manufacturer_land else (
                    "采用原厂尺寸化Land图，但该图未标注Recommended；保持HOLD等待Layout确认。"
                    if extracted_land else "暂存计算；不是量产放行。"
                )
            ),
            source_tier=spec.recommended_land_pattern.source_tier if extracted_land else "industry_standard",
        ),
        "solder_mask_expansion": spec.solder_mask_expansion if spec.solder_mask_expansion.status == "manufacturer_recommended" else RuleStatus(status="company_rule", value={"expansion_mm": 0.05, "profile_id": PROFILE_ID, "role": "solder_mask_opening", "basis_role": "pcb_copper_land", "approval_status": "not_approved"}, note="PoC默认每边外扩0.05 mm。", source_tier="company_rule"),
        "paste_mask_rule": spec.paste_mask_rule if spec.paste_mask_rule.status == "manufacturer_recommended" else RuleStatus(status="calculated", value={"scale": 1.0, "exposed_pad_area_coverage": 0.625, "exposed_pad_window_rows": 2, "exposed_pad_window_columns": 2, "exposed_pad_window_gap_mm": 0.20, "profile_id": PROFILE_ID, "role": "paste_mask_opening", "basis_role": "pcb_copper_land", "approval_status": "not_approved"}, note="外围1:1；EP采用2×2分窗，目标面积覆盖62.5%；正式值由SMT确认。", source_tier="manufacturer_application_note", source_uri="https://www.nxp.com/docs/en/application-note/AN1902.pdf", source_revision="Rev. 9"),
        "courtyard": RuleStatus(status="company_rule", value={"clearance_mm": 0.25, "profile_id": PROFILE_ID}, note="PoC默认外扩0.25 mm。", source_tier="company_rule"),
    })
    # A staging default must not relabel supplied manufacturing evidence or
    # erase its revision/approval metadata. The gate decides its acceptability.
    if isinstance(existing, dict) and (
        existing.get("pads")
        or (existing.get("pad_width_mm") and existing.get("pad_length_mm"))
    ):
        updates["recommended_land_pattern"] = spec.recommended_land_pattern.model_copy(deep=True)
    for rule_name in ("solder_mask_expansion", "paste_mask_rule", "courtyard"):
        supplied_rule = getattr(spec, rule_name)
        if supplied_rule.status != "missing" and supplied_rule.value is not None:
            updates[rule_name] = supplied_rule.model_copy(deep=True)
    return spec.model_copy(update=updates)


def apply_staging_manufacturing_profile(spec: FootprintSpec) -> FootprintSpec:
    """Apply versioned manufacturing calculations without inventing package facts.

    This is the only rule application entry point used by the desktop product.
    It may calculate copper/mask/paste/courtyard from dimensions already tied to
    Datasheet evidence, but it never guesses body size, pitch, pin topology,
    array map, Pin 1, or exposed-pad number.  Existing blocking findings remain
    blocking so the generation gate can stop an unprovable package.
    """
    from .reference_defaults import apply_reference_fallback
    calculated = _calculate_manufacturing_rules(spec)
    if calculated is spec:
        return apply_reference_fallback(spec, spec)

    calculation_note: list[UnresolvedItem] = []
    if calculated.recommended_land_pattern.status == "calculated":
        land = calculated.recommended_land_pattern.value
        detail = ''
        if isinstance(land, dict) and isinstance(land.get('calculation_inputs'), dict) and calculated.package_type.upper() in ARRAYS:
            inputs = land['calculation_inputs']
            detail = (f"铜盘=min({inputs['terminal_width_mm']:g}×0.80, "
                      f"{inputs['pitch_mm']:g}×0.60)={land['pad_width_mm']:g} mm；")
        calculation_note.append(UnresolvedItem(
            code="STAGING_LAND_PATTERN_CALCULATED",
            severity="warning",
            message=(
                f"铜盘由版本化规则 {PROFILE_ID} 根据Datasheet物理尺寸计算；"
                + detail +
                "它不是原厂Recommended Land Pattern。"
            ),
            required_action="Layout/SMT确认公司Padstack、阻焊和钢网Profile后方可发布正式Library。",
        ))

    preserved = calculated.model_copy(update={
        # Rule calculation must preserve every parser blocker.
        "unresolved_items": list(spec.unresolved_items) + calculation_note,
    })
    return apply_reference_fallback(spec, preserved)
