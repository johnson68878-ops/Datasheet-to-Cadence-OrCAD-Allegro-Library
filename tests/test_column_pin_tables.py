from pathlib import Path
import shutil
import pytest
from datasheet_to_allegro.column_pin_tables import read_column_pin_tables, read_ball_map_labels
from datasheet_to_allegro.orcad_library import normalize_electrical_type


@pytest.mark.parametrize('raw,expected', [('IU','Input'),('ID','Input'),('O4','Output'),('I/O','Bidirectional'),('IU/O4','Bidirectional')])
def test_decorated_buffer_types(raw, expected):
    assert normalize_electrical_type(raw) == expected


