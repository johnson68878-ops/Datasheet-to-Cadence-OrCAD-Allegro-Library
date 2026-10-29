import pytest
from datasheet_to_allegro.dimension_tables import read_table, read_document_tables
from datasheet_to_allegro.universal_extractor import PageRecord, _relevant_dimension_schema_issues
from synthetic_spec import synthetic_spec


ROWS=[["Symbol","mm","","","inch","",""],["","Min","Typ","Max","Min","Typ","Max"],
      ["b1", ".30",".35",".40",".0118",".0128",".0157"]]
DECLARATION="Values in inches are converted from mm and rounded to four decimal digits."


@pytest.mark.parametrize("label", ["QFP32","QFP64"])
def test_declared_primary_unit_on_same_variant_continuation_retains_conflict(label):
    records=[PageRecord(1,label+" Mechanical data",tables=[ROWS]),
             PageRecord(2,label+" Notes\n"+DECLARATION)]
    values,issues=read_document_tables(records)
    assert values[1]["b1"].nominal_mm==.35
    assert issues[0]["code"]=="DIMENSION_UNIT_CONFLICT"
    assert issues[0]["authority"]["page"]==2
    assert issues[0]["raw"]==ROWS[2]
    assert not _relevant_dimension_schema_issues(synthetic_spec(),issues,{1})


def test_no_declaration_or_other_variant_keeps_blocking_unit_conflict():
    for text in ("QFP64 "+DECLARATION, "QFP32 ordinary notes"):
        _,issues=read_document_tables([
            PageRecord(1,"QFP32 Mechanical data",tables=[ROWS]),PageRecord(2,text)])
        assert _relevant_dimension_schema_issues(synthetic_spec(),issues,{1})
    _,issues,_=read_table(ROWS,1,DECLARATION+" Inches control.")
    assert "resolution" not in issues[0]


def test_continuation_cannot_override_explicit_conflicting_primary_unit():
    _, issues = read_document_tables([
        PageRecord(1, "QFP32 Mechanical data. Inches\ncontrol.", tables=[ROWS]),
        PageRecord(2, "QFP32 Notes " + DECLARATION),
    ])
    assert "resolution" not in issues[0]
    assert _relevant_dimension_schema_issues(synthetic_spec(), issues, {1})
    _, issues, _ = read_table(
        ROWS, 1, "Inches govern.", unit_authority={
            "page": 2, "raw_text": DECLARATION, "primary_unit": "mm"})
    assert "resolution" not in issues[0]
