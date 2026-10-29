"""Allegro-only readiness; no functional symbol table is required here."""
from .audit import audit_as_dict, audit_geometry
from .generation_gate import generation_blockers, review_generation_blockers
from .geometry import build_geometry
from .models import FootprintSpec


def assess_draft_readiness(spec: FootprintSpec) -> dict:
    blockers = [item.model_dump(mode='json') for item in review_generation_blockers(spec)]
    report = {
        'status': 'hold', 'parser_eligible': not blockers,
        'geometry_checked': False, 'cad_generation_attempted': False,
        'native_verified': False, 'publish_allowed': False,
        'blockers': blockers, 'geometry_findings': [],
        'release_risks': [item.model_dump(mode='json') for item in generation_blockers(spec)],
        'functional_pin_table_required': False,
    }
    if blockers:
        return report
    try:
        geometry = build_geometry(spec)
        findings = audit_geometry(spec, geometry, gate_mode='review')
        report['geometry_checked'] = True
        report['geometry_findings'] = audit_as_dict(findings)
        if any(item.status == 'fail' for item in findings):
            report['blockers'].append({
                'code': 'DRAFT_GEOMETRY_AUDIT_FAILED', 'severity': 'blocking',
                'message': '几何审计未通过，不能进入Allegro生成。',
                'required_action': '核对geometry_findings及原始图面。'})
        else:
            report['status'] = 'ready_for_native_generation'
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        report['blockers'].append({
            'code': 'DRAFT_GEOMETRY_BUILD_FAILED', 'severity': 'blocking',
            'message': str(exc), 'required_action': '核对来源几何，禁止猜值。'})
    return report
