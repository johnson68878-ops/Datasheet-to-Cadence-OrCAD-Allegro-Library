from datasheet_to_allegro.ocr_engine import select_languages


def test_language_selection_uses_only_installed_packages():
    assert select_languages('中文封装尺寸', '', {'eng', 'chi_sim'}) == 'eng+chi_sim'
    assert select_languages('', '中文封装尺寸', {'chi_sim'}) == 'chi_sim'
    assert select_languages('English package', '', {'eng', 'chi_sim'}) == 'eng'
    assert select_languages('中文封装尺寸', '', {'eng'}) == 'eng'
    assert select_languages('anything', '', set()) is None


def test_traditional_evidence_prefers_traditional_when_installed():
    assert select_languages('封裝尺寸與驗證', '', {'eng', 'chi_sim', 'chi_tra'}) == 'eng+chi_tra'
