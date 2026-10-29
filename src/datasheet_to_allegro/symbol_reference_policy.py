"""Shared human-readable symbol constraints; never device-name lookups.

Reference layouts inform side/order/property placement. Exact geometry from
reliable source diagrams wins; these constraints only ensure readability.
Electrical types and manufacturing identities are not copied by visual style.
"""
TEXT_UNIT_PER_CHAR = 6
TEXT_GUTTER = 36
MIN_PIN_SPACING = 20
MAX_SOURCE_WHITESPACE_SCALE = 1.25

def property_role(name):
    normalized = name.strip().casefold()
    if normalized in {'part reference','reference','value'}:
        return 'visible_identity'
    if normalized in {'pcb footprint','package variant'}:
        return 'physical_mapping'
    if normalized in {'room','side','side1'}:
        return 'design_instance_only'
    if normalized in {'source library','source part','source package','pdf source','datasheet revision'}:
        return 'provenance_not_device_identity'
    return 'preserve_for_review'
