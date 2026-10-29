import pytest
from datasheet_to_allegro.dimension_tables import read_table, read_document_tables, tables_from_ocr
from datasheet_to_allegro.universal_extractor import PageRecord, _body_pair, _min_nom_max_ocr_values


def test_dual_unit_repeated_min_nom_max_groups_keep_their_own_units():
    rows=[
        ['', '(MM)', '(MM)', 'Millimeters', 'Millimeters', '(Inch)', '(Inch)'],
        ['Symbol', 'MIN.', 'NOM.', 'MAX.', 'MIN.', 'NOM.', 'MAX.'],
        ['A', '0.60', '0.70', '0.80', '0.024', '0.028', '0.032'],
        ['b', '0.20', '0.25', '0.30', '0.008', '0.010', '0.012'],
    ]
    values,issues,_=read_table(rows,46,'Controlling Dimension: Millimeters')
    assert issues==[]
    assert (values['A'].min_mm,values['A'].nominal_mm,values['A'].max_mm)==(0.60,0.70,0.80)
    assert (values['b'].min_mm,values['b'].nominal_mm,values['b'].max_mm)==(0.20,0.25,0.30)


@pytest.mark.parametrize('qualifier',['BSC','REF','REFERENCE'])
def test_standalone_qualifier_in_adjacent_column(qualifier):
    rows=[['Symbol','mm','',''],['','Min','Nor.','Max'],['D','','6.6',qualifier]]
    values,issues,_=read_table(rows,1)
    assert not issues and values['D'].value_mm==6.6
    rows[-1]=['D','6.5','6.6',qualifier]
    values,issues,_=read_table(rows,1)
    assert not values and any(i['code']=='DIMENSION_BSC_CONFLICT' for i in issues)


@pytest.mark.parametrize('page', [1,57,411])
@pytest.mark.parametrize('order', [('Min','Typ','Max'),('Max','Min','Typ')])
def test_headers_not_numeric_order_or_page_number_define_dimensions(page,order):
    values={'Min':'1.2','Typ':'1.3','Max':'1.4'}
    rows=[['Symbol','mm','',''],['',*order],['D',*[values[k] for k in order]]]
    dimensions,issues,_=read_table(rows,page)
    assert not issues
    assert dimensions['D'].min_mm==1.2 and dimensions['D'].max_mm==1.4
    assert dimensions['D'].nominal_mm==1.3
    assert dimensions['D'].evidence[0].page==page


def test_mixed_units_are_compared_not_flattened():
    rows=[['Symbol','inch','','mm',''],['','Max','Min','Max','Min'],['D','.1','.05','2.54','1.27']]
    values,issues,_=read_table(rows,1)
    assert not issues and values['D'].max_mm==2.54 and values['D'].min_mm==1.27
    rows[-1][-1]='1.5'
    assert any(i['code']=='DIMENSION_UNIT_CONFLICT' for i in read_table(rows,1)[1])


def test_bad_digits_and_reversed_ranges_are_never_repaired():
    assert _min_nom_max_ocr_values('1.53 1.55 137')==[]
    rows=[['Symbol','mm','',''],['','Min','Typ','Max'],['D','1.53','1.55','137']]
    # Do not hallucinate 1.57. Cross-source checks/range bounds remain required.
    values,_,_=read_table(rows,1)
    assert values['D'].max_mm == 137
    rows[-1]=['D','1.55','1.53','1.57']
    values,issues,_=read_table(rows,1)
    assert 'D' not in values and issues[0]['code']=='DIMENSION_RANGE_CONFLICT'


def test_conflicting_duplicate_cannot_reappear_from_third_row():
    rows=[['Symbol','mm',''],['','Min','Max'],['D','1','2'],['D','3','4'],['D','1','2']]
    values,issues,_=read_table(rows,1)
    assert 'D' not in values and issues


def test_bsc_is_not_a_minimum_or_invented_tolerance():
    values,issues,_=read_table([['Symbol','mm',''],['','Min','Max'],['D','7 BSC','']],1)
    assert not issues and values['D'].value_mm==7 and values['D'].min_mm is None


def test_cross_page_headerless_continuation_keeps_units_and_roles():
    records=[PageRecord(41,'QFP48 dimensions mm',tables=[[['Symbol','mm',''],['','Max','Min'],['D','9','8']]]),
             PageRecord(42,'QFP48 dimensions mm continued',tables=[[['E','7','6']]])]
    values,issues=read_document_tables(records)
    assert not issues and values[42]['E'].min_mm==6
    records[1].number=44
    assert 44 not in read_document_tables(records)[0]


def test_body_symbol_definition_overrides_lead_span():
    table=[['Symbol','mm',''],['','Min','Max'],['D','9 BSC',''],['E','9 BSC',''],['D1','7 BSC',''],['E1','7 BSC','']]
    record=PageRecord(3,'D1 and E1 do not include mold protrusion.',tables=[table])
    width,length=_body_pair([record],3)
    assert width.value_mm==length.value_mm==7


def test_standard_tssop_symbols_bind_body_without_part_number_routing():
    from pathlib import Path
    from datasheet_to_allegro.universal_extractor import PackageCandidate, _candidate_spec
    rows=[['Symbol','mm','',''],['','Min','Typ','Max'],['D','6.4','6.5','6.6'],
          ['E','6.2','6.4','6.6'],['E1','4.3','4.4','4.5'],['e','','.65',''],
          ['b','.19','','.3'],['L','.45','.6','.75'],['A','','','1.2']]
    for name in ('brand_new_part','unseen_vendor'):
        record=PageRecord(1,'TSSOP20 Package dimensions mm',tables=[rows],families={'SOIC'})
        spec=_candidate_spec(Path(name+'.pdf'),[record],PackageCandidate('SOIC',1,20,'TSSOP20'))
        assert (spec.body_width.min_mm,spec.body_width.max_mm)==(4.3,4.5)
        assert (spec.body_length.min_mm,spec.body_length.max_mm)==(6.4,6.6)
        assert 'BODY_SYMBOL_BINDING_UNRESOLVED' not in {b.code for b in spec.unresolved_items}


@pytest.mark.parametrize('offset,scale', [(0,1),(800,2),(50,.7)])
def test_ocr_columns_follow_header_positions_after_translation_and_scaling(offset,scale):
    def token(text,x,y,confidence=.99):
        return {'text':text,'confidence':confidence,'polygon':[[offset+scale*a,offset+scale*b] for a,b in [(x,y),(x+12,y),(x+12,y+10),(x,y+10)]]}
    tokens=[token('Symbol',0,30),token('mm',90,10),token('Max',60,30),token('Min',120,30),
            token('D',0,55),token('1.6',60,55),token('1.4',120,55)]
    tables=tables_from_ocr(tokens)
    assert len(tables)==1
    values,issues,_=read_table(tables[0],5)
    assert not issues and values['D'].min_mm==1.4 and values['D'].max_mm==1.6
    tokens[-1]['confidence']=.5
    assert read_table(tables_from_ocr(tokens)[0],5)[1][0]['code']=='DIMENSION_CELL_UNREADABLE'
