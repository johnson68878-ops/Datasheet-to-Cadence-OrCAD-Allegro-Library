from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Callable

from pypdf import PdfReader


@dataclass(slots=True)
class DocumentEvidenceProfile:
    source_pdf: str
    sha256: str
    page_count: int
    scanned_pages: list[int]
    searchable_text_pages: list[int]
    sparse_or_image_pages: list[int]
    roles: dict[str, list[int]] = field(default_factory=dict)
    package_families: list[str] = field(default_factory=list)
    complete_scan: bool = False
    unreadable_text_pages: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_CACHE: dict[tuple[str, int, int], DocumentEvidenceProfile] = {}
_CACHE_LOCK = Lock()

_ROLE_PATTERNS: dict[str, re.Pattern[str]] = {
    "pin_table": re.compile(r"(?:pin|pad)\s+(?:list|table|function|definitions?|descriptions?)|ball\s+(?:list|table)|引脚(?:说明|功能|表)|引腳(?:說明|功能|表)", re.I),
    "pin_map": re.compile(r"pin\s+configuration|pin\s+map|ball\s+map|引脚分布|引腳分佈|球位图", re.I),
    "package_outline": re.compile(r"package\s+outline|mechanical\s+(?:drawing|data)|封装外形|封裝外形|机械尺寸|機械尺寸", re.I),
    "dimension_table": re.compile(r"dimensions?\s+in\s+(?:millimeters|mm)|package\s+dimensions?|尺寸.*(?:mm|毫米)", re.I),
    "recommended_land_pattern": re.compile(r"recommended\s+(?:land|pad)\s+pattern|land\s+pattern|建议焊盘|建議焊盤", re.I),
    "functional_block_diagram": re.compile(
        r"functional\s+block\s+diagram|block\s+diagram|architecture\s+diagram|"
        r"功能框图|功能方框图|功能方塊圖|架构图|架構圖",
        re.I,
    ),
    "typical_application": re.compile(
        r"typical\s+application|application\s+(?:diagram|circuit)|reference\s+(?:schematic|design)|"
        r"simplified\s+(?:schematic|diagram|application)|"
        r"典型应用|典型應用|参考原理图|參考原理圖|简化(?:版)?原理图|簡化(?:版)?原理圖|"
        r"应用电路|應用電路|电路设计|電路設計",
        re.I,
    ),
    "ordering_information": re.compile(r"ordering\s+information|orderable\s+part|订购信息|訂購資訊", re.I),
    "solder_mask": re.compile(r"solder\s+mask|阻焊", re.I),
    "stencil_paste": re.compile(r"solder\s+paste|stencil|钢网|鋼網", re.I),
}

_FAMILY_PATTERNS: dict[str, re.Pattern[str]] = {
    "BGA": re.compile(r"\b(?:BGA|FBGA|WFBGA|FCBGA|UFBGA|PBGA)\b|ball\s+grid\s+array", re.I),
    "WLCSP": re.compile(r"\b(?:WLCSP|WCSP|CSP)\b|wafer\s+level", re.I),
    "QFN_DFN": re.compile(r"\b(?:QFN|DFN|SON|WSON|VQFN|TQFN)\b", re.I),
    "GULLWING": re.compile(r"\b(?:QFP|LQFP|TQFP|SOIC|SOP|SSOP|TSSOP|MSOP)\b", re.I),
    "LGA": re.compile(r"\bLGA\b|land\s+grid\s+array", re.I),
    "THT_CONNECTOR": re.compile(r"through[- ]hole|\b(?:DIP|PDIP|PGA|CONNECTOR|HEADER|RECEPTACLE)\b", re.I),
}


def _page_has_image_xobject(page: object) -> bool:
    """Detect image-backed pages without decoding their image streams."""

    try:
        resources = page.get("/Resources") or {}  # type: ignore[attr-defined]
        resources = resources.get_object() if hasattr(resources, "get_object") else resources
        xobjects = resources.get("/XObject") or {}
        xobjects = xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
        for value in xobjects.values():
            obj = value.get_object() if hasattr(value, "get_object") else value
            if obj.get("/Subtype") == "/Image":
                return True
    except Exception:
        return False
    return False


def analyze_document_evidence(
    pdf_path: str | Path,
    progress: Callable[[int, int], None] | None = None,
) -> DocumentEvidenceProfile:
    """Inventory every PDF page before either Cadence backend may run."""

    path = Path(pdf_path).resolve()
    stat = path.stat()
    key = (str(path).lower(), stat.st_size, stat.st_mtime_ns)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        if progress:
            progress(cached.page_count, cached.page_count)
        return cached

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)

    scanned: list[int] = []
    searchable: list[int] = []
    sparse: list[int] = []
    unreadable: list[int] = []
    roles = {name: [] for name in _ROLE_PATTERNS}
    family_hits: set[str] = set()
    # pypdf is deliberately used for the all-page inventory: it is much faster
    # than table extraction and leaves the expensive OCR/table pass to only the
    # evidence pages selected by the downstream PCB/OrCAD extractor.
    pages = PdfReader(path).pages
    total = len(pages)
    for index, page in enumerate(pages, start=1):
        text = page.extract_text() or ""
        compact = re.sub(r"\s+", "", text)
        if (sum(ord(c) < 32 and c not in "\r\n\t" for c in text) >= 5
                or len(re.findall(r"\(cid:\d+\)", text)) >= 3 or text.count("\ufffd") >= 3):
            unreadable.append(index)
        scanned.append(index)
        if compact:
            searchable.append(index)
        # Only inspect XObjects on text-light pages. Walking every page's
        # resource tree made large searchable datasheets look frozen even
        # though their text layer was already sufficient for indexing.
        if len(compact) < 80 or (len(compact) < 300 and _page_has_image_xobject(page)):
            sparse.append(index)
        for role, pattern in _ROLE_PATTERNS.items():
            if pattern.search(text):
                roles[role].append(index)
        for family, pattern in _FAMILY_PATTERNS.items():
            if pattern.search(text):
                family_hits.add(family)
        if progress and (index == 1 or index % 5 == 0 or index == total):
            progress(index, total)

    profile = DocumentEvidenceProfile(
        source_pdf=str(path),
        sha256=digest.hexdigest(),
        page_count=len(scanned),
        scanned_pages=scanned,
        searchable_text_pages=searchable,
        sparse_or_image_pages=sparse,
        roles={name: pages for name, pages in roles.items() if pages},
        package_families=sorted(family_hits),
        complete_scan=bool(scanned) and scanned == list(range(1, len(scanned) + 1)),
        unreadable_text_pages=unreadable,
    )
    with _CACHE_LOCK:
        _CACHE[key] = profile
    return profile


def write_document_evidence_manifest(profile: DocumentEvidenceProfile, output_path: str | Path) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def require_complete_document_scan(profile: DocumentEvidenceProfile) -> None:
    if not profile.complete_scan or len(profile.scanned_pages) != profile.page_count:
        raise ValueError(f"PDF整份扫描Gate失败：应读取{profile.page_count}页，实际读取{len(profile.scanned_pages)}页。")
