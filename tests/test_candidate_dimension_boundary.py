import pytest

from datasheet_to_allegro.universal_extractor import PageRecord, _discover


@pytest.mark.parametrize('identity', ['UNRELATED_ALPHA', 'RENAMED_BETA'])
@pytest.mark.parametrize('family,token', [
    ('WLCSP', 'WL-CSP1.31x2.91-21(BSC)'),
    ('WLCSP', 'CSP2,50x3,50'),
    ('QFN', 'QFN32.5x40'),
    ('QFN', 'QFN32 x 40'),
    ('BGA', 'BGA64×80'),
    ('SOIC', 'SOIC300-mil'),
    ('QFP', 'QFP48 mm'),
])
def test_dimensions_never_become_counts(identity, family, token):
    candidates = _discover([PageRecord(1, f'{identity}\nPACKAGE OUTLINE\n{token}', families={family})])
    assert all(item.pin_count == 0 for item in candidates)


@pytest.mark.parametrize('family,token,count', [
    ('WLCSP', 'CSP-8', 8), ('QFN', 'QFN32', 32),
    ('BGA', 'BGA64', 64), ('QFP', 'QFP48', 48),
])
def test_valid_count_survives_conflicting_dimension_token(family, token, count):
    candidates = _discover([PageRecord(1, f'PACKAGE OUTLINE\n{token}\n{token}.5x80', families={family})])
    assert {item.pin_count for item in candidates} == {count}
