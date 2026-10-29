import pytest

from datasheet_to_allegro.dimension_tables import number, read_table, read_document_tables, unit

from datasheet_to_allegro.positioned_tables import positioned_tables, horizontal_character

from datasheet_to_allegro.ocr_crosscheck import confirm_numeric_tokens

from datasheet_to_allegro.package_vocabulary import identify_package_families

from datasheet_to_allegro.universal_extractor import PageRecord, _discover

def token(text,x,y,w=12,h=10,confidence=.99):
    return dict(text=text,confidence=confidence,polygon=[[x,y],[x+w,y],[x+w,y+h],[x,y+h]])
