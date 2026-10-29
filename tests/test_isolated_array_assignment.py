from datasheet_to_allegro.universal_extractor import (
    PageRecord, PackageCandidate, _variant_records, _array_pin1_evidence,
)


def records(identity='UNRELATED'):
    return [
        PageRecord(1, identity + '\nWCSP4F Package Information', families={'WLCSP'}),
        PageRecord(2, 'Thermal test board\nThrough hole: diameter 0.7mm x 20\n'
                   '5. Pin Assignment (top view)\n1 2\nA VIN OUT\nB CTRL GND',
                   families={'THT'}, tables=[
                       [['', '1', '2'], ['A', 'VIN', 'OUT'], ['B', 'CTRL', 'GND']]]),
    ]


def test_pin_assignment_isolated_from_board_test_prose():
    for identity in ('UNRELATED', 'RENAMED'):
        result = _variant_records(records(identity), PackageCandidate('WLCSP', 1, 4, 'WCSP4F'))
        maps = [r for r in result if r.number == 2]
        assert len(maps) == 1
        assert maps[0].families == {'WLCSP'}
        assert 'Through hole' not in maps[0].text
        assert _array_pin1_evidence(maps, 'WLCSP', 4)


def test_assignment_conflicts_not_borrowed():
    variants = []
    r = records(); r[1].tables[0][2][2] = ''; variants.append(r)
    r = records(); r[1].tables[0][0][2] = '3'; variants.append(r)
    r = records(); r[1].text += '\nQFN4 Package'; variants.append(r)
    r = records(); r.append(PageRecord(3, 'WCSP8F Package Information', families={'WLCSP'})); variants.append(r)
    r = records(); r[1].tables[0][2][0] = 'A'; variants.append(r)
    for r in variants:
        result = _variant_records(r, PackageCandidate('WLCSP', 1, 4, 'WCSP4F'))
        assert not any(p.number == 2 and 'WLCSP' in p.families for p in result)
