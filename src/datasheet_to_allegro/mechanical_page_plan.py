"""Extraction scheduling only: page priority is never geometry evidence."""
import re


def page_priority(text: str) -> int:
    # A contents entry or prose reference must not outrank an actual drawing.
    lines = [line.strip() for line in text.splitlines()
             if not re.search(r'\.{3,}', line)]
    for priority, pattern in (
        (0, r'(?:recommended\s+(?:land\s+pattern|footprint)|example\s+board\s+layout|land\s+pattern(?:\s+example)?|footprint\s+example|PCB\s+land\s+pattern|推荐焊盘|推薦焊盤)'),
        (1, r'(?:package\s+(?:outline|dimensions?|drawing)|mechanical\s+(?:data|drawing)|封装尺寸|封裝尺寸)'),
        (2, r'(?:pin\s+(?:configuration|map|assignments?)|ball\s+map|pinout)'),
    ):
        if any(re.match(r'^(?:\d+(?:\.\d+)*[.:]?\s+)?' + pattern + r'\b', line, re.I)
               for line in lines):
            return priority
    return 3


def extraction_order(texts: list[str]) -> list[int]:
    """Visit known land/outline pages first, tail first within those classes.

    All pages remain in the plan. Missing titles/image-only drawings therefore
    still reach the existing OCR fallback. Original page numbers stay intact.
    """
    return sorted(range(len(texts)), key=lambda i: (
        page_priority(texts[i]), -i if page_priority(texts[i]) < 2 else i))
