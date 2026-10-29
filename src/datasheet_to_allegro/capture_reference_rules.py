"""Evidence-preserving comparison of library and schematic attributes.

Differences are review findings, never automatic replacements of source data.
"""
from collections import Counter
from .symbol_reference_policy import property_role

def compare_attribute_maps(library, design):
    common = sorted(library.keys() & design.keys())
    return {
        'matched_fields': sum(library[k] == design[k] for k in common),
        'differences': [{'kind': k[0], 'object': k[1], 'attribute': k[2], 'library': library[k], 'design': design[k]} for k in common if library[k] != design[k]],
        'library_only': [list(k) for k in sorted(library.keys()-design.keys())],
        'design_only': [list(k) for k in sorted(design.keys()-library.keys())],
        'policy': 'review_only_no_automatic_override',
        'attribute_roles': {k[2]:property_role(k[2]) for k in common if k[0] == 'PROPERTY'},
    }

def review_instance_identity(properties):
    """Keep authoritative-looking strings separate; neither name proves MPN."""
    result = []
    footprint = properties.get('PCB Footprint', '').strip()
    if not footprint:
        result.append('FOOTPRINT_MISSING')
    elif footprint.isdecimal():
        result.append('FOOTPRINT_NUMERIC_REVIEW')
    if not properties.get('Value', '').strip():
        result.append('VALUE_MISSING')
    return result
