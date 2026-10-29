"""Conditional board-derived fallbacks, never substitutes for supplied rules."""
from .models import RuleStatus, UnresolvedItem
import math


def golden_ep_grid(width, length, coverage=0.62, gap=0.25):
    """Golden-derived window-size cap, not a device-name or fixed 2x2 rule.

    Reference-board native stencil windows are approximately 1.0 by 0.8 mm.
    Minimize window count subject to that maximum span and achievable coverage.
    """
    if not all(math.isfinite(v) and v > 0 for v in (width, length, coverage, gap)) or coverage > 1:
        raise ValueError('Invalid EP geometry')
    candidates = []
    for rows in range(1, 65):
        for columns in range(1, 65):
            uw, ul = width-gap*(columns-1), length-gap*(rows-1)
            if min(uw, ul) <= 0 or uw*ul < width*length*coverage:
                continue
            scale = math.sqrt(width*length*coverage/(uw*ul))
            w, l = uw/columns*scale, ul/rows*scale
            if max(w,l) <= 1.01 and min(w,l) >= 0.15:
                candidates.append((rows*columns, abs(math.log(w/l)), rows, columns))
    if not candidates:
        raise ValueError('EP cannot meet Golden window size and coverage constraints')
    _,_,rows,columns = min(candidates)
    return dict(exposed_pad_area_coverage=coverage, exposed_pad_window_rows=rows,
                exposed_pad_window_columns=columns, exposed_pad_window_gap_mm=gap)

REFERENCE_PROFILE = 'board_reference_035_array_v1'
REFERENCE_SOURCE = 'Anonymized board-derived reference; native audit 2026-09-10'

def _value(measurement):
    if measurement is None:
        return None
    return next((v for v in (measurement.value_mm, measurement.nominal_mm) if v is not None), None)

def apply_reference_fallback(original, calculated):
    """Fill only genuinely missing rules; preserve original declared values.

    Manufacturer rules and existing explicit company/engineer rules win.
    Board evidence supplies a review default for matching uniform 0.35-mm
    BGA/WLCSP pitch. Other pitches and land families retain existing calculation.
    This does not infer body, numbering, topology, rotation or an EP window.
    """
    updates = {}
    fields = ('recommended_land_pattern', 'solder_mask_expansion', 'paste_mask_rule')
    for field in fields:
        prior = getattr(original, field)
        if prior.value is not None or prior.status == 'manufacturer_recommended':
            updates[field] = prior
    pitches = [_value(original.pitch_x or original.pitch), _value(original.pitch_y or original.pitch)]
    matches = original.package_type.upper() in {'BGA','WLCSP'} and all(p is not None and abs(p-0.35)<0.00001 for p in pitches)
    applied = []
    ep_w, ep_l = _value(original.exposed_pad_width), _value(original.exposed_pad_length)
    if ep_w and ep_l and original.package_type.upper() in {'QFN','DFN','SON','TSSOP','HTSSOP','QFP'}:
        prior = original.paste_mask_rule
        # Preserve every supplied stencil field, including manufacturer rules.
        if prior.status == 'missing' and prior.value is None:
            base = calculated.paste_mask_rule.value if isinstance(calculated.paste_mask_rule.value, dict) else {}
            try:
                grid = golden_ep_grid(ep_w, ep_l)
            except ValueError:
                grid = None  # retain existing fallback rather than fabricate windows
            if grid:
                updates['paste_mask_rule'] = RuleStatus(status='reference_design',
                    value={**base, **grid, 'profile_id':'golden_adaptive_ep_v1'},
                    source_tier='reference_design',source_revision='golden_adaptive_ep_v1',
                    note='Golden Reference-board原生分窗经验：最大窗边约1mm、目标62%、间距0.25mm；按实际EP大小选择行列，不按料号。')
                applied.append('paste_mask_rule')
    if matches:
        for field, value in (
            ('recommended_land_pattern', {'pad_width_mm':0.22, 'pad_length_mm':0.22}),
            ('solder_mask_expansion', {'expansion_mm':0.04}),
            ('paste_mask_rule', {'scale':1.0}),
        ):
            if field in updates:
                continue
            updates[field] = RuleStatus(status='reference_design', value={**value,'profile_id':REFERENCE_PROFILE},
                source_tier='reference_design', source_revision=REFERENCE_PROFILE,
                note=f'{REFERENCE_SOURCE}；仅缺项且双轴Pitch=0.35mm时使用，非原厂推荐；Layout/SMT复核。')
            applied.append(field)
    if applied:
        warnings = [w for w in calculated.unresolved_items if w.code != 'BOARD_REFERENCE_DEFAULT_APPLIED']
        warnings.append(UnresolvedItem(code='BOARD_REFERENCE_DEFAULT_APPLIED', severity='warning',
            message='采用板级参考缺省：'+', '.join(applied), required_action='核对封装结构、球径、板厂阻焊能力和钢网；原厂/工程师明确规则优先。'))
        updates.update(unresolved_items=warnings, status='draft')
    return calculated.model_copy(update=updates)
