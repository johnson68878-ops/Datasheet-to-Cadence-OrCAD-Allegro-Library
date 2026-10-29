from datasheet_to_allegro.cadence_compatibility import compatibility_error, session_failure
from datasheet_to_allegro.cadence_compatibility import generated_version_error


def test_unknown_versions_are_not_implicitly_compatible(tmp_path):
    dra=tmp_path/'unknown.dra'
    dra.write_bytes(b'opaque')
    assert compatibility_error(dra,'17.2')
    assert compatibility_error(dra,'unknown')
    assert generated_version_error(dra,'17.2','')


def test_runtime_and_database_must_match_target(tmp_path):
    dra=tmp_path/'part.dra'
    dra.write_bytes(b'ALLEGRO_DESIGN_WAS_LAST_SAVED\0allegro 17.4 P001')
    assert generated_version_error(dra,'17.2','Allegro PCB Editor 17.4 P001')
    dra.write_bytes(b'ALLEGRO_DESIGN_WAS_LAST_SAVED\0allegro 17.2 P001')
    assert generated_version_error(dra,'17.2','Allegro PCB Editor 17.4 P001')
    assert generated_version_error(dra,'17.2','Allegro PCB Editor 17.2 P001') is None


def test_newer_database_is_rejected_before_launch(tmp_path):
    dra = tmp_path / "new.dra"
    dra.write_bytes(b"binary\0ALLEGRO_DESIGN_WAS_LAST_SAVED\0allegro 17.4 P001 - date")
    assert "SPMHOD-29" in compatibility_error(dra, "17.2")
    assert compatibility_error(dra, "17.4") is None
    assert compatibility_error(dra, "23.1") is None


def test_cadence_failure_is_not_a_successful_launch():
    assert "SPMHOD-29" in session_failure("ERROR(SPMHOD-29): Unable to open design")
    assert session_failure("*Error* undefined function: axlFoo")
    assert session_failure("No DRC errors detected. Writing design to disk.") is None
