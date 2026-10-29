from pathlib import Path
import pytest

from datasheet_to_allegro.positioned_tables import parameter_rows
from datasheet_to_allegro.universal_extractor import PageRecord, PackageCandidate, _candidate_spec
from test_positioned_pdf_tables import token


def table_tokens():
    return [token('Parameter',10,20,55),token('Min',110,15,20),token('Normal',160,15,35),token('Max',225,15,20),
            token('Millimeters',145,35,70),token('Package body',10,55,70),token('dimension Y',10,67,70),
            token('1.51',110,62,20),token('1.55',170,62,20),token('1.59',225,62,20)]


def test_multiline_named_parameter_keeps_columns_and_axis():
    rows=parameter_rows(table_tokens())
    assert len(rows)==1
    assert rows[0]['label']=='Package body dimension Y'
    assert rows[0]['values']==dict(min_mm=1.51,nominal_mm=1.55,max_mm=1.59)


def test_weak_numeric_cell_never_filled_from_other_columns():
    tokens=table_tokens()
    tokens[-1]['confidence']=.5
    assert parameter_rows(tokens)==[]


def test_header_corroboration_requires_same_role_and_position():
    from datasheet_to_allegro.ocr_crosscheck import confirm_header_tokens
    primary=[token('MIN.',10,10,confidence=.8),token('MAX.',90,10,confidence=.8)]
    independent=[token('min',10,10),token('MAX',190,10)]
    assert confirm_header_tokens(primary,independent)==1
    assert primary[0]['confidence']==.96 and primary[1]['confidence']==.8


def test_parameter_table_requires_units_and_does_not_sort_bad_limits():
    tokens=table_tokens()
    tokens=[t for t in tokens if t['text']!='Millimeters']
    assert not parameter_rows(tokens)
    tokens=table_tokens()
    tokens[-1]['text']='1.40'
    assert not parameter_rows(tokens)


def test_other_family_page_never_supplies_body():
    from datasheet_to_allegro.models import Measurement,SourceEvidence
    value=Measurement(value_mm=8,original_value='8',original_unit='mm',evidence=[SourceEvidence(
        page=1,figure_or_table='DFN body',raw_text='E 8',extraction_method='pdf_text',confidence=1)])
    other=PageRecord(1,'Package DFN-8',families={'DFN'},dimension_schema_values={'E':value,'D':value})
    own=PageRecord(2,'Package CSP-8',families={'WLCSP'})
    spec=_candidate_spec(Path('arbitrary.pdf'),[other,own],PackageCandidate('WLCSP',2,8,'CSP-8'))
    assert spec.body_width is None and spec.body_length is None


