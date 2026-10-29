import pytest
from datasheet_to_allegro.dimension_tables import read_document_tables
from datasheet_to_allegro.universal_extractor import PageRecord


def records(identity='UNRELATED', reference='3', note='3', following=''):
    rows=[['Symbol','mm','','inch('+reference+'.)','',''],
          ['','Min','Max','Min','Max'],['b','0.2','0.3','00079','0.0118']]
    return [PageRecord(10,identity+' mechanical data',tables=[rows]),
            PageRecord(11,'Notes\n'+note+'. Values in inches are converted from mm and rounded to 4 decimal digits.\n'+following)]


@pytest.mark.parametrize('identity,reference',[('ALPHA','3'),('RENAMED','3'),('OTHER','17')])
def test_explicit_reference_preserves_primary_values_and_conflict(identity,reference):
    values,issues=read_document_tables(records(identity,reference,reference))
    assert values[10]['b'].min_mm==.2
    assert len(issues)==1
    assert issues[0]['severity']=='warning'
    assert issues[0]['authority']['footnote']==reference
    assert '00079' in issues[0]['raw']


@pytest.mark.parametrize('fault',['wrong_reference','gap','new_variant','opposite','duplicate','no_notes'])
def test_unbound_or_conflicting_authority_remains_blocking(fault):
    rec=records()
    if fault=='wrong_reference': rec=records(note='4')
    elif fault=='gap': rec[1].number=12
    elif fault=='new_variant': rec[1].text+='\nTable 5. QFN32 mechanical data'
    elif fault=='opposite': rec[1].text+='\nInches are controlling dimensions.'
    elif fault=='duplicate': rec[1].text+='\n3. Values in inches are converted from mm.'
    else: rec[1].text=rec[1].text.replace('Notes','Introduction')
    _,issues=read_document_tables(rec)
    assert issues and all(i.get('severity')!='warning' for i in issues)
