from __future__ import annotations

import math
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

import pdfplumber

from .models import FootprintSpec, Measurement, RuleStatus, SourceEvidence, UnresolvedItem
from .package_vocabulary import identify_package_families


ARRAY_FAMILIES = {"BGA", "WLCSP", "LGA"}
JEDEC_ROWS = "ABCDEFGHJKLMNPRTUVWY"
PACKAGE_ORDER = ("WLCSP", "BGA", "LGA", "QFN", "DFN", "QFP", "SOIC", "SOT", "DIODE", "TO", "THT", "CONNECTOR")
PACKAGE_PAGE_CUE = re.compile(
    r"package\s+(?:mechanical|outline|drawing|dimension|information)|mechanical\s+(?:data|drawing)|"
    r"封装(?:信息|尺寸|外形|图)|封裝(?:資訊|尺寸|外形|圖)|recommended\s+(?:pcb\s+)?land\s+pattern|"
    r"package\s+outline|outline\s+and\s+dimensions|soldering\s+footprint|example\s+board\s+layout|land\s+pattern\s+example",
    re.I,
)
DIMENSION_PAGE_CUE = re.compile(
    r"\bdimensions?\b|\b(?:symbol|minimum|nominal|maximum)\b|\b(?:min|nom|max)\b|"
    r"\b(?:pitch|terminal|lead|ball|bump|exposed\s+pad|stencil|solder\s+mask)\b|"
    r"\b(?:D2|E2)\b|尺寸|焊盘|钢网|阻焊",
    re.I,
)
OCR_PAGE_CACHE: dict[tuple[str, int, int, int, int], tuple[str, float] | None] = {}
OCR_RENDER_DPI = 600


def _ocr_cache_file(source: Path, page_number: int) -> Path:
    from .ocr_engine import CACHE_SCHEMA, cache_root
    identity = f"{source.resolve()}|{source.stat().st_size}|{source.stat().st_mtime_ns}|{page_number}|{OCR_RENDER_DPI}|{CACHE_SCHEMA}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return cache_root() / f"{digest}.json"


@dataclass
class PageRecord:
    number: int
    text: str
    tables: list[list[list[str | None]]] = field(default_factory=list)
    families: set[str] = field(default_factory=set)
    ocr_confidence: float | None = None
    width_pt: float = 0.0
    height_pt: float = 0.0
    object_counts: dict[str, int] = field(default_factory=dict)
    roles: set[str] = field(default_factory=set)
    ocr_tokens: list[dict] = field(default_factory=list)
    dimension_schema_values: dict[str, Measurement] = field(default_factory=dict)
    dimension_schema_issues: list[dict] = field(default_factory=list)
    located_dimension_tables: list[dict] = field(default_factory=list)
    text_layout_audit: dict = field(default_factory=dict)
    ocr_table_regions: list[dict] = field(default_factory=list)
    perimeter_maps: list[dict] = field(default_factory=list)
    vector_land_patterns: list[dict] = field(default_factory=list)


@dataclass
class PackageCandidate:
    family: str
    page: int
    pin_count: int = 0
    package_code: str | None = None


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split())


def _collapse_doubled_overlay_tokens(value: object) -> str:
    """Collapse tokens emitted twice by overprinted PDF text layers.

    Some vendor PDFs paint every glyph twice, producing tokens such as
    ``11..334477mmmm`` and ``PPIITTCCHH``.  Only tokens whose *every* adjacent
    pair is identical are collapsed, so ordinary words with a doubled letter
    (for example ``ball``) remain untouched.
    """
    collapsed: list[str] = []
    for token in _clean(value).split():
        if len(token) >= 4 and len(token) % 2 == 0 and all(token[index] == token[index + 1] for index in range(0, len(token), 2)):
            token = token[::2]
        elif token.lower() == "xx":
            token = token[0]
        collapsed.append(token)
    return " ".join(collapsed)


def _numbers(value: object) -> list[float]:
    return [float(item) for item in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", _clean(value))]


def _evidence(page: int, label: str, raw: str, method: str = "pdf_text", confidence: float = 0.86, scope: str = "other") -> SourceEvidence:
    return SourceEvidence(
        page=page,
        figure_or_table=label,
        raw_text=_clean(raw)[:800],
        extraction_method=method,
        confidence=confidence,
        evidence_scope=scope,
    )


def _measurement(values: list[float], page: int, label: str, raw: str, method: str = "table_extract", scope: str = "package_outline") -> Measurement | None:
    if not values:
        return None
    ev = [_evidence(page, label, raw, method, 0.92 if method == "table_extract" else 0.82, scope)]
    if len(values) >= 3:
        return Measurement(min_mm=values[0], nominal_mm=values[1], max_mm=values[2], original_value=" / ".join(map(str, values[:3])), original_unit="mm", evidence=ev)
    if len(values) == 2:
        return Measurement(min_mm=values[0], max_mm=values[1], original_value=" / ".join(map(str, values)), original_unit="mm", evidence=ev)
    return Measurement(value_mm=values[0], original_value=str(values[0]), original_unit="mm", evidence=ev)


def _relevant_dimension_schema_issues(
    spec: FootprintSpec, issues: list[dict], used_pages: set[int]
) -> list[dict]:
    # Successful symbols cannot establish that an unreadable row is irrelevant.
    # Until region/variant ownership is independently proved, retain every issue
    # on the selected pages, including symbols that failed extraction entirely.
    return [issue for issue in issues if issue.get("page") in used_pages
            and not (
                issue.get("code") == "DIMENSION_UNIT_CONFLICT"
                and issue.get("resolution") == "explicit_primary_unit"
                and issue.get("authority", {}).get("primary_unit") == "mm"
                and issue.get("severity") == "warning"
            )]


def _min_nom_max_ocr_values(raw: str) -> list[float]:
    """Normalize a strict Min/Nominal/Max OCR triplet.

    Reject damaged digits; arithmetic symmetry is not a source measurement.
    """
    tokens = re.findall(r"(?<![A-Za-z])(\d+(?:\.\d+)?)", _clean(raw))
    values = [float(token) for token in tokens[:3]]
    # An arithmetic progression is not independent OCR evidence. Reject a
    # damaged row instead of inventing its maximum from the other two cells.
    return values if values == sorted(values) and all(0 <= value <= 50 for value in values) else []


def _metric_dimension_values(values: list[float]) -> list[float]:
    """Select the millimetre group from common MM/INCH dual-unit rows."""
    if len(values) >= 2 and len(values) % 2 == 0:
        half = len(values) // 2
        first, second = values[:half], values[half:]
        comparable = [(left, right) for left, right in zip(first, second) if left > 0 and right > 0]
        if comparable:
            first_ratio = sum(left / right for left, right in comparable) / len(comparable)
            second_ratio = sum(right / left for left, right in comparable) / len(comparable)
            if 24.0 <= first_ratio <= 26.0:
                return first
            if 24.0 <= second_ratio <= 26.0:
                return second
    return values[-3:] if len(values) >= 3 else values


def _tesseract_executable() -> Path | None:
    found = shutil.which("tesseract")
    candidates = [Path(found)] if found else []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS")) / "tesseract" / "tesseract.exe")
    candidates += [Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"), Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")]
    return next((path for path in candidates if path.is_file()), None)


def _pdftoppm_executable() -> Path | None:
    found = shutil.which("pdftoppm")
    candidates = [Path(found)] if found else []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS")) / "poppler" / "pdftoppm.exe")
    candidates.append(
        Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" /
        "dependencies" / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe"
    )
    return next((path for path in candidates if path.is_file()), None)


def _run_external(args: list[str], timeout: int = 45) -> subprocess.CompletedProcess[str]:
    """Retry short-lived Windows tool launch races seen with OCR/Poppler binaries."""
    last_error: OSError | None = None
    for attempt in range(3):
        try:
            return subprocess.run(
                args, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout, check=False,
            )
        except OSError as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.2 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _render_ocr_page(page, image_path: Path) -> None:
    """Render an engineering page with Poppler, falling back to PyMuPDF."""
    poppler = _pdftoppm_executable()
    source = Path(page.pdf.stream.name)
    if poppler is not None:
        prefix = image_path.with_suffix("")
        try:
            completed = _run_external(
                [
                    str(poppler), "-f", str(page.page_number), "-l", str(page.page_number),
                    "-singlefile", "-png", "-r", str(OCR_RENDER_DPI), "--", str(source), str(prefix),
                ]
            )
            if completed.returncode == 0 and image_path.is_file() and image_path.stat().st_size > 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        import fitz
        document = fitz.open(source)
        pixmap = document[page.page_number - 1].get_pixmap(matrix=fitz.Matrix(OCR_RENDER_DPI / 72, OCR_RENDER_DPI / 72), alpha=False)
        pixmap.save(image_path)
        document.close()
    except Exception:  # noqa: BLE001 - final optional fallback
        page.to_image(resolution=OCR_RENDER_DPI, antialias=True).original.save(image_path)


def _ocr_package_page(page) -> tuple[str, float] | None:
    """OCR one already-classified package page; never scan the full PDF blindly."""
    source = Path(page.pdf.stream.name).resolve()
    stat = source.stat()
    cache_key = (str(source).lower(), stat.st_size, stat.st_mtime_ns, page.page_number, OCR_RENDER_DPI)
    if cache_key in OCR_PAGE_CACHE:
        return OCR_PAGE_CACHE[cache_key]
    cache_file = _ocr_cache_file(source, page.page_number)
    if cache_file.is_file():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            outcome = (str(cached["text"]), float(cached["confidence"])) if cached.get("text") else None
            OCR_PAGE_CACHE[cache_key] = outcome
            return outcome
        except (OSError, ValueError, TypeError, KeyError):
            pass
    with tempfile.TemporaryDirectory(prefix="ds2a_ocr_") as directory:
        image_path = Path(directory) / "page.png"
        _render_ocr_page(page, image_path)
        from .ocr_crosscheck import recognize_image, compare_numeric_evidence
        from .ocr_engine import CACHE_SCHEMA, installed_languages, rapidocr_confidence, select_languages
        try:
            secondary = recognize_image(image_path)
        except Exception as exc:
            secondary = {"engine": "rapidocr_onnxruntime", "text": "", "tokens": [], "error": str(exc)}
        executable = _tesseract_executable()
        language = (
            select_languages(
                page.extract_text(x_tolerance=2, y_tolerance=2) or "",
                secondary.get("text", ""),
                installed_languages(str(executable)),
            )
            if executable else None
        )

        def run_tesseract(psm: str, tsv: bool = False):
            if executable is None or language is None:
                return None
            args = [str(executable), str(image_path), "stdout", "-l", language, "--psm", psm]
            if tsv:
                args.append("tsv")
            try:
                completed = _run_external(args)
            except (OSError, subprocess.SubprocessError):
                return None
            return completed if completed.returncode == 0 else None

        primary = run_tesseract("3")
        alternate = run_tesseract("4")
        sparse = run_tesseract("11")
        confidence_result = run_tesseract("3", tsv=True)

    successful = [item for item in (primary, alternate, sparse) if item and item.stdout.strip()]
    if not successful:
        text = secondary.get("text", "").strip()
        outcome = (text, rapidocr_confidence(secondary.get("tokens", []))) if text else None
        crosscheck = {
            "status": "single_engine_review" if text else "unavailable",
            "primary_only": [],
            "secondary_only": [],
            "note": "RapidOCR-only recovery is capped at 0.80 and is not cross-checked evidence.",
        }
        OCR_PAGE_CACHE[cache_key] = outcome
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({
                "schema": CACHE_SCHEMA,
                "text": outcome[0] if outcome else "",
                "confidence": outcome[1] if outcome else 0.0,
                "language": language,
                "secondary": secondary,
                "crosscheck": crosscheck,
            }, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return outcome
    confidences: list[float] = []
    for raw in (confidence_result.stdout.splitlines()[1:] if confidence_result else []):
        fields = raw.split("\t", 11)
        if len(fields) != 12 or not fields[11].strip():
            continue
        try:
            confidence = float(fields[10])
        except ValueError:
            confidence = -1
        if confidence >= 0:
            confidences.append(confidence)
    # Engineering drawings often place a dimension value, its multiplier and
    # tolerance on separate baselines. Sparse-text mode recovers those labels
    # much more reliably than paragraph modes, so all three views are retained
    # for the later semantic binding pass.
    primary_text = primary.stdout.strip() if primary else successful[0].stdout.strip()
    alternate_text = alternate.stdout.strip() if alternate else ""
    sparse_text = sparse.stdout.strip() if sparse else ""
    text = (primary_text + "\n[OCR ALTERNATE LAYOUT]\n" + alternate_text
            + "\n[OCR SPARSE DIMENSIONS]\n" + sparse_text).strip()
    outcome = (text, round(sum(confidences) / max(1, len(confidences)) / 100, 3)) if text else None
    crosscheck = compare_numeric_evidence(primary_text, secondary)
    if outcome and crosscheck["status"] != "agree":
        # Keep primary text separate. Never append contradictory numbers into
        # the dimension extractor or call agreement a semantic proof.
        outcome = (outcome[0], min(outcome[1], 0.89))
    OCR_PAGE_CACHE[cache_key] = outcome
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps({"schema": CACHE_SCHEMA, "text": outcome[0], "confidence": outcome[1],
                        "language": language, "secondary": secondary,
                        "crosscheck": crosscheck}, ensure_ascii=False) if outcome else json.dumps({"schema": CACHE_SCHEMA, "text": "", "secondary": secondary}),
            encoding="utf-8",
        )
    except OSError:
        pass
    return outcome


def _ocr_table_regions(page, tokens):
    from .dimension_tables import symbol_header
    from .ocr_engine import installed_languages, select_languages
    if not any(symbol_header(t.get('text','')) or t.get('text','').upper() in {'UNIT','PARAMETER'} or re.fullmatch(r'symbo[il1]',t.get('text',''),re.I) for t in tokens):
        return []
    source=Path(page.pdf.stream.name).resolve()
    executable=_tesseract_executable()
    language=select_languages(
        page.extract_text(x_tolerance=2,y_tolerance=2) or '',
        '\n'.join(t.get('text','') for t in tokens),
        installed_languages(str(executable)),
    ) if executable else None
    # Region inputs and language policy affect the evidence. Do not replay
    # the old English-only result or a crop proposed by different tokens.
    region_identity=json.dumps({'tokens':tokens,'language':language,'executable':str(executable)},
                               sort_keys=True,ensure_ascii=False)
    region_digest=hashlib.sha256(region_identity.encode('utf-8')).hexdigest()
    cache=_ocr_cache_file(source,page.page_number).with_suffix(f'.tables-v10-{region_digest}.json')
    try:
        return json.loads(cache.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        pass
    from .ocr_crosscheck import recognize_table_regions
    with tempfile.TemporaryDirectory(prefix='ds2a_regions_') as directory:
        image=Path(directory)/'page.png'
        _render_ocr_page(page,image)
        regions=recognize_table_regions(image,tokens,executable,language=language)
    try:
        cache.parent.mkdir(parents=True,exist_ok=True)
        # Transient engine failures must be retried on the next invocation.
        if not any(r.get('error') or r.get('independent_errors') for r in regions):
            cache.write_text(json.dumps(regions,ensure_ascii=False),encoding='utf-8')
    except OSError:
        pass
    return regions


def _read_pages(path: Path, progress: Callable[[int, int], None] | None = None) -> list[PageRecord]:
    """Build a page-complete Document IR before semantic package binding.

    Phase one inventories text and PDF objects for every page. Phase two OCRs
    sparse image pages and all pages around a package/mechanical cue. This
    prevents an image-only drawing from being skipped merely because its text
    layer did not contain a package-family keyword.
    """
    records: list[PageRecord] = []
    with pdfplumber.open(path) as pdf:
        raw_pages: list[tuple[str, dict[str, int]]] = []
        cue_pages: set[int] = set()
        mechanical_ranges: set[int] = set()
        for index, page in enumerate(pdf.pages):
            text = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
            object_counts = {name: len(items) for name, items in page.objects.items()}
            raw_pages.append((text, object_counts))
            page_number = index + 1
            if PACKAGE_PAGE_CUE.search(text) or re.search(
                r"pin\s+configuration|pin\s+map|ball\s+map|管脚分布", text, re.I
            ):
                cue_pages.add(page_number)
            if re.search(
                r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\s+)?mechanical\s+data\b|package\s+option\s+addendum",
                text,
                re.I,
            ):
                mechanical_ranges.update(range(page_number, min(len(pdf.pages), page_number + 4) + 1))

        neighbor_pages = {
            nearby
            for cue in cue_pages
            for nearby in range(max(1, cue - 2), min(len(pdf.pages), cue + 3) + 1)
        }
        from .mechanical_page_plan import extraction_order
        ordered_indices = extraction_order([text for text, _ in raw_pages])
        for completed, index in enumerate(ordered_indices, 1):
            page = pdf.pages[index]
            text, object_counts = raw_pages[index]
            # Keep the original text in the audit, including vertical labels.
            # Horizontal table binding must not concatenate a diagonal watermark.
            # Keep the full semantic/pin text channel unchanged. Vertical pin
            # labels are not noise; only dimension-table glyph reading is filtered.
            # Table extraction is intentionally limited to package/pin pages. This
            # avoids the former UI freeze caused by running it on every PDF page.
            families = set(identify_package_families(text))
            page_number = index + 1
            in_mechanical_context = page_number in mechanical_ranges
            dimension_candidate = bool(DIMENSION_PAGE_CUE.search(text))
            interesting = bool(
                PACKAGE_PAGE_CUE.search(text)
                or re.search(r"pin\s+configuration|pin\s+map|ball\s+map|管脚分布", text, re.I)
                or re.search(r"pin\s+list.*function\s+table", text, re.I)
                or (families and dimension_candidate)
                or page_number in neighbor_pages
                or in_mechanical_context
            )
            ocr_confidence = None
            ocr_tokens = []
            # OCR every sparse raster page, even when the text layer provides no
            # package keyword. This is the generic path for scanned drawings.
            sparse_raster_page = object_counts.get("image", 0) > 0 and len(_clean(text)) < 350
            drawing_primitives = object_counts.get("line", 0) + object_counts.get("curve", 0)
            sparse_vector_page = drawing_primitives >= 100 and len(_clean(text)) < 350
            vector_dimension_drawing = bool(families) and dimension_candidate and (
                drawing_primitives >= 100 or object_counts.get("image", 0) >= 20
            )
            # Long handling notes can coexist with an image-only dimension
            # drawing. Text length alone must not suppress OCR of that figure.
            image_package_figure = object_counts.get("image", 0) > 0 and any(
                re.search(r"^\s*Figure\s+\d+[.:].*package\s+(?:drawing|outline|dimensions?)\s*$", line, re.I)
                and not re.search(r"\.{3,}", line)
                for line in text.splitlines()
            )
            needs_ocr = (interesting and (in_mechanical_context or len(_clean(text)) < 350)) or sparse_raster_page or sparse_vector_page or vector_dimension_drawing or image_package_figure
            geometry_cue = re.search(r'\bpinout\b|\bpin\s+assignments?\b|\bfootprint\s+example\b|\brecommended\s+(?:land\s+pattern|footprint)\b', text, re.I)
            if not interesting and not needs_ocr and not geometry_cue and not dimension_candidate:
                # Keep every native text page for identity, variant scope and
                # conflicting declarations. No downstream consumer uses the
                # filtered vector channel on these non-table/non-drawing pages.
                records.append(PageRecord(
                    page_number, text, families=families,
                    width_pt=float(page.width), height_pt=float(page.height),
                    object_counts=object_counts,
                    text_layout_audit={'extraction_sequence': completed,
                                       'processing': 'native_text_inventory'}))
                if progress and (completed == 1 or completed % 5 == 0 or completed == len(pdf.pages)):
                    progress(completed, len(pdf.pages))
                continue
            from .positioned_tables import vector_channel, positioned_tables
            horizontal, vector_tokens, separated_chars = vector_channel(page)
            horizontal_text = horizontal.extract_text(x_tolerance=2, y_tolerance=2) or ''
            if needs_ocr:
                ocr = _ocr_package_page(page)
                if ocr:
                    ocr_text, ocr_confidence = ocr
                    text = text + "\n[OCR PACKAGE PAGE]\n" + ocr_text
                    families.update(identify_package_families(ocr_text))
                    try:
                        cached = json.loads(_ocr_cache_file(path.resolve(), page_number).read_text(encoding="utf-8"))
                        ocr_tokens = cached.get("secondary", {}).get("tokens", [])
                    except (OSError, ValueError, TypeError):
                        pass
            tables = horizontal.extract_tables() if interesting else []
            located_tables = positioned_tables(vector_tokens, method='table_extract') if interesting else []
            ocr_regions=[]
            if ocr_tokens:
                # Prefer glyph tables where present; OCR is a separate fallback
                # channel, not duplicated/contradictory rows in the same table.
                if not located_tables:
                    try:
                        ocr_regions=_ocr_table_regions(page,ocr_tokens)
                    except Exception as exc:  # Optional second look; preserve failure evidence.
                        ocr_regions=[{'error':str(exc)}]
                    from .dimension_tables import read_table
                    channels=[positioned_tables(ocr_tokens,method='ocr'),[]]
                    for region in ocr_regions:
                        channels[1].extend(positioned_tables(region.get('tokens',[]),method='ocr'))
                    def channel_quality(tables):
                        results=[read_table(t['rows'],page_number,text) for t in tables]
                        return (sum(len(v) for v,_,_ in results),-sum(len(e) for _,e,_ in results))
                    located_tables=max(channels,key=channel_quality)
                    # Both readings remain available; disagreements in accepted
                    # numeric rows are visible and block downstream generation.
                    accepted=[]
                    def overlaps(first,second):
                        a,b=first['bbox'],second['bbox']
                        intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
                        minimum=min((a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1]))
                        return minimum>0 and intersection/minimum>.6
                    for channel in channels:
                        for table in channel:
                            values,_,_=read_table(table['rows'],page_number,text)
                            for name,value in values.items():
                                signature=value.model_dump(exclude={'evidence','original_value','original_unit'})
                                if any(name==prior_name and overlaps(table,prior_table) and signature!=prior_signature
                                       for prior_table,prior_name,prior_signature in accepted):
                                    table.setdefault('channel_conflicts',[]).append(name)
                                    for selected in located_tables:
                                        selected.setdefault('channel_conflicts',[]).append(name)
                                accepted.append((table,name,signature))
            if located_tables:
                from .dimension_tables import read_table
                located_tables = [t for t in located_tables if read_table(t['rows'],page_number,text)[2] is not None]
            roles: set[str] = set()
            if re.search(r"package\s+outline|mechanical\s+(?:data|drawing)", text, re.I):
                roles.add("package_outline")
            if re.search(r"example\s+board\s+layout|land\s+pattern", text, re.I):
                roles.add("land_pattern")
            if re.search(r"example\s+stencil\s+design|solder\s+paste", text, re.I):
                roles.add("stencil")
            if re.search(r"pin\s+configuration|pin\s+map|ball\s+map", text, re.I):
                roles.add("pin_map")
            if in_mechanical_context:
                roles.add("mechanical_section")
            if dimension_candidate or (ocr_confidence is not None and DIMENSION_PAGE_CUE.search(text)):
                roles.add("dimension_candidate")
            from .perimeter_topology import oriented_words, read_perimeter_maps, read_dual_maps
            perimeter_maps = (
                read_perimeter_maps(oriented_words(page), float(page.height))
                if re.search(r"\bpinout\b|\bpin\s+assignments?\b", text, re.I)
                else []
            )
            if re.search(r'\bpin\s+configuration\b', text, re.I):
                perimeter_maps.extend(read_dual_maps(oriented_words(page), float(page.height)))
            from .dimension_graph import read_vector_land_patterns
            vector_land_patterns = (
                read_vector_land_patterns(page)
                if re.search(r"\bfootprint\s+example\b|\brecommended\s+(?:land\s+pattern|footprint)\b", text,re.I)
                else []
            )
            records.append(PageRecord(
                page_number, text, tables, families, ocr_confidence,
                float(page.width), float(page.height),
                object_counts, roles, ocr_tokens,
                located_dimension_tables=located_tables,
                text_layout_audit={'separated_glyphs':separated_chars,'horizontal_text':horizontal_text if separated_chars else None,
                                   'extraction_sequence': completed, 'processing': 'deep_evidence'},
                ocr_table_regions=ocr_regions,
                perimeter_maps=perimeter_maps,
                vector_land_patterns=vector_land_patterns,
            ))
            if progress and (completed == 1 or completed % 5 == 0 or completed == len(pdf.pages)):
                progress(completed, len(pdf.pages))
    # Cross-page table inheritance and variant binding require document order,
    # not the extraction work queue's priority order.
    return sorted(records, key=lambda record: record.number)


def _pin_map_shape(record: PageRecord) -> tuple[int, list[str], int] | None:
    for table in record.tables:
        if not table:
            continue
        header_numbers = [int(n) for n in re.findall(r"\b\d{1,2}\b", " ".join(_clean(cell) for cell in table[0]))]
        if len(header_numbers) < 2:
            continue
        columns = max(header_numbers)
        rows: list[str] = []
        for row in table[1:]:
            first = next((_clean(cell) for cell in row if _clean(cell)), "")
            occupied = sum(bool(_clean(cell)) for cell in row)
            if first in JEDEC_ROWS and occupied >= 3 and first not in rows:
                rows.append(first)
        if rows and columns:
            return len(rows) * columns, rows, columns
    return None


def _pin_map_topology(record: PageRecord, expected_count: int = 0) -> tuple[list[str], int, list[str]] | None:
    """NC/DNU are electrical states, not missing physical balls.

    Only a physical absence label or an explicit source legend permits a hole.
    """
    absent_nc = bool(re.search(r"(?:\bNC\b|no\s*connects?)[^.]{0,200}(?:not\s+populated|no\s+balls?)", _clean(record.text), re.I))
    for table in record.tables:
        if not table:
            continue
        header_index = -1
        column_cells: list[tuple[int, int]] = []
        for index, header in enumerate(table[:4]):
            numbered = [
                (cell_index, int(value))
                for cell_index, cell in enumerate(header)
                for value in re.findall(r"^\s*(\d{1,2})\s*$", str(cell or ""))
            ]
            numbers = [value for _cell_index, value in numbered]
            if len(numbers) >= 3 and numbers == list(range(1, max(numbers) + 1)):
                header_index = index
                column_cells = numbered
                break
        if header_index < 0 or not column_cells:
            continue
        rows: list[str] = []
        missing: list[str] = []
        for table_row in table[header_index + 1:]:
            row_name = next((_clean(cell).upper() for cell in table_row[:column_cells[0][0]] if len(_clean(cell)) == 1 and _clean(cell).upper() in JEDEC_ROWS), "")
            if not row_name or row_name in rows:
                continue
            rows.append(row_name)
            for cell_index, column in column_cells:
                value = _clean(table_row[cell_index] if cell_index < len(table_row) else "").upper()
                if re.fullmatch(r"NO\s*BALL|UNPOPULATED|NOT\s*POPULATED", value) or (absent_nc and re.fullmatch(r"N\s*/?\s*C|NO\s*CONNECT", value)):
                    missing.append(f"{row_name}{column}")
        columns = max(value for _cell_index, value in column_cells)
        occupied = len(rows) * columns - len(missing)
        if rows and (not expected_count or occupied == expected_count):
            return rows, columns, missing
    return None


def _drawing_pin_map_shape(record: PageRecord, expected_count: int = 0) -> tuple[int, list[str], int] | None:
    """Read a JEDEC grid printed as drawing text rather than a PDF table."""
    # Axis labels belong to the drawing region.  Dimension-table symbols and a
    # second OCR transcript on the same page must not become extra JEDEC rows.
    drawing_text = record.text.split("[OCR PACKAGE PAGE]", 1)[0]
    drawing_text = re.split(
        r"\b(?:CONTROLLING\s+DIMENSION|DIMENSION\s+DIMENSION|SYMBOL\s*\n\s*(?:\(MM\)|MILLIMETERS?))\b",
        drawing_text,
        maxsplit=1,
        flags=re.I,
    )[0]
    lines = [_clean(line) for line in drawing_text.splitlines() if _clean(line)]
    column_count = 0
    small_land_grid = bool(
        expected_count and re.search(r'example\s+board\s+layout|land\s+pattern\s+example', drawing_text, re.I)
        and re.search(rf'\b{expected_count}\s*X\b', drawing_text, re.I)
    )
    for line in lines:
        numbers = [int(item) for item in re.findall(r"\b\d{1,2}\b", line)]
        if len(numbers) >= 3 or (small_land_grid and re.fullmatch(r'\s*(?:1\s+2|2\s+1)\s*', line)):
            maximum = max(numbers)
            ascending = list(range(1, maximum + 1))
            if numbers == ascending or numbers == list(reversed(ascending)):
                column_count = max(column_count, maximum)
    if not column_count:
        # Image-only die-size arrays often lose axis characters in OCR while
        # preserving several rows of ball circles.  Accept the raster-text grid
        # only when independent count, pitch, span, A1 and package-title evidence
        # agree.  A bare count is never factorised.
        token_text = "\n".join(str(item.get("text", "")) for item in record.ocr_tokens)
        combined = record.text + "\n" + token_text
        holes_declared = re.search(
            r"\b(?:NO\s+BALL|UNPOPULATED|NOT\s+POPULATED|DEPOPULATED|MISSING\s+BALL|DNP)\b",
            combined,
            re.I,
        )
        repeated_count = re.search(rf"\b{expected_count}\s*X\b", combined, re.I) if expected_count else None
        pitch_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:MM\s*)?(?:TYP|PITCH)\b", combined, re.I)
        span_values = [float(value) for value in re.findall(r"(?<![\d.])(\d+(?:\.\d+))(?![\d.])", token_text)]
        grid_lines: list[int] = []
        for line in record.text.splitlines():
            compact = re.sub(r"[^A-Z0-9]", "", line.upper())
            circular = len(re.findall(r"[O0QCDG]", compact))
            if circular >= 4 and circular >= len(compact) - 2:
                grid_lines.append(circular)
        if (
            expected_count
            and not holes_declared
            and repeated_count
            and pitch_match
            and re.search(r"\b(?:DIE\s+SIZE\s+)?BALL\s+GRID\s+ARRAY\b", combined, re.I)
            and re.search(r"\bBALL\s+A1\b.{0,40}\bCORNER\b|\bA1\s+CORNER\b", combined, re.I | re.S)
            and len(grid_lines) >= 3
        ):
            pitch_value = float(pitch_match.group(1))
            columns = max(grid_lines)
            rows = expected_count // columns if columns and expected_count % columns == 0 else 0
            span_proves_columns = any(
                abs(value / pitch_value - (columns - 1)) <= 0.05
                for value in span_values
                if pitch_value > 0
            )
            if rows >= 2 and span_proves_columns:
                return expected_count, list(JEDEC_ROWS[:rows]), columns
        return None
    visible_rows = []
    # Function labels can share the row-axis line. Require an independently
    # declared complete ball-map caption and isolate its preceding grid block.
    caption = re.search(rf'[^\n]*\b{expected_count}\s*[- ]?BALLS?\b[^\n]*\bTOP\s+VIEW\b', drawing_text, re.I) if expected_count else None
    if caption:
        block = drawing_text[:caption.start()]
        headers = list(re.finditer(r'(?m)^\s*1(?:\s+\d+){2,}\s*$', block))
        if headers:
            block = block[headers[-1].end():]
            axis_rows = re.findall(r'(?m)^\s*([A-Z])\s+\S+[^\n]*', block)
            if (axis_rows == list(JEDEC_ROWS[:len(axis_rows)])
                    and len(axis_rows)*column_count == expected_count
                    and not re.search(r'NO\s+BALL|UNPOPULATED|DEPOPULATED|MISSING\s+BALL|DNP', drawing_text, re.I)):
                return expected_count, axis_rows, column_count
    for line in lines:
        if line in JEDEC_ROWS and line not in visible_rows:
            visible_rows.append(line)
    if len(visible_rows) >= 2:
        rows = [row for row in JEDEC_ROWS if row in visible_rows]
        count = len(rows) * column_count
        if not expected_count or count == expected_count:
            return count, rows, column_count

    # PDF drawing text is frequently emitted in paint order: one row label can
    # be merged into a dimension line even though the two axes, declared ball
    # count and A1 index are all explicit.  Recover only that narrowly proven
    # rectangular case.  This is intentionally stricter than factorising a pin
    # count: a package with holes, depopulated balls or no labelled drawing must
    # remain unresolved.
    if expected_count and expected_count % column_count == 0:
        row_count = expected_count // column_count
        expected_rows = list(JEDEC_ROWS[:row_count])
        normalized = _clean(record.text)
        declared_count = re.search(
            rf"\b{expected_count}\s*[- ]?(?:BALL|BUMP)S?\b|"
            rf"\b(?:BALL|BUMP)S?\s*(?:COUNT)?\s*[:=]?\s*{expected_count}\b",
            normalized,
            re.I,
        )
        view_and_index = (
            re.search(r"\b(?:TOP|BOTTOM)\s+VIEW\b", normalized, re.I)
            and re.search(r"\b(?:PIN\s*)?A1\s+(?:PIN\s*)?INDEX\b|\bA1\s+CORNER\b", normalized, re.I)
        )
        holes_declared = re.search(
            r"\b(?:NO\s+BALL|UNPOPULATED|NOT\s+POPULATED|DEPOPULATED|MISSING\s+BALL|DNP)\b",
            normalized,
            re.I,
        )
        visible = set(visible_rows)
        almost_complete_axis = (
            row_count >= 2
            and set(expected_rows[:1] + expected_rows[-1:]) <= visible
            and visible <= set(expected_rows)
            and len(visible) >= row_count - 1
        )
        if declared_count and view_and_index and not holes_declared and almost_complete_axis:
            return expected_count, expected_rows, column_count
    return None


def _explicit_coordinate_shape(record: PageRecord, expected_count: int = 0) -> tuple[int, list[str], int] | None:
    """Recover a complete array map from explicit A1/B2-style coordinates.

    Small CSP datasheets frequently draw the pin map as vector text instead of
    a real PDF table. Only accept a gap-free rectangular coordinate set whose
    occupied count agrees with the declared ball count; isolated dimension
    symbols such as D1/E1 therefore cannot form a topology.
    """
    coordinates = {
        (row, int(column))
        for row, column in re.findall(r"\b([A-Z]{1,2})(\d{1,2})\b", record.text.upper())
        if row in JEDEC_ROWS and 1 <= int(column) <= 99
    }
    if not coordinates:
        return None
    rows = [row for row in JEDEC_ROWS if any(item[0] == row for item in coordinates)]
    columns = sorted({column for _row, column in coordinates})
    if not rows or columns != list(range(1, max(columns) + 1)):
        return None
    if not expected_count and (len(rows) < 2 or len(columns) < 2):
        return None
    expected_coordinates = {(row, column) for row in rows for column in columns}
    if coordinates != expected_coordinates:
        return None
    count = len(expected_coordinates)
    if expected_count and count != expected_count:
        return None
    return count, rows, max(columns)


def _drawing_range(record: PageRecord, symbol: str) -> Measurement | None:
    pattern = re.compile(
        rf"\b{re.escape(symbol)}\s*:\s*Max\s*=\s*(\d+(?:\s*\.\s*\d+)?)\s*mm\s*,\s*Min\s*=\s*(\d+(?:\s*\.\s*\d+)?)\s*mm",
        re.I,
    )
    match = pattern.search(record.text)
    if not match:
        # OCR may corrupt the literal ``Max =`` label while preserving the
        # final maximum immediately before ``mm, Min =``. Bind only that strict
        # suffix; unrelated drawing numbers cannot satisfy the full sequence.
        pattern = re.compile(
            rf"\b{re.escape(symbol)}\s*:.{{0,80}}?((?:\d+)?\s*\.\s*\d+)\s*mm\s*,\s*Min\s*=\s*((?:\d+)?\s*\.\s*\d+)\s*mm",
            re.I,
        )
        match = pattern.search(record.text)
    if not match:
        return None
    normalize = lambda value: float(re.sub(r"\s+", "", value))
    maximum, minimum = normalize(match.group(1)), normalize(match.group(2))
    raw = _clean(match.group(0))
    return Measurement(
        min_mm=minimum,
        max_mm=maximum,
        original_value=raw,
        original_unit="mm",
        evidence=[_evidence(record.number, "Package Outline dimension note", raw, "pdf_text", 0.94, "package_outline")],
    )


def _drawing_max_height(records: list[PageRecord], pages: list[int]) -> Measurement | None:
    """Bind package height only when a drawing explicitly says MAX/seating plane.

    This avoids treating a positional tolerance such as ``0.015 C A B`` as
    symbol A/package height on dense WLP drawings.
    """
    patterns = (
        re.compile(r"\b(\d+(?:\.\d+)?)\s*MAX\b.{0,80}?\bSEATING\s+PLANE\b", re.I | re.S),
        re.compile(r"\bA\s*(?:MAX\s*)?[:=]?\s*(\d+(?:\.\d+)?)\s*(?:mm\s*)?MAX\b", re.I),
    )
    for record in records:
        if record.number not in pages:
            continue
        for pattern in patterns:
            match = pattern.search(record.text)
            if not match:
                continue
            maximum = float(match.group(1))
            raw = _clean(match.group(0))
            return Measurement(
                max_mm=maximum,
                original_value=raw,
                original_unit="mm",
                evidence=[_evidence(record.number, "Package drawing maximum height", raw, "pdf_text", 0.96, "package_outline")],
            )
    return None


def _ocr_symbol_measurement(records: list[PageRecord], page: int, marker: str, symbol: str, scope: str) -> Measurement | None:
    record = next((item for item in records if item.number == page and item.ocr_confidence is not None), None)
    if record is None:
        return None
    start = record.text.rfind(marker)
    section = record.text[start:] if start >= 0 else record.text
    next_header = re.search(rf"\b(?:QFN|DFN|BGA|WLCSP|LGA|SOIC|QFP)\s*\d+\b", section[len(marker):], re.I)
    if next_header:
        section = section[:len(marker) + next_header.start()]
    lines = [_clean(line) for line in section.splitlines() if _clean(line)]
    symbols = {"A", "A1", "b", "b1", "b2", "b3", "c", "D", "D1", "D2", "E", "E1", "E2", "e", "L", "L1", "K", "K1", "h"}
    for index, line in enumerate(lines):
        if line != symbol:
            continue
        captured: list[float] = []
        raw_lines = [line]
        for following in lines[index + 1:index + 8]:
            if following in symbols:
                break
            if re.search(r'SEATING\s+PLANE|BALL\s+TYP|\bMAX\b', following, re.I):
                break  # A new drawing annotation is not this symbol's table.
            raw_lines.append(following)
            captured.extend(float(item) for item in re.findall(r"\b\d+\.\s*\d+\b", following.replace(" ", "")))
            if len(captured) >= 3:
                break
        values = [value for value in captured if 0 < value < 100][:3]
        if len(values) == 3 and not values[0] <= values[1] <= values[2]:
            endpoint_integer = math.floor(values[0])
            repaired_middle = endpoint_integer + (values[1] - math.floor(values[1]))
            if math.floor(values[2]) == endpoint_integer and values[0] <= repaired_middle <= values[2]:
                values = [values[0], round(repaired_middle, 6), values[2]]
        if values:
            found = _measurement(values, record.number, f"OCR {marker} dimension table", " ".join(raw_lines), "ocr", scope)
            if found:
                evidence = [item.model_copy(update={"confidence": record.ocr_confidence or 0.0}) for item in found.evidence]
                return found.model_copy(update={"evidence": evidence})
    return None


def _ocr_variant_table_measurement(
    records: list[PageRecord], page: int, family: str, pin_count: int,
    symbol: str, scope: str,
) -> Measurement | None:
    """Read one symbol from the table belonging to a specific package variant.

    A single mechanical page may contain several QFN/BGA options.  The section
    boundary is derived from family+pin-count headings, never from a part name.
    Partial MIN/NOM/MAX rows retain only columns that OCR actually recovered.
    """
    record = next((item for item in records if item.number == page and item.ocr_confidence is not None), None)
    if record is None or not pin_count:
        return None
    compact_body = r"(?:\s*\d+(?:\.\d+)?\s*[xX]\s*\d+(?:\.\d+)?)?"
    header = re.compile(
        rf"\b{re.escape(family)}{compact_body}[-_ ]?0*{pin_count}\b", re.I
    )
    next_header = re.compile(
        rf"\b{re.escape(family)}{compact_body}[-_ ]?\d{{1,4}}\b", re.I
    )
    candidates: list[tuple[list[float], str]] = []
    for found in header.finditer(record.text):
        section = record.text[found.end():]
        following = next_header.search(section)
        if following:
            section = section[:following.start()]
        table_header = re.search(r"\bSYMBOL\b.{0,120}?\bMIN\b.{0,80}?\bNOM\b.{0,80}?\bMAX\b", section, re.I | re.S)
        if table_header:
            section = section[table_header.end():]
        lines = [_clean(line) for line in section.splitlines() if _clean(line)]
        for index, line in enumerate(lines):
            if not re.match(rf"^{re.escape(symbol)}(?:\s|\||$)", line):
                continue
            raw_lines = [line]
            numeric_line = re.sub(r"(?<=\d)\.\s*[lLI](?=\d)", ".1", line)
            numeric_line = re.sub(r"(?<=\.\d)/(?=\d)", "7", numeric_line)
            numeric_line = re.sub(r"(?<![A-Za-z])O\s*\.(?=\d)", "0.", numeric_line, flags=re.I)
            numeric_line = re.sub(r"(?<![A-Za-z])Q\s*\.\s*(?=\d)", "0.", numeric_line, flags=re.I)
            numeric_line = re.sub(r"(?<![\d.])0(\d{3})(?!\d)", r"0.\1", numeric_line)
            values = [float(re.sub(r"\s+", "", value)) for value in re.findall(r"\d+\.\s*\d+", numeric_line)]
            if not values and line == symbol:
                for following_line in lines[index + 1:index + 7]:
                    if re.fullmatch(r"(?:A\d?|b\d?|c|D\d?|E\d?|e\d?|L\d?|K\d?|h|R)", following_line):
                        break
                    raw_lines.append(following_line)
                    normalized = re.sub(r"(?<=\d)\.\s*[lLI](?=\d)", ".1", following_line)
                    normalized = re.sub(r"(?<=\.\d)/(?=\d)", "7", normalized)
                    normalized = re.sub(r"(?<![A-Za-z])O\s*\.(?=\d)", "0.", normalized, flags=re.I)
                    normalized = re.sub(r"(?<![A-Za-z])Q\s*\.\s*(?=\d)", "0.", normalized, flags=re.I)
                    normalized = re.sub(r"(?<![\d.])0(\d{3})(?!\d)", r"0.\1", normalized)
                    values.extend(float(re.sub(r"\s+", "", value)) for value in re.findall(r"\d+\.\s*\d+", normalized))
                    # Dual-unit tables commonly place MM MIN/MAX followed by
                    # INCH MIN/MAX on separate OCR lines. Keep all four values
                    # before selecting the millimetre pair.
                    if len(values) >= 4:
                        break
                if not values:
                    preceding_lines = lines[max(0, index - 4):index]
                    preceding_values: list[float] = []
                    for preceding_line in preceding_lines:
                        normalized = re.sub(r"(?<=\d)\.\s*[lLI](?=\d)", ".1", preceding_line)
                        normalized = re.sub(r"(?<=\.\d)/(?=\d)", "7", normalized)
                        normalized = re.sub(r"(?<![A-Za-z])Q\s*\.\s*(?=\d)", "0.", normalized, flags=re.I)
                        preceding_values.extend(float(re.sub(r"\s+", "", value)) for value in re.findall(r"\d+\.\s*\d+", normalized))
                    if preceding_values:
                        values = preceding_values[-4:]
                        raw_lines = preceding_lines + raw_lines
            upper_limit = 2.0 if symbol in {"e", "e1"} else 100.0
            values = _metric_dimension_values([value for value in values if 0 < value < upper_limit])[:3]
            if values:
                candidates.append((values, " | ".join(raw_lines)))
    if not candidates:
        return None
    # Prefer a complete monotonic MIN/NOM/MAX row, then the most complete row.
    candidates.sort(
        key=lambda item: (
            (
                len(item[0]) == 1
                or (len(item[0]) == 2 and item[0][0] <= item[0][1])
                or (len(item[0]) == 3 and item[0][0] <= item[0][1] <= item[0][2])
            ),
            len(item[0]),
            -(max(item[0]) - min(item[0])) if len(item[0]) > 1 else 0,
        ),
        reverse=True,
    )
    values, raw = candidates[0]
    partial_columns: str | None = None
    if len(values) == 3:
        first_gap, second_gap = values[1] - values[0], values[2] - values[1]
        if first_gap > 0 and second_gap > 0 and max(first_gap, second_gap) >= 3 * min(first_gap, second_gap) and max(first_gap, second_gap) > 0.2:
            if first_gap > second_gap:
                values = values[1:]
                partial_columns = "nominal_max"
            else:
                values = values[:2]
                partial_columns = "min_nominal"
    evidence = [_evidence(page, f"OCR {family}{pin_count} dimension table", raw, "ocr_cross_checked", record.ocr_confidence or 0.0, scope)]
    if len(values) == 3 and values[0] <= values[1] <= values[2]:
        return Measurement(min_mm=values[0], nominal_mm=values[1], max_mm=values[2], original_value=" / ".join(map(str, values)), original_unit="mm", evidence=evidence)
    if len(values) == 2:
        if partial_columns == "nominal_max":
            return Measurement(nominal_mm=values[0], max_mm=values[1], original_value=" / ".join(map(str, values)), original_unit="mm", evidence=evidence)
        if partial_columns == "min_nominal":
            return Measurement(min_mm=values[0], nominal_mm=values[1], original_value=" / ".join(map(str, values)), original_unit="mm", evidence=evidence)
        # A two-column MIN/MAX table is common in package drawings and must
        # take precedence over the partial NOM/MAX heuristic.
        min_max_table = bool(re.search(
            r"Dimensions\s+In\s+Millimeters.{0,240}?\bMin\.?\s+Max\.?",
            record.text,
            re.I | re.S,
        ))
        if min_max_table:
            return Measurement(min_mm=min(values), max_mm=max(values), original_value=" / ".join(map(str, values)), original_unit="mm", evidence=evidence)
        # Preserve a partial nominal/max row only when the first value is the
        # round nominal and the second is a small positive tolerance step.
        if abs(values[0] - round(values[0], 1)) < 1e-9 and 0 < values[1] - values[0] <= 0.2:
            return Measurement(nominal_mm=values[0], max_mm=values[1], original_value=" / ".join(map(str, values)), original_unit="mm", evidence=evidence)
        return Measurement(min_mm=min(values), max_mm=max(values), original_value=" / ".join(map(str, values)), original_unit="mm", evidence=evidence)
    if symbol in {"D", "E", "D2", "E2"} and re.search(r"\b(?:MIN|NOM|MAX|MILLIMETER)\b", section, re.I):
        return Measurement(nominal_mm=values[0], original_value=str(values[0]), original_unit="mm", evidence=evidence)
    return Measurement(value_mm=values[0], original_value=str(values[0]), original_unit="mm", evidence=evidence)


def _drawing_typ_measurement(records: list[PageRecord], pages: list[int], cue: str, scope: str) -> Measurement | None:
    cue_pattern = re.compile(cue, re.I)
    for record in records:
        if record.number not in pages:
            continue
        lines = [_clean(line) for line in record.text.splitlines() if _clean(line)]
        for index, line in enumerate(lines):
            window = " ".join(lines[max(0, index - 2):index + 3])
            if cue_pattern.search(window):
                match = re.search(r"\(?\s*(0\.\d+)\s*\)?\s*(?:mm\s*)?(?:TYP|PITCH)\b", window, re.I)
                if match:
                    method = "ocr_cross_checked" if record.ocr_confidence is not None else "pdf_text"
                    found = _measurement([float(match.group(1))], record.number, "Package drawing", window, method, scope)
                    if found and method == "ocr_cross_checked":
                        return found.model_copy(update={"evidence": [item.model_copy(update={"confidence": 0.93}) for item in found.evidence]})
                    return found
    return None


def _array_ball_range(record: PageRecord, expected_count: int) -> Measurement | None:
    text = _clean(record.text)
    # This reader binds page-wide text, not individual drawing regions. A
    # repeated process aperture is not a component ball. Mixed-role pages
    # require region isolation before this fallback can safely consume them.
    if re.search(
        r"\b(?:EXAMPLE\s+(?:STENCIL\s+DESIGN|BOARD\s+LAYOUT)|"
        r"LAND\s+PATTERN\s+EXAMPLE|SOLDER\s+PASTE\s+EXAMPLE)\b",
        text, re.I,
    ):
        return None
    # Many memory/device datasheets place the only available bump size in the
    # package/pin-map heading, e.g. "16-ball WLCSP (Ball Diameter 0.30mm)".
    # Bind it only when the same heading also carries the expected count.
    explicit = re.search(
        rf"\b{expected_count}\s*[- ]?\s*(?:ball|bump)s?\b.{{0,100}}?"
        rf"\b(?:ball|bump)\s+diameter\s*[:=]?\s*(0\.\d+)\s*mm\b",
        text,
        re.I,
    ) if expected_count else None
    if explicit:
        diameter = float(explicit.group(1))
        raw = _clean(explicit.group(0))
        return Measurement(
            value_mm=diameter,
            original_value=raw,
            original_unit="mm",
            evidence=[_evidence(
                record.number,
                "Package / Pin Map heading - Ball Diameter",
                raw,
                "pdf_text",
                0.97,
                "component_terminal",
            )],
        )
    # Bind values to the repeated-ball callout itself. This prevents unrelated
    # side-view height dimensions (for example 0.20/0.14 mm) from being
    # mistaken for the ball diameter printed next to "42X".
    if expected_count:
        for count_match in re.finditer(rf"\b{expected_count}\s*[Xx]\b", text):
            before = text[max(0, count_match.start() - 90):count_match.start()]
            after = text[count_match.end():count_match.end() + 90]
            before_values = [float(item) for item in re.findall(r"0\.\d+", before)]
            after_values = [float(item) for item in re.findall(r"0\.\d+", after)]
            pair: list[float] = []
            if before_values and after_values:
                pair = [before_values[-1], after_values[0]]
            elif len(after_values) >= 2:
                pair = after_values[:2]
            if len(pair) == 2 and min(pair) / max(pair) >= 0.70:
                low, high = sorted(pair)
                raw = _clean(text[max(0, count_match.start() - 45):count_match.end() + 55])
                method = "ocr_cross_checked" if record.ocr_confidence is not None else "pdf_text"
                return Measurement(
                    min_mm=low, max_mm=high, original_value=raw, original_unit="mm",
                    evidence=[_evidence(record.number, "Package Outline repeated ball diameter", raw, method, 0.94, "component_terminal")],
                )
    patterns = (
        rf"(0\.\d+)\s+(\d{{1,4}})X\s+(0\.\d+)",
        rf"(\d{{1,4}})X\s*(0\.\d+)\s+(0\.\d+)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            values = match.groups()
            count_values = [int(value) for value in values if re.fullmatch(r"\d{1,4}", value)]
            decimals = [float(value) for value in values if value.startswith("0.")]
            if len(decimals) == 2 and min(decimals) / max(decimals) >= 0.5 and (not expected_count or expected_count in count_values):
                low, high = sorted(decimals)
                return Measurement(min_mm=low, max_mm=high, original_value=_clean(match.group(0)), original_unit="mm", evidence=[_evidence(record.number, "Package Outline ball diameter", match.group(0), "pdf_text", 0.91, "component_terminal")])
    # Sparse drawing OCR may read "42X Ø0.27/0.23" as
    # "AX ... 027 ... 0.23". Recover the missing decimal point only inside a
    # repeated-feature callout and only when the resulting pair is plausible.
    for match in re.finditer(r"\b(?:\d{1,4}|[A-Z])X\b.{0,50}?(?<!\d)0?(\d{2})(?!\d).{0,35}?\b(0\.\d+)\b", text, re.I):
        first = int(match.group(1)) / 100
        second = float(match.group(2))
        if 0.05 <= first <= 2.0 and 0.05 <= second <= 2.0 and min(first, second) / max(first, second) >= 0.70:
            low, high = sorted((first, second))
            raw = _clean(match.group(0))
            return Measurement(
                min_mm=low, max_mm=high, original_value=raw, original_unit="mm",
                evidence=[_evidence(record.number, "Package Outline repeated feature diameter", raw, "ocr_cross_checked", 0.82, "component_terminal")],
            )
    if expected_count and re.search(rf"\b[A-Z]{{2,5}}0*{expected_count}\b", text) and re.search(r"PACKAGE\s+OUTLINE", text, re.I):
        match = re.search(r"\b(0\.2\d)\b.{0,160}?\b(0\.2\d)\b", text, re.I)
        if match:
            low, high = sorted((float(match.group(1)), float(match.group(2))))
            raw = _clean(match.group(0))
            return Measurement(
                min_mm=low, max_mm=high, original_value=raw, original_unit="mm",
                evidence=[_evidence(record.number, "Package Outline ball diameter", raw, "ocr_cross_checked", 0.91, "component_terminal")],
            )
    return None


def _manufacturer_array_land(records: list[PageRecord], family: str, expected_count: int) -> RuleStatus | None:
    for record in records:
        if family not in record.families or not re.search(r"example\s+board\s+layout|land\s+pattern\s+example", record.text, re.I):
            continue
        text = _clean(record.text)
        # The upper drawing also contains parenthesized pitch values.  Copper
        # diameter is identified in the LAND PATTERN / EXPOSED METAL section,
        # not by taking the first or largest parenthesized number on the page.
        repeated_land = re.search(
            rf"\b{expected_count}\s*[Xx]\s*\(\s*(?:[DØ@]\s*)?(0\.\d+)\s*\)",
            text,
            re.I,
        )
        if repeated_land:
            matches = [repeated_land]
        else:
            matches = []
        land_section_match = re.search(r"LAND\s+PATTERN\s+EXAMPLE", text, re.I)
        land_section = text[land_section_match.start():] if land_section_match else text
        if not matches:
            matches = list(re.finditer(r"\(\s*(?:[DØ@]\s*)?(0\.\d+)\s*\)", land_section, re.I))
        if not matches:
            continue
        # On vendor board-layout drawings the repeated small parenthesized value
        # is the copper-land diameter. Keep the exact excerpt as evidence.
        plausible = [float(match.group(1)) for match in matches if float(match.group(1)) >= 0.10]
        if not plausible:
            continue
        diameter = min(plausible)
        method = "ocr_cross_checked" if record.ocr_confidence is not None else "pdf_text"
        evidence = _evidence(record.number, "Example Board Layout / Land Pattern Example", text[:600], method, 0.93 if method == "ocr_cross_checked" else 0.90, "land_pattern")
        return RuleStatus(
            status="manufacturer_recommended",
            value={"pad_width_mm": diameter, "pad_length_mm": diameter, "shape": "circle", "origin": "manufacturer_land_pattern"},
            evidence=[evidence], source_tier="device_datasheet",
        )
    return None


def _manufacturer_array_opening_land(
    records: list[PageRecord], family: str, expected_count: int, page: int
) -> RuleStatus | None:
    """Use an explicit package-drawing BALL/LAND OPENING as exact copper evidence.

    This is intentionally classified as ``reference_design`` rather than a
    manufacturer-recommended land pattern.  It preserves an explicit drawing
    value (for example Apollo510 BGA225 BALL OPENING 0.25) while keeping the
    production-release HOLD when the datasheet has no Recommended Land Pattern.
    Restricting the lookup to the selected package page prevents values from a
    different package option in the same PDF from leaking into this variant.
    """
    record = next((item for item in records if item.number == page), None)
    if record is None or family not in record.families:
        return None
    opening = _ocr_labeled_measurement(
        records,
        page,
        ("ball opening", "land opening", "copper opening"),
        "O",
        "land_pattern",
    )
    if opening is None:
        match = re.search(
            r"\b(?:BALL|LAND|COPPER)\s+OPENING\b.{0,48}?(?<!\d)(0\.\d+)(?!\d)",
            _clean(record.text),
            re.I,
        )
        if match:
            opening = Measurement(
                value_mm=float(match.group(1)),
                original_value=match.group(1),
                original_unit="mm",
                evidence=[_evidence(
                    page,
                    f"{family}{expected_count} package drawing BALL/LAND OPENING",
                    match.group(0),
                    "ocr_cross_checked" if record.ocr_confidence is not None else "pdf_text",
                    record.ocr_confidence or 0.86,
                    "land_pattern",
                )],
            )
    if opening is None:
        return None
    diameter = next((value for value in (
        opening.value_mm,
        opening.nominal_mm,
        opening.min_mm if opening.min_mm == opening.max_mm else None,
    ) if value is not None), None)
    if diameter is None or not 0.05 <= diameter <= 1.0:
        return None
    return RuleStatus(
        status="reference_design",
        value={
            "pad_width_mm": diameter,
            "pad_length_mm": diameter,
            "shape": "circle",
            "origin": "manufacturer_package_drawing_ball_opening",
        },
        evidence=opening.evidence,
        note="原厂封装图明确标注BALL/LAND OPENING；不是Recommended Land Pattern，正式发布仍需Layout/SMT确认。",
        source_tier="device_datasheet",
    )


def _manufacturer_array_process_rules(
    records: list[PageRecord], family: str, expected_count: int
) -> tuple[RuleStatus | None, RuleStatus | None]:
    """Merge mask and stencil evidence from every page of one package section."""
    solder_mask: RuleStatus | None = None
    paste_mask: RuleStatus | None = None
    for record in records:
        if family not in record.families:
            continue
        text = _clean(record.text)
        method = "ocr_cross_checked" if record.ocr_confidence is not None else "pdf_text"
        if solder_mask is None and re.search(r"NON[- ]SOLDER\s+MASK\s+DEFINED", text, re.I):
            expansion = 0.05 if re.search(r"0\.05\s+MAX", text, re.I) else None
            if expansion is not None:
                ev = _evidence(
                    record.number, "Example Board Layout - Solder Mask Details",
                    text[:800], method, 0.94, "solder_mask",
                )
                solder_mask = RuleStatus(
                    status="manufacturer_recommended",
                    value={"style": "NSMD", "expansion_mm": expansion, "preference": "preferred"},
                    evidence=[ev], note="原厂图纸标示NON-SOLDER MASK DEFINED (PREFERRED)。",
                    source_tier="device_datasheet",
                )
        if paste_mask is None and re.search(r"EXAMPLE\s+STENCIL\s+DESIGN|SOLDER\s+PASTE\s+EXAMPLE", text, re.I):
            size = 0.25 if re.search(r"0\.25", text) else None
            radius = 0.05 if re.search(r"R\s*(?:0\.05|[O0]\.0?5)", text, re.I) else None
            if size is not None:
                ev = _evidence(
                    record.number, "Example Stencil Design",
                    text[:800], method, 0.94, "paste_mask",
                )
                paste_mask = RuleStatus(
                    status="manufacturer_recommended",
                    value={
                        "aperture_width_mm": size,
                        "aperture_length_mm": size,
                        "shape": "rounded_rectangle" if radius else "square",
                        "corner_radius_mm": radius,
                        "stencil_thickness_mm": 0.10 if re.search(r"0\.1\s*mm\s+THICK\s+STENCIL", text, re.I) else None,
                    },
                    evidence=[ev], note="原厂Example Stencil Design。",
                    source_tier="device_datasheet",
                )
    return solder_mask, paste_mask


def _manufacturer_perimeter_land(
    records: list[PageRecord], family: str, page: int
) -> RuleStatus | None:
    """Extract an explicitly drawn manufacturer PCB land geometry block.

    Some vendors place a dimensioned PCB land drawing below the mechanical
    package table on the same ``PACKAGE OUTLINE`` page without calling it a
    "Recommended Land Pattern".  It is useful source geometry, but its status
    must remain ``reference_design`` until Layout approves it.
    """
    if family not in {"DFN", "SON", "QFN", "LCC"}:
        return None
    record = next((item for item in records if item.number == page), None)
    if record is None or not re.search(r"Dimensions\s+In\s+Millimeters", record.text, re.I):
        return None
    for table in record.tables:
        values: dict[str, float] = {}
        raw_rows: list[str] = []
        for row in table:
            cells = [_clean(cell) for cell in row]
            raw_rows.append(" | ".join(cells))
            if len(cells) < 2:
                continue
            symbol = cells[0]
            numbers = _numbers(cells[1])
            if symbol in {"D", "E", "D1", "E1", "D2", "e", "b", "k", "L"} and numbers:
                values[symbol] = float(numbers[0])
        required = {"D", "E", "D1", "E1", "D2", "e", "b", "L"}
        if not required.issubset(values):
            continue
        # D2 larger than D plus a smaller central D1/E1 rectangle identifies
        # a land drawing rather than the mechanical MIN/MAX table above it.
        if not (
            values["D2"] > values["D"]
            and values["D1"] < values["D"]
            and values["E1"] < values["E"]
            and values["L"] > values["b"]
        ):
            continue
        pad_center_x = (values["D2"] - values["L"]) / 2
        raw = "\n".join(raw_rows)
        evidence = [_evidence(
            page,
            "Manufacturer dimensioned PCB land figure (not titled Recommended Land Pattern)",
            raw,
            "table_extract",
            0.95,
            "land_pattern",
        )]
        return RuleStatus(
            status="reference_design",
            value={
                "pad_width_mm": values["b"],
                "pad_length_mm": values["L"],
                "pad_center_x_mm": round(pad_center_x, 6),
                "pitch_mm": values["e"],
                "exposed_pad_width_mm": values["D1"],
                "exposed_pad_length_mm": values["E1"],
                "overall_land_width_mm": values["D2"],
                "body_reference_width_mm": values["D"],
                "body_reference_length_mm": values["E"],
                "origin": "manufacturer_dimensioned_land_figure_not_titled_recommended",
            },
            evidence=evidence,
            note="原厂Datasheet给出尺寸化PCB Land图，但未标注Recommended；保持HOLD等待Layout确认。",
            source_tier="device_datasheet",
        )
    return None


def _vector_perimeter_land(records, family, count, pitch, side_counts):
    patterns = [(r,p) for r in records for p in r.vector_land_patterns
                if p["family"]==family and p["pin_count"]==count]
    if len(patterns)!=1 or pitch is None:
        return None
    record,pattern=patterns[0]
    pitch_value = next((v for v in (pitch.value_mm,pitch.nominal_mm) if v is not None),None)
    if (pitch_value is None or abs(pitch_value-pattern["pitch_mm"])>1e-6
            or side_counts!=pattern["side_pin_counts"]):
        return None
    length=pattern["pad_length_mm"]
    if min(pattern["overall_x_mm"],pattern["overall_y_mm"])<=length:
        return None
    return RuleStatus(
        status=("manufacturer_recommended" if re.search(r"\brecommended\b",pattern["caption_text"],re.I)
                else "reference_design"),
        value={
            "pad_width_mm":pattern["pad_width_mm"],"pad_length_mm":length,
            "pad_center_x_mm":(pattern["overall_x_mm"]-length)/2,
            "pad_center_y_mm":(pattern["overall_y_mm"]-length)/2,
            "pitch_mm":pattern["pitch_mm"],"origin":pattern["origin"],
            "role":"pcb_copper_land","binding_method":"vector_arrow_extension_lines",
            "dimension_bindings":pattern["dimension_bindings"],
            "pad_bindings":pattern["pad_bindings"],
            "numbered_corner_pads":pattern["numbered_corner_pads"],
            "side_pin_counts":pattern["side_pin_counts"],
            "source_side_origin_quarter_turns":pattern.get("source_side_origin_quarter_turns",0),
        },
        evidence=[_evidence(record.number,pattern["caption_text"],
            json.dumps({"pad_bindings":pattern["pad_bindings"],"region":pattern["region_pdf_points"]}),
            "table_extract",.97,"land_pattern")],
        source_tier="device_datasheet",
        note="尺寸来自箭头／延伸线绑定的铜焊盘与图例；Footprint example 不冒充推荐或量产核准。",
    )


def _count_token_end() -> str:
    """A family count cannot be the integer prefix of a physical dimension.

    Keep this boundary shared by all compact/spaced discovery paths; otherwise
    a rejected identity can be reintroduced by the mechanical-page fallback.
    """
    return r"\b(?![.,]\d)(?!\s*[xX×]\s*\d)(?!\s*[- ]?\s*(?:mm|mil|inch|inches)\b)"


def _discover(records: list[PageRecord]) -> list[PackageCandidate]:
    candidates: list[PackageCandidate] = []
    for record in records:
        native=record.text.split('[OCR PACKAGE PAGE]',1)[0]
        if PACKAGE_PAGE_CUE.search(native) and re.search(r'\b(?:UFQFPN|U?QFN|[LT]?QFP|[ST]?SSOP|SOIC|DFN|BGA)[-_]?\d+\b',native,re.I):
            record=replace(record,text=native)
        # A package code is an identity, not its pin count (SOT-353, TO-252,
        # SOD-323). Keep these families visible even when geometry is unresolved.
        # Generic words such as "terminal" or "maximum" also occur in
        # electrical prose. They do not make a stray SOT/TO/SMA token a
        # mechanical package candidate.
        if PACKAGE_PAGE_CUE.search(record.text) or record.located_dimension_tables or record.dimension_schema_values:
            for family, pattern in (
                ('DIODE',r'\b(?:SOD[- ]?\d+[A-Z]*|SM[ABC](?:[- ]FL)?|DO[- ]?\d+[A-Z]*)\b'),
                ('SOT',r'\b(?:SOT|TSOT)[- ]?\d+(?:-\d+)?\b'),
                ('TO',r'\bTO[- ]?\d+[A-Z]*\b'),
            ):
                for match in re.finditer(pattern,record.text):
                    ocr_start = record.text.find('[OCR PACKAGE PAGE]')
                    if (ocr_start >= 0 and match.start() > ocr_start
                            and not record.located_dimension_tables
                            and not record.dimension_schema_values
                            and not PACKAGE_PAGE_CUE.search(record.text[max(ocr_start, match.start()-120):match.end()+120])):
                        # An OCR-only package-looking token in electrical prose
                        # needs local mechanical context, not a title elsewhere.
                        continue
                    code=re.sub(r'\s+','',match[0]).upper()
                    explicit=re.search(r':\s*(\d+)\s*[- ]LEADS?\b',record.text[match.end():match.end()+50],re.I)
                    suffix=re.search(r'\d+-(\d+)$',code) if family=='SOT' else None
                    pins=int(explicit[1]) if explicit else int(suffix[1]) if suffix else 0
                    candidates.append(PackageCandidate(family,record.number,pins,code))
        if (DIMENSION_PAGE_CUE.search(record.text) or PACKAGE_PAGE_CUE.search(record.text)) and 'CONNECTOR' in record.families and re.search(r'No\.?\s*(?:of\s*)?(?:Positions|Contacts)|排针|排針',record.text,re.I):
            candidates.append(PackageCandidate('CONNECTOR',record.number,0))
        if re.search(r'package\s+outline',record.text,re.I):
            for match in re.finditer(r'\b([A-Z]{2,5})(\d{4})([A-Z])\b',record.text):
                nearby=record.text[max(0,match.start()-30):match.end()+80]
                families=set(identify_package_families(nearby)) & {'SOIC','QFP','QFN','DFN','BGA','WLCSP','LGA'}
                if len(families)==1 and 2<=int(match[2])<=2000:
                    candidates.append(PackageCandidate(next(iter(families)),record.number,int(match[2]),match[0]))
        # Compact vendor package names commonly encode body size before the
        # final pin count (DFN2x2-8) or use CSP-N for wafer-level arrays.
        for match in re.finditer(r"\bDFN\s*\d+(?:\s*[xX]\s*\d+)?[-_ ](\d{1,3})\b", record.text, re.I):
            candidates.append(PackageCandidate("DFN", record.number, int(match.group(1)), _clean(match.group(0))))
        csp_matches = list(re.finditer(r"\bCSP[-_ ]?(\d{1,3})" + _count_token_end(), record.text, re.I))
        csp_counts = Counter(int(match.group(1)) for match in csp_matches)
        repeated_counts = {count for count, occurrences in csp_counts.items() if occurrences > 1}
        for match in csp_matches:
            count = int(match.group(1))
            if count < 2:
                continue
            # OCR often creates one stray package number beside several
            # identical text/vector readings of the real package identity.
            if repeated_counts and count not in repeated_counts:
                continue
            candidates.append(PackageCandidate("WLCSP", record.number, count, _clean(match.group(0))))
        for match in re.finditer(r"\bWCSP\s*(\d{1,3})\s*F\b", record.text, re.I):
            candidates.append(PackageCandidate("WLCSP", record.number, int(match.group(1)), _clean(match.group(0))))
        for match in re.finditer(
            r"(?:pin\s+configuration.{0,80}?|package\s+information.{0,80}?)"
            r"\b(\d{1,3})\s+(WLP|PicoStar)\b",
            _clean(record.text),
            re.I,
        ):
            count, alias = int(match.group(1)), match.group(2).upper()
            candidates.append(PackageCandidate("WLCSP", record.number, count, f"{alias}{count}"))
        # Package identity is often declared on the cover or pin-information
        # page while the mechanical drawing only says "Package Dimensions of
        # QFN". Capture that identity globally and associate it with the
        # strongest drawing page below.
        for family in PACKAGE_ORDER:
            if family not in record.families:
                continue
            family_alias = {
                "BGA": r"(?:DSBGA|WFBGA|FCBGA|UFBGA|TFBGA|VFBGA|PBGA|CBGA|FBGA|BGA)",
                "WLCSP": r"(?:WLCSP|WCSP(?:\d+F)?|WLP|PICOSTAR)",
                "DFN": r"(?:DFN|WSON|SON|XSON|USON)",
                "SOIC": r"(?:SOIC|SOP|SSOP|TSSOP|MSOP|VSSOP|QSOP|SOJ)",
                "QFP": r"(?:QFP|LQFP|TQFP|MQFP|PLCC)",
                "QFN": r"(?:UFQFPN|UQFN|VQFN|HVQFN|TQFN|QFN)",
            }.get(family, re.escape(family))
            # A whitespace-separated number after a family token is unsafe in
            # ordering tables (for example "BGA 120 ... 169-pin", where 120 is
            # GPIO count). Accept compact/hyphenated identities here; spaced
            # identities must be confirmed by an explicit pin/ball phrase.
            for raw_count in re.findall(rf"\b{family_alias}(?:[-_]?(\d{{1,4}}))" + _count_token_end(), record.text, re.I) if family not in {'SOT','TO','DIODE'} else []:
                count = int(raw_count)
                if 2 <= count <= 2000:
                    candidates.append(PackageCandidate(family, record.number, count, f"{family}{count}"))
            for raw_count in re.findall(rf"\b{family_alias}\s+[- ]?(\d{{1,4}})\s*[- ]?(?:pin|ball|bump|land)s?\b", record.text, re.I):
                count = int(raw_count)
                if 2 <= count <= 2000:
                    candidates.append(PackageCandidate(family, record.number, count, f"{family}{count}"))
            # Vendor package identity often uses "169-pin BGA" or
            # "42-ball DSBGA" rather than BGA169. This is stronger than a bare
            # family/count token found inside a multi-page pin-function table.
            for raw_count in re.findall(rf"\b(\d{{1,4}})\s*[- ]\s*(?:pin|ball|bump|land)s?\s+{family_alias}\b", record.text, re.I):
                count = int(raw_count)
                if 2 <= count <= 2000:
                    candidates.append(PackageCandidate(family, record.number, count, f"{family}{count}"))
        for code, raw_count in re.findall(r"\b([A-Z]{2,5})(\d{4})\b", record.text):
            count = int(raw_count)
            # Drawing codes may encode pin count, but unrelated references
            # (TN1433) and crypto capabilities (RSA1024) emphatically do not.
            if code not in {"TN", "RSA", "AES", "ISO", "IEEE", "DS", "AN"} and 2 <= count <= 2000 and record.families & ARRAY_FAMILIES and re.search(r"package\s+outline", record.text, re.I):
                family = next((item for item in PACKAGE_ORDER if item in record.families and item in ARRAY_FAMILIES), "BGA")
                candidates.append(PackageCandidate(family, record.number, count, f"{code}{raw_count}"))

        for match in re.finditer(r"\b([A-Z][A-Z0-9]{4,})\s+Active\s+(DSBGA|WLCSP|WCSP|BGA|LGA)\s+([A-Z]{2,5})\s+(\d{1,4})\b", _clean(record.text), re.I):
            package_type, code, raw_count = match.group(2).upper(), match.group(3).upper(), match.group(4)
            family = "WLCSP" if package_type in {"WLCSP", "WCSP"} else ("LGA" if package_type == "LGA" else "BGA")
            candidates.append(PackageCandidate(family, record.number, int(raw_count), code))

        if not PACKAGE_PAGE_CUE.search(record.text):
            continue
        for family in PACKAGE_ORDER:
            if family not in record.families:
                continue
            explicit = re.findall(rf"\b{family}[-_ ]?(\d{{2,4}})" + _count_token_end(), record.text, re.I) if family not in {'SOT','TO','DIODE'} else []
            if explicit:
                for count in dict.fromkeys(int(item) for item in explicit):
                    candidates.append(PackageCandidate(family, record.number, count, f"{family}{count}"))
            else:
                strong_page = re.search(
                    r"package\s+(?:outline|drawing|dimensions?)|mechanical\s+drawing|"
                    r"example\s+board\s+layout|land\s+pattern\s+example|"
                    r"package\s+type\s+package\s+drawing\s+pins",
                    record.text, re.I,
                )
                if strong_page:
                    candidates.append(PackageCandidate(family, record.number))
    # Some documents put only a short section title (for example "5.1 QFN50")
    # on an image-only package page. Restrict this fallback to heading-shaped
    # lines so unrelated BGA120 feature text cannot become a package candidate.
    for record in records:
        for family in PACKAGE_ORDER:
            if family in {'SOT','TO','DIODE'}:
                continue
            headings = "\n".join(
                line for line in record.text.splitlines()
                if re.fullmatch(rf"\s*(?:\d+(?:\.\d+)*\s*)?{family}[-_ ]?\d{{2,4}}\s*", line, re.I)
            )
            for raw_count in re.findall(rf"\b{family}[-_ ]?(\d{{2,4}})\b", headings, re.I):
                item = PackageCandidate(family, record.number, int(raw_count), f"{family}{raw_count}")
                # Keep the real drawing candidate even when the same package is
                # mentioned earlier in a contents/order table. The page scorer
                # below must choose between both pieces of evidence.
                candidates.append(item)
    # Merge title-only package drawings with authoritative identity first, then
    # with a pin map only when no package code/count is available.
    authoritative = [item for item in candidates if item.pin_count and item.package_code]
    identities={(item.family,item.pin_count,item.package_code) for item in authoritative}
    if len(identities)==1:
        family,count,code=next(iter(identities))
        for record in records:
            if not record.families and PACKAGE_PAGE_CUE.search(record.text) and (record.located_dimension_tables or record.ocr_tokens):
                candidates.append(PackageCandidate(family,record.number,count,code))
    for candidate in candidates:
        if not candidate.pin_count and candidate.package_code:
            matching={item.pin_count for item in authoritative if item.family==candidate.family and item.package_code==candidate.package_code}
            if len(matching)==1:
                candidate.pin_count=next(iter(matching))
        if not candidate.pin_count and not candidate.package_code:
            same_family = [item for item in authoritative if item.family == candidate.family]
            if same_family:
                identity = min(same_family, key=lambda item: abs(item.page - candidate.page))
                candidate.pin_count = identity.pin_count
                candidate.package_code = identity.package_code
                continue
        nearest: tuple[int, tuple[int, list[str], int]] | None = None
        for record in records:
            if candidate.family not in record.families:
                continue
            shape = (
                _pin_map_shape(record)
                or _explicit_coordinate_shape(record, candidate.pin_count)
                or _drawing_pin_map_shape(record, candidate.pin_count)
            )
            if shape and (nearest is None or abs(record.number - candidate.page) < nearest[0]):
                nearest = (abs(record.number - candidate.page), shape)
        if nearest and not candidate.pin_count:
            candidate.pin_count = nearest[1][0]
    def page_score(item: PackageCandidate) -> int:
        record = next(record for record in records if record.number == item.page)
        text = record.text
        family_alias = {
            "QFN": r"(?:UFQFPN|UQFN|VQFN|HVQFN|TQFN|QFN)",
            "BGA": r"(?:DSBGA|WFBGA|FCBGA|UFBGA|BGA|FBGA|PBGA|TFBGA|VFBGA)",
            "WLCSP": r"(?:WLCSP|WCSP(?:\d+F)?|WLP|PICOSTAR)",
            "DFN": r"(?:DFN|WSON|SON|XSON|USON)",
            "SOIC": r"(?:SOIC|SOP|SSOP|TSSOP|MSOP|VSSOP|QSOP|SOJ)",
            "QFP": r"(?:QFP|LQFP|TQFP|MQFP|PLCC)",
        }.get(item.family, re.escape(item.family))
        return (
            (90 if re.search(rf'{family_alias}[-_ ]?{item.pin_count}\b[^\n]{{0,25}}(?:package\s+information|[-:]\s*(?:outline|mechanical))',text.split('[OCR PACKAGE PAGE]',1)[0],re.I) else 0)
            + (60 if re.search(r'封装(?:信息|尺寸|外形|图)|封裝(?:資訊|尺寸|外形|圖)',text) and record.located_dimension_tables else 0)
            + (65 if record.dimension_schema_values and re.search(r'package\s+dimensions?',text,re.I) else 0)
            + (70 if item.family in {'SOT','TO','DIODE'} and item.package_code and item.package_code.replace('-','') in re.sub(r'[-\s]','',text.split('[OCR PACKAGE PAGE]',1)[0]).upper() and record.dimension_schema_values else 0)
            + (50 if re.search(r"(?:^|\n)\s*package\s+outline", text, re.I) else 0)
            + (15 if re.search(r"\b[A-Z]{2,5}\d{4}\b", text) else 0)
            + (10 if re.search(r"package\s+(?:outline|drawing)|mechanical\s+drawing", text, re.I) else 0)
            + (25 if re.search(r"package\s+dimensions?", text, re.I) else 0)
            + (5 if re.search(r"example\s+board\s+layout|land\s+pattern\s+example", text, re.I) else 0)
            + (35 if re.search(r"\bSYMBOL\b.{0,80}\bMILLIMETER\b", _clean(text), re.I) else 0)
            + (40 if item.family in ARRAY_FAMILIES and re.search(r"package\s+body|ball\s+pitch|bump\s+diameter", text, re.I) else 0)
            + (10 if record.ocr_confidence is not None else 0)
            + (2 if record.tables else 0)
            + (35 if re.search(
                rf"(?:^|\n)\s*(?:\d+(?:-\d+)*\.\s*)?{item.pin_count}\s*[- ]?"
                rf"(?:pin|ball|bump|land)s?\s+{family_alias}\b",
                text,
                re.I,
            ) else 0)
            + (45 if re.search(
                rf"\b{item.pin_count}\s*[- ]?\s*(?:ball|bump)s?\s+"
                rf"(?:{re.escape(item.family)}|WCSP|DSBGA)\b.{{0,120}}?"
                r"\b(?:ball|bump)\s+diameter\b",
                text,
                re.I,
            ) else 0)
            - (20 if re.search(r"^\s*package\s+materials\s+information", text, re.I) else 0)
            - (200 if re.search(r'\bcontents\b|目录|目錄',text,re.I) and not record.dimension_schema_values else 0)
        )

    unique: dict[tuple, PackageCandidate] = {}
    drawing_identities={(c.family,c.pin_count) for c in candidates if c.package_code and re.fullmatch(r'[A-Z]{2,5}\d{4}[A-Z]',c.package_code)}
    for candidate in candidates:
        if candidate.family in {'SOT','TO','DIODE'} and not candidate.package_code and any(c.family==candidate.family and c.package_code for c in candidates):
            continue
        if (candidate.family,candidate.pin_count) in drawing_identities and candidate.package_code==f'{candidate.family}{candidate.pin_count}':
            continue
        distinct=candidate.family in {'SOT','TO','DIODE'} or (candidate.package_code and re.fullmatch(r'[A-Z]{2,5}\d{4}[A-Z]',candidate.package_code))
        key = (candidate.family, candidate.pin_count, candidate.package_code if distinct else None)
        if key not in unique or page_score(candidate) > page_score(unique[key]):
            unique[key] = candidate
    discovered = list(unique.values())
    strong_families = {item.family for item in discovered if page_score(item) > 0}
    # A bare token such as "BGA120 feature" is not a package identity. Keep a
    # score-zero identity only when the document has no stronger drawing/order/
    # pin evidence for that family; title-only drawings have already inherited
    # the authoritative count above and therefore carry a positive page score.
    return [item for item in discovered if page_score(item) > 0 or item.family not in strong_families]


def _table_rows(record: PageRecord) -> list[tuple[str, list[str]]]:
    result: list[tuple[str, list[str]]] = []
    for table in record.tables:
        for row in table:
            cells = [_clean(cell) for cell in row]
            result.append((" | ".join(cells), cells))
    return result


def _dimension_symbol_cell_matches(cell: str, symbol: str) -> bool:
    """Match JEDEC table symbols without collapsing case-sensitive E/e.

    Vendor tables commonly print terminal width ``b`` as uppercase ``B`` and
    combine equal axes into rows such as ``D/E`` or ``D2/E2``.  General
    case-folding is unsafe because body ``E`` and pitch ``e`` are different
    dimensions, so only the known lowercase ``b`` alias is case-insensitive.
    """
    cleaned = _clean(cell).replace(" ", "")
    if symbol == "b" and cleaned in {"b", "B"}:
        return True
    if symbol == "b" and not re.search(r"\d", cell) and re.search(r"(?:^|\s)[bB]$", _clean(cell)):
        # Diagonal confidentiality watermarks sometimes leak one leading
        # letter into the first table cell (for example ``R B``).  Accept only
        # a terminal B at the end of a digit-free symbol cell.
        return True
    return symbol in cleaned.split("/")


def _row_measurement(
    records: list[PageRecord], page: int, labels: tuple[str, ...], symbols: tuple[str, ...],
    scope: str = "package_outline", max_distance: int = 1,
) -> Measurement | None:
    for distance in range(max_distance + 1):
        for record in records:
            if abs(record.number - page) != distance:
                continue
            for wanted in symbols:
                if scope != 'land_pattern' and wanted in record.dimension_schema_values:
                    value = record.dimension_schema_values[wanted]
                    return value.model_copy(update={"evidence": [ev.model_copy(update={"evidence_scope": scope}) for ev in value.evidence]})
            if record.located_dimension_tables:
                # Do not resurrect a conflicted/rejected structured cell through
                # the legacy flattened numeric reader. Scope remains explicit.
                if scope == 'land_pattern':
                    from .dimension_tables import read_table
                    for table in record.located_dimension_tables:
                        if table['scope'] != scope:
                            continue
                        parsed, _, _ = read_table(table['rows'],record.number,record.text,
                            method=table['method'],scope=scope)
                        for wanted in symbols:
                            if wanted in parsed:
                                return parsed[wanted]
                continue
            from .dimension_tables import read_table
            schema_tables = []
            for table in record.tables:
                parsed, issues, schema = read_table(table, record.number, record.text)
                if schema:
                    schema_tables.append(table)
                    for wanted in symbols:
                        value = parsed.get(wanted)
                        if value is not None:
                            return value.model_copy(update={"evidence": [ev.model_copy(update={"evidence_scope": scope}) for ev in value.evidence]})
            for raw, cells in _table_rows(record):
                if any(any([_clean(c) for c in row] == cells for row in table) for table in schema_tables):
                    continue  # Never reinterpret a rejected schema row by sorted numbers.
                low = raw.lower()
                has_label = any(label.lower() in low for label in labels)
                # Headerless fallback only supports a leading symbol column.
                # A datum B following a tolerance value is not terminal b.
                # Other column orders need the structured table schema above.
                leading_cell = next((cell for cell in cells if _clean(cell)), '')
                has_symbol = any(
                    _dimension_symbol_cell_matches(leading_cell, symbol)
                    for symbol in symbols
                )
                # Dimension tables with D/E/e/b symbols are safer than label-only
                # regexes. A label may span the D row while E is on the next row.
                if symbols and not has_symbol:
                    continue
                if not symbols and not has_label:
                    continue
                values: list[float] = []
                for cell in cells:
                    if any(_dimension_symbol_cell_matches(cell, symbol) for symbol in symbols):
                        continue
                    values.extend(_numbers(cell))
                # Ignore dimension symbols embedded as digits (A1/D1/E1).
                values = [value for value in values if value < 100]
                found = _measurement(_metric_dimension_values(values), record.number, "Package dimension table", raw, scope=scope)
                if found:
                    return found
    return None


def _ocr_labeled_measurement(
    records: list[PageRecord], page: int, labels: tuple[str, ...], symbol: str, scope: str
) -> Measurement | None:
    """Bind a drawing label and symbol to nearby decimal dimensions.

    Mechanical drawings frequently place the semantic label, the JEDEC symbol
    and its value on separate baselines.  Binding a value only when all three
    happen to share one OCR line silently loses valid dimensions.  This pass
    therefore uses a small local window, while still requiring a unique label
    and retaining the complete raw window as evidence.
    """
    record = next((item for item in records if item.number == page and item.ocr_confidence is not None), None)
    if record is None:
        return None
    lines = [_clean(line) for line in record.text.splitlines() if _clean(line)]
    label_pattern = re.compile("|".join(re.escape(label) for label in labels), re.I)
    symbol_only_pattern = re.compile(rf"^{re.escape(symbol)}$", re.I)
    symbol_token_pattern = re.compile(rf"\b{re.escape(symbol)}\b", re.I)

    def decimals(window: list[str]) -> list[float]:
        values: list[float] = []
        for item in window:
            values.extend(float(value) for value in re.findall(r"(?<![A-Za-z0-9])\d+\.\d+(?![A-Za-z0-9])", item))
        return [value for value in values if 0 < value < 100]

    def normalize_triplet(values: list[float]) -> list[float]:
        candidate = values[:3]
        if len(candidate) != 3 or candidate[0] <= candidate[1] <= candidate[2]:
            return candidate
        # OCR commonly confuses the leading digit in a compact MIN/NOM/MAX
        # row (for example 3.5 / 5.6 / 3.7). Repair only when both endpoints
        # share an integer part and the middle fractional digit yields a strict
        # monotonic sequence. The unmodified source row remains in evidence.
        endpoint_integer = math.floor(candidate[0])
        repaired_middle = endpoint_integer + (candidate[1] - math.floor(candidate[1]))
        if math.floor(candidate[2]) == endpoint_integer and candidate[0] <= repaired_middle <= candidate[2]:
            return [candidate[0], round(repaired_middle, 6), candidate[2]]
        return candidate

    # Best case: semantic label, symbol and value survived on one OCR line.
    for line in lines:
        if not label_pattern.search(line) or not symbol_token_pattern.search(line):
            continue
        values = normalize_triplet(decimals([symbol_token_pattern.sub(" ", line)]))
        if values:
            found = _measurement(values[:3], record.number, "OCR package dimension row", line, "ocr_cross_checked", scope)
            if found:
                return found.model_copy(update={
                    "evidence": [item.model_copy(update={"confidence": record.ocr_confidence or 0.0}) for item in found.evidence]
                })

    for index, line in enumerate(lines):
        context_lines = lines[max(0, index - 4):index + 1]
        if not symbol_only_pattern.fullmatch(line) or not label_pattern.search(" ".join(context_lines)):
            continue
        value_lines = lines[index + 1:index + 6]
        values = normalize_triplet(decimals(value_lines))
        if not values:
            continue
        raw = " | ".join(context_lines + [line] + value_lines)
        found = _measurement(values[:3], record.number, "OCR package dimension row", raw, "ocr_cross_checked", scope)
        if found:
            return found.model_copy(update={
                "evidence": [item.model_copy(update={"confidence": record.ocr_confidence or 0.0}) for item in found.evidence]
            })

    # Some OCR engines omit a one-character JEDEC symbol.  A unique semantic
    # label may still be used, but only with decimal values immediately below
    # it; integers are deliberately ignored to avoid binding BALL COUNT as a
    # physical dimension.
    label_indexes = [index for index, line in enumerate(lines) if label_pattern.search(line)]
    # PDF text, vector OCR and raster OCR may duplicate the same semantic row.
    # Accept repeated inline rows only when every recovered decimal tuple is
    # identical; conflicting duplicates remain unresolved for review.
    repeated_inline: list[tuple[tuple[float, ...], str]] = []
    for index in label_indexes:
        values = tuple(normalize_triplet(decimals([lines[index]]))[:3])
        if values:
            repeated_inline.append((values, lines[index]))
    if repeated_inline and len({item[0] for item in repeated_inline}) == 1:
        values, raw = repeated_inline[0]
        found = _measurement(
            list(values), record.number, "OCR repeated semantic dimension row",
            raw, "ocr_cross_checked", scope,
        )
        if found:
            return found.model_copy(update={
                "evidence": [item.model_copy(update={"confidence": record.ocr_confidence or 0.0}) for item in found.evidence]
            })
    if len(label_indexes) == 1:
        index = label_indexes[0]
        local_lines = lines[max(0, index - 3):index + 4]
        inline_values: list[float] = []
        for local in local_lines:
            if re.match(rf"^{re.escape(symbol)}\b", local, re.I):
                inline_values = normalize_triplet(decimals([symbol_token_pattern.sub(" ", local)]))
                if inline_values:
                    raw = " | ".join(local_lines)
                    found = _measurement(inline_values[:3], record.number, "OCR semantic dimension block", raw, "ocr_cross_checked", scope)
                    if found:
                        return found.model_copy(update={
                            "evidence": [item.model_copy(update={"confidence": record.ocr_confidence or 0.0}) for item in found.evidence]
                        })
        # Vendor CSP tables often spell out "Bump diameter" or "Ball pitch"
        # and omit the one-character JEDEC symbol entirely. A unique label
        # with its decimal row on the same OCR line is still deterministic.
        inline_values = normalize_triplet(decimals([lines[index]]))
        if inline_values:
            found = _measurement(
                inline_values[:3], record.number, "OCR semantic dimension row",
                lines[index], "ocr_cross_checked", scope,
            )
            if found:
                return found.model_copy(update={
                    "evidence": [item.model_copy(update={"confidence": record.ocr_confidence or 0.0}) for item in found.evidence]
                })
        value_lines = lines[index + 1:index + 7]
        values = normalize_triplet(decimals(value_lines))
        if values:
            raw = " | ".join([lines[index]] + value_lines)
            found = _measurement(values[:3], record.number, "OCR semantic dimension block", raw, "ocr_cross_checked", scope)
            if found:
                return found.model_copy(update={
                    "evidence": [item.model_copy(update={"confidence": record.ocr_confidence or 0.0}) for item in found.evidence]
                })
    return None


def _declared_body_pair(
    records: list[PageRecord], family: str, pin_count: int
) -> tuple[Measurement | None, Measurement | None]:
    """Use an explicit cover/pin-page declaration such as QFN40(5 mm x 5 mm)."""
    count = str(pin_count) if pin_count else r"\d{1,4}"
    family_alias = {
        "BGA": r"(?:DSBGA|WFBGA|FCBGA|UFBGA|TFBGA|VFBGA|PBGA|CBGA|FBGA|BGA)",
        "WLCSP": r"(?:WLCSP|WCSP(?:\d+F)?|WLP|PICOSTAR|CSP)",
    }.get(family, re.escape(family))
    identity = rf"(?:{family_alias}[-_ ]?{count}|{count}\s*(?:pin|ball|bump)?s?\s*{family_alias})"
    pattern = re.compile(
        rf"\b{identity}\b.{{0,260}}?\(\s*(\d+(?:\.\d+)?)\s*mm\s*[xX×]\s*(\d+(?:\.\d+)?)\s*mm",
        re.I,
    )
    for record in records:
        normalized_overlay = _collapse_doubled_overlay_tokens(record.text)
        match = pattern.search(record.text) or pattern.search(normalized_overlay)
        if not match:
            continue
        raw = _clean(match.group(0))
        evidence = [_evidence(record.number, "Declared package size", raw, "pdf_text", 0.97, "package_outline")]
        width = Measurement(value_mm=float(match.group(1)), original_value=raw, original_unit="mm", evidence=evidence)
        length = Measurement(value_mm=float(match.group(2)), original_value=raw, original_unit="mm", evidence=evidence)
        return width, length
    compact_square = re.compile(
        rf"\b{identity}\b.{{0,120}}?\(\s*(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)\s*mm(?:2|²)",
        re.I,
    )
    for record in records:
        normalized_overlay = _collapse_doubled_overlay_tokens(record.text)
        match = compact_square.search(record.text) or compact_square.search(normalized_overlay)
        if match:
            raw = _clean(match.group(0))
            evidence = [_evidence(record.number, "Declared package body area", raw, "pdf_text", 0.97, "package_outline")]
            return (
                Measurement(value_mm=float(match.group(1)), original_value=raw, original_unit="mm", evidence=evidence),
                Measurement(value_mm=float(match.group(2)), original_value=raw, original_unit="mm", evidence=evidence),
            )
    semantic_size = re.compile(
        rf"\b{family_alias}\b.{{0,220}}?(?:dimensions?|size|尺寸)\s*[:：]?\s*"
        r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*[xX×]\s*(\d+(?:\.\d+)?)\s*mm",
        re.I,
    )
    semantic_matches = [
        (record, semantic_size.search(record.text) or semantic_size.search(_collapse_doubled_overlay_tokens(record.text)))
        for record in records
    ]
    semantic_matches = [(record, match) for record, match in semantic_matches if match]
    if len(semantic_matches) == 1:
        record, match = semantic_matches[0]
        assert match is not None
        raw = _clean(match.group(0))
        evidence = [_evidence(record.number, "Declared package dimensions", raw, "pdf_text", 0.94, "package_outline")]
        return (
            Measurement(value_mm=float(match.group(1)), original_value=raw, original_unit="mm", evidence=evidence),
            Measurement(value_mm=float(match.group(2)), original_value=raw, original_unit="mm", evidence=evidence),
        )
    # Generic package-options form used by many vendors:
    # "5.3 mm x 5.3 mm x 0.8 mm, 13 x 13 BGA (169 pins)".
    # The pin count and family on the same short block prevent dimensions from
    # a neighboring package option being attached to this Variant.
    options_pattern = re.compile(
        rf"(\d+(?:\.\d+)?)\s*mm\s*[xX×]\s*(\d+(?:\.\d+)?)\s*mm"
        rf".{{0,140}}?\b{re.escape(family)}\b.{{0,80}}?\b{count}\s*(?:pins?|balls?|bumps?)\b",
        re.I,
    )
    for record in records:
        match = options_pattern.search(record.text)
        if not match:
            continue
        raw = _clean(match.group(0))
        evidence = [_evidence(record.number, "Package options declared size", raw, "pdf_text", 0.97, "package_outline")]
        return (
            Measurement(value_mm=float(match.group(1)), original_value=raw, original_unit="mm", evidence=evidence),
            Measurement(value_mm=float(match.group(2)), original_value=raw, original_unit="mm", evidence=evidence),
        )
    return None, None


def _declared_tuple_height(records: list[PageRecord], family: str, pin_count: int) -> Measurement | None:
    """Read the third value from an explicit ``WLP (X x Y x H)`` identity."""
    if not pin_count:
        return None
    family_alias = {
        "BGA": r"(?:DSBGA|WFBGA|FCBGA|UFBGA|TFBGA|VFBGA|PBGA|CBGA|FBGA|BGA)",
        "WLCSP": r"(?:WLCSP|WCSP(?:\d+F)?|WLP|PICOSTAR|CSP)",
    }.get(family, re.escape(family))
    identity = rf"(?:{family_alias}[-_ ]?{pin_count}|{pin_count}\s*(?:pin|ball|bump)?s?\s*{family_alias})"
    pattern = re.compile(
        rf"\b{identity}\b.{{0,260}}?\(\s*\d+(?:\.\d+)?\s*mm\s*[xX×]\s*"
        rf"\d+(?:\.\d+)?\s*mm\s*[xX×]\s*(\d+(?:\.\d+)?)\s*mm",
        re.I,
    )
    for record in records:
        normalized = _collapse_doubled_overlay_tokens(record.text)
        match = pattern.search(record.text) or pattern.search(normalized)
        if match:
            raw = _clean(match.group(0))
            return Measurement(
                value_mm=float(match.group(1)), original_value=raw, original_unit="mm",
                evidence=[_evidence(record.number, "Declared package height", raw, "pdf_text", 0.97, "package_outline")],
            )
    return None


def _inline_pitch_measurement(records: list[PageRecord], family: str, pages: list[int]) -> Measurement | None:
    for record in records:
        if record.number not in pages or family not in record.families:
            continue
        text = _collapse_doubled_overlay_tokens(record.text)
        patterns = (
            r"(?:ball|bump|terminal)?\s*pitch\s*[:=]?\s*(\d+(?:\.\d+)?)\s*mm",
            r"(?:ball|bump|terminal|grid)(?:\s+\w+){0,5}\s+pitch\s+(?:is|of)\s+(\d+(?:\.\d+)?)\s*mm",
            r"(\d+(?:\.\d+)?)\s*mm\s*(?:ball|bump|terminal)?\s*pitch\b",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                raw = _clean(match.group(0))
                return Measurement(
                    value_mm=float(match.group(1)), original_value=raw, original_unit="mm",
                    evidence=[_evidence(record.number, "Inline package pitch", raw, "pdf_text", 0.96, "component_terminal")],
                )
    return None


def _body_pair(records: list[PageRecord], page: int) -> tuple[Measurement | None, Measurement | None]:
    record = next((item for item in records if item.number == page), None)
    if record and re.search(r'D1\s*(?:and|,|/)\s*E1.{0,100}(?:mold|body)', _clean(record.text), re.I):
        # The drawing explicitly defines the symbols; QFP D/E often include
        # leads. Package-name conventions alone are not sufficient evidence.
        return (_row_measurement(records,page,('body dimensions',),('D1',),max_distance=0),
                _row_measurement(records,page,('body dimensions',),('E1',),max_distance=0))
    width = _row_measurement(records, page, ("body size", "package size"), ("D",))
    length = _row_measurement(records, page, ("body size", "package size"), ("E",))
    record = next((item for item in records if item.number == page), None)
    if record:
        width = width or _drawing_range(record, "D")
        length = length or _drawing_range(record, "E")
    width = width or _ocr_labeled_measurement(records, page, ("body size", "package size"), "D", "package_outline")
    length = length or _ocr_labeled_measurement(records, page, ("body size", "package size"), "E", "package_outline")
    return width, length


def _package_body_xy_pair(records: list[PageRecord], page: int) -> tuple[Measurement | None, Measurement | None]:
    """Extract X/Y body rows used by CSP/WLCSP vendor drawings."""
    record = next((item for item in records if item.number == page), None)
    if record is None or "package body" not in record.text.lower():
        return None, None
    normalized = _clean(record.text)
    sections = re.findall(r"Package\s+body(.{0,100}?)(?=Package\s+body|Package\s+Height)", normalized, re.I)
    measurements: list[Measurement] = []
    seen_values: set[tuple[float, ...]] = set()
    for section in sections:
        values = [value for value in _min_nom_max_ocr_values(section) if value >= 0.1]
        selected = tuple(values[:3] if len(values) >= 3 else values[:2])
        if len(selected) >= 2 and selected not in seen_values:
            seen_values.add(selected)
            found = _measurement(list(selected), page, "Package body dimension X/Y", "Package body" + section, "ocr", "package_outline")
            if found is not None:
                measurements.append(found)
        if len(measurements) >= 2:
            break
    return (
        measurements[0] if measurements else None,
        measurements[1] if len(measurements) > 1 else None,
    )


def _semantic_parameter_measurement(
    records: list[PageRecord], page: int, labels: tuple[str, ...], scope: str = "package_outline"
) -> Measurement | None:
    """Read a named Min/Nominal/Max row from flattened or OCR table text."""
    record = next((item for item in records if item.number == page), None)
    if record is None:
        return None
    label_pattern = "|".join(re.escape(label) for label in labels)
    for match in re.finditer(
        rf"(?:{label_pattern})\s+((?:\d+(?:\.\d+)?\s+){{1,3}})",
        _clean(record.text),
        re.I,
    ):
        values = _min_nom_max_ocr_values(match.group(1))
        found = _measurement(values, page, "Package parameter table", match.group(0), "ocr_cross_checked", scope)
        if found is not None:
            return found
    return None


def _part_numbers(records: list[PageRecord], page: int, family: str, stem: str, pin_count: int) -> tuple[str, list[str]]:
    # Explicit ordering options bind suffix to package. Ranking all part
    # numbers together can attach the BGA order code to the CSP candidate.
    family_alias = r"(?:WLCSP|WCSP|CSP)" if family == "WLCSP" else re.escape(family)
    ordering = list(dict.fromkeys(
        match[1] for record in records for match in re.finditer(
            rf"\b([A-Z][A-Z0-9]{{2,}}(?:-[A-Z0-9]{{2,}})+)\s*\(\s*{family_alias}\s*\)", record.text
        )
    ))
    if len(ordering) == 1:
        return ordering[0], []
    # The source filename is user-controlled document identity and is much less
    # likely than arbitrary body text to be a register field (BP0-BP3), page
    # range (P54-55), dimension symbol, or package drawing number. Prefer the
    # first mixed alpha/numeric filename token when it is not generic metadata.
    filename_tokens = re.findall(r"(?i)(?<![A-Z0-9])[A-Z][A-Z0-9-]{3,}(?![A-Z0-9])", stem)
    generic_filename = re.compile(r"^(?:DATASHEET|DATA|SHEET|REV|VERSION|VER|FINAL|PRELIMINARY|V?\d+(?:-\d+)*)$", re.I)
    filename_part = next((
        token for token in filename_tokens
        if any(char.isalpha() for char in token)
        and any(char.isdigit() for char in token)
        and not generic_filename.match(token)
        and not re.fullmatch(r"(?:BP|BIT|PAGE|P)\d+(?:-(?:BP|BIT|PAGE|P)?\d+)?", token, re.I)
    ), None)
    filename_segments = [
        segment for token in filename_tokens for segment in re.split(r"[-_]", token)
        if any(char.isalpha() for char in segment) and any(char.isdigit() for char in segment)
    ]
    identity_text = "\n".join(record.text for record in records[:5])
    confirmed_filename_parts = [
        token for token in [*filename_segments, *(filename_tokens if filename_part else [])]
        if re.search(rf"(?<![A-Z0-9]){re.escape(token)}(?![A-Z0-9])", identity_text, re.I)
    ]
    confirmed_filename_part = max(confirmed_filename_parts, key=len) if confirmed_filename_parts else None
    tokens: list[str] = []
    by_page = {record.number: record for record in records}
    search_pages = list(dict.fromkeys([1, 2, 3, page, page + 1, page - 1, page + 2, page - 2, page - 3]))
    for number in search_pages:
        record = by_page.get(number)
        if record:
            found = re.findall(r"\b[A-Z][A-Z0-9]{2,}(?:-[A-Z0-9]{2,})*\b", record.text)
            if found:
                tokens.extend(found)
    # Datasheet document IDs usually contain DS/DOC and are not orderable parts.
    tokens = list(dict.fromkeys(token for token in tokens if any(char.isdigit() for char in token) and not re.match(r"^(?:DS|DOC|AN)-", token, re.I)))
    if tokens:
        counts = Counter(re.findall(r"\b[A-Z][A-Z0-9]{2,}(?:-[A-Z0-9]{2,})*\b", "\n".join(record.text for record in records[:5])))
        def rank(token: str) -> tuple[int, int, int]:
            package_like = bool(re.fullmatch(
                r"(?:[A-Z]{2,5}\d{4}|(?:CSP|WLCSP|WCSP|DFN|SON|QFN|BGA|LGA|QFP|SOIC)-?\d{1,4})",
                token,
                re.I,
            ))
            document_like = bool(re.match(r"^(?:ZH|SN|SL|REV|ISO|ASME|STD|IPC|JEDEC)", token))
            orderable_suffix = "-" in token
            return (counts[token] * 10 + orderable_suffix * 1000 - package_like * 30 - document_like * 1000, len(token), -tokens.index(token))
        chosen = max(tokens, key=rank)
        noisy_body_token = bool(re.fullmatch(
            r"(?:BP|BIT|PAGE|P)\d+(?:-(?:BP|BIT|PAGE|P)?\d+)?",
            chosen,
            re.I,
        ))
        chosen_is_package_identity = bool(re.fullmatch(
            r"(?:[A-Z]{2,5}\d{4}|(?:CSP|WLCSP|WCSP|DFN|SON|QFN|BGA|LGA|QFP|SOIC)-?\d{1,4})",
            chosen,
            re.I,
        ))
        if confirmed_filename_part:
            chosen = (
                filename_part
                if filename_part and filename_part.upper().startswith(confirmed_filename_part.upper() + "-")
                else confirmed_filename_part
            )
        elif filename_part and (noisy_body_token or chosen_is_package_identity):
            chosen = filename_part
        root = chosen.split("-")[0][:5]
        compatible = [token for token in tokens if token != chosen and token.startswith(root)]
        return chosen, compatible[:50]
    suffix = f"-{family}{pin_count}" if pin_count else f"-{family}"
    return f"{stem[:60]}{suffix}", []


def _manufacturer(records: list[PageRecord]) -> str:
    text = "\n".join(record.text for record in records[:5])
    known = ("Realtek", "OSEMITECH", "Texas Instruments", "Analog Devices", "NXP", "Infineon", "STMicroelectronics", "Microchip", "Renesas", "onsemi", "Qualcomm", "Broadcom", "Ambiq Micro", "MediaTek", "Nordic Semiconductor", "Monolithic Power Systems", "Zhejiang Shengyan Technology Co., Ltd")
    return next((name for name in known if name.lower() in text.lower()), "未识别")


def _find_text_evidence(records: list[PageRecord], family: str, cue: str) -> tuple[int, str] | None:
    pattern = re.compile(cue, re.I)
    for record in records:
        if family in record.families:
            lines = record.text.splitlines()
            for index in range(len(lines)):
                excerpt = _clean(" ".join(lines[index:index + 3]))
                if pattern.search(excerpt):
                    return record.number, excerpt
            normalized = _clean(record.text)
            match = pattern.search(normalized)
            if match:
                start = max(0, match.start() - 120)
                return record.number, normalized[start:match.end() + 160]
    return None


def _array_pin1_evidence(records: list[PageRecord], family: str, expected_count: int) -> tuple[int, str] | None:
    from .pin_reconciliation import collect_array_maps
    for mapping in collect_array_maps(records, family):
        populated = [address for address, cell in mapping['cells'].items() if cell['kind'] != 'unpopulated']
        if (not mapping['issues'] and len(populated) == expected_count and mapping['rows'][0] == 'A'
                and 'A1' in populated):
            return mapping['page'], 'Component Top View: A1 upper left; ordered row/column labels in ' + mapping['cells']['A1']['figure']
    for record in records:
        if family not in record.families:
            continue
        shape = (_pin_map_shape(record) if 'isolated_complete_array_assignment' in record.roles
                 else _drawing_pin_map_shape(record, expected_count))
        normalized = _clean(record.text)
        explicit_a1 = re.search(
            r"\b(?:PIN\s*)?A1\s+(?:PIN\s*)?INDEX\b|\bA1\s+CORNER\b",
            normalized,
            re.I,
        )
        ordered_a1_axis = bool(shape and shape[1] and shape[1][0] == "A" and shape[2] >= 1)
        if shape and re.search(r"\bTOP\s+VIEW\b", normalized, re.I) and (explicit_a1 or ordered_a1_axis):
            return (
                record.number,
                "Component Top View: A1 is established by the explicit index or ordered A.. / 1.. grid axes",
            )
    for record in records:
        if family not in record.families:
            continue
        text = _clean(record.text)
        if expected_count and not re.search(rf"\b[A-Z]{{2,5}}0*{expected_count}\b", text):
            continue
        match = re.search(r"(?:BALL\s+A[1IT]|A[1IT]\s+BALL).{0,30}CORNER|CORNER.{0,30}(?:BALL\s+A[1IT]|A[1IT]\s+BALL)", text, re.I)
        if match:
            raw = _clean(match.group(0)).replace("AI", "A1").replace("At", "A1")
            return record.number, f"Component Top View: BALL A1 CORNER. Original drawing text: {raw}"
    return None


def _perimeter_pin_map_evidence(
    records: list[PageRecord], family: str, expected_count: int
) -> tuple[tuple[int, str], tuple[int, str]] | None:
    """Normalize a four-sided component-top pin map from its number matrix."""
    if not expected_count or expected_count % 4:
        return None
    side = expected_count // 4
    top = " ".join(str(value) for value in range(expected_count, expected_count - side, -1))
    bottom = " ".join(str(value) for value in range(side + 1, side * 2 + 1))
    for record in records:
        if family not in record.families or not re.search(r"top\s*view\s*pin\s*assignments?", record.text, re.I):
            continue
        normalized = _clean(record.text)
        if top not in normalized or bottom not in normalized:
            continue
        if not re.search(rf"(?:^|\s)1\s+{expected_count - side}(?:\s|$)", normalized):
            continue
        return (
            (record.number, "Component Top View：Pin 1位于左上侧；原图外围编号从Pin 1开始逆时针。"),
            (
                record.number,
                f"Component Top View：{expected_count}个外围Pin按逆时针编号；顶部为{expected_count}至{expected_count - side + 1}，"
                f"左侧为1至{side}，底部为{side + 1}至{side * 2}。",
            ),
        )
    return None


def _isolated_array_assignment(record: PageRecord, candidate: PackageCandidate) -> PageRecord | None:
    """Isolate a complete, explicitly oriented assignment from unrelated prose.

    Caller must establish a unique document variant. Never bring thermal-board
    dimensions or OCR evidence from the rest of this page into that variant.
    """
    headings = list(re.finditer(r'(?im)^\s*(?:\d+[.]?\s+)?Pin\s+Assignments?\s*\(top\s+view\)\s*$', record.text))
    if len(headings) != 1:
        return None
    block = record.text[headings[0].start():].split('[OCR PACKAGE PAGE]', 1)[0]
    block = re.split(r'\n\s*\d+[.]?\s+[A-Za-z]', block, maxsplit=1)[0]
    if identify_package_families(block):
        return None  # An explicitly scoped package needs the normal variant path.
    matches = []
    for table in record.tables:
        if len(table) < 3 or len(table[0]) < 3 or _clean(table[0][0]):
            continue
        columns = [_clean(c) for c in table[0][1:]]
        if columns != [str(i) for i in range(1, len(columns) + 1)]:
            continue
        rows = [_clean(row[0]) for row in table[1:] if row]
        if rows != list(JEDEC_ROWS[:len(rows)]) or len(rows)*len(columns) != candidate.pin_count:
            continue
        if any(len(row) != len(columns)+1 or not all(_clean(c) for c in row) for row in table[1:]):
            continue
        if any(_clean(' '.join(str(c or '') for c in row)) not in _clean(block) for row in table[1:]):
            continue
        if re.search(r'NO\s*BALL|UNPOPULATED|DEPOPULATED|MISSING\s*BALL|DNP', block, re.I):
            continue
        matches.append(table)
    if len(matches) != 1:
        return None
    return PageRecord(record.number, block, tables=matches, families={candidate.family},
                      roles={'pin_map', 'isolated_complete_array_assignment'})


def _variant_records(records: list[PageRecord], candidate: PackageCandidate) -> list[PageRecord]:
    """Exclude explicit competing package drawings within the same family.

    Codes are discovered from this document, never a device answer registry.
    Preserve page order for document identity and continuation-table readers.
    """
    discovered = _discover(records)
    identities = {(item.family, item.pin_count, item.package_code) for item in discovered if item.package_code and item.pin_count}
    if candidate.family in ARRAY_FAMILIES and identities == {(candidate.family, candidate.pin_count, candidate.package_code)}:
        records = [(_isolated_array_assignment(record, candidate) or record)
                   if candidate.family not in record.families else record for record in records]
    other_codes = {item.package_code for item in discovered
                   if item.family == candidate.family and item.package_code
                   and item.package_code != candidate.package_code}

    def mentions(text, code):
        return bool(code and re.search(r'(?<![A-Za-z0-9])' + re.escape(code) + r'(?![A-Za-z0-9])', text, re.I))

    return [record for record in records
            if record.number == candidate.page or (
                (not record.families or candidate.family in record.families)
                and (not any(mentions(record.text, code) for code in other_codes)
                     or mentions(record.text, candidate.package_code)))]


def _candidate_spec(path: Path, records: list[PageRecord], candidate: PackageCandidate) -> FootprintSpec:
    family, page = candidate.family, candidate.page
    # Neighboring pages are not permission to borrow another package's body.
    # Unlabelled continuation pages remain available; explicit other families
    # cannot supply this candidate's mechanical dimensions or topology.
    records = _variant_records(records, candidate)
    body_w, body_l = _body_pair(records, page)
    xy_body_bound = False
    if family in ARRAY_FAMILIES:
        xy_width, xy_length = _package_body_xy_pair(records, page)
        body_w, body_l = xy_width or body_w, xy_length or body_l
        xy_body_bound = xy_width is not None or xy_length is not None
    declared_w, declared_l = _declared_body_pair(records, family, candidate.pin_count)

    def reconcile_body(extracted: Measurement | None, declared: Measurement | None) -> Measurement | None:
        if declared is None:
            return extracted
        if extracted is None:
            return declared
        def representative(item: Measurement) -> float | None:
            if item.value_mm is not None:
                return item.value_mm
            if item.nominal_mm is not None:
                return item.nominal_mm
            if item.min_mm is not None and item.max_mm is not None:
                return (item.min_mm + item.max_mm) / 2
            return item.min_mm if item.min_mm is not None else item.max_mm

        extracted_value = representative(extracted)
        declared_value = representative(declared)
        # An explicit package declaration such as QFN40(5 mm x 5 mm) is an
        # independent document constraint. Reject a semantic OCR binding that
        # differs materially (for example E accidentally bound to pitch 0.4).
        if extracted_value is None or declared_value is None or abs(extracted_value - declared_value) > max(0.10, declared_value * 0.05):
            return declared
        return extracted

    body_w, body_l = reconcile_body(body_w, declared_w), reconcile_body(body_l, declared_l)
    marker = f"{family}{candidate.pin_count}" if candidate.pin_count else family
    body_w = body_w or _ocr_symbol_measurement(records, page, marker, "D", "package_outline")
    body_l = body_l or _ocr_symbol_measurement(records, page, marker, "E", "package_outline")
    nearby_pages = list(range(max(1, page - 1), page + 4))
    package_height = _declared_tuple_height(records, family, candidate.pin_count)
    if family in ARRAY_FAMILIES:
        package_height = package_height or _drawing_max_height(records, nearby_pages)
    package_height = package_height or _semantic_parameter_measurement(records, page, ("package height", "body height"))
    package_height = package_height or _row_measurement(records, page, ("package height", "body height"), ("A",), "package_outline")
    package_height = package_height or _ocr_symbol_measurement(records, page, marker, "A", "package_outline")
    local_table_distance = 0 if family in ARRAY_FAMILIES else 1
    pitch = _row_measurement(
        records, page, ("pitch", "ball/bump pitch"), ("e",), "component_terminal", local_table_distance
    )
    terminal_w = _row_measurement(
        records, page,
        ("terminal width", "lead width", "ball/bump width", "ball diameter", "bump diameter"),
        ("b",), "component_terminal", local_table_distance,
    )
    terminal_l = None if family in ARRAY_FAMILIES else _row_measurement(records, page, ("terminal length", "lead length"), ("L",), "component_terminal")
    pitch = pitch or _ocr_symbol_measurement(records, page, marker, "e", "component_terminal")
    terminal_w = terminal_w or _ocr_symbol_measurement(records, page, marker, "b", "component_terminal")
    if family not in ARRAY_FAMILIES:
        terminal_l = terminal_l or _ocr_symbol_measurement(records, page, marker, "L", "component_terminal")
    pitch = pitch or _ocr_labeled_measurement(records, page, ("lead pitch", "terminal pitch", "ball pitch"), "e", "component_terminal")
    terminal_w = terminal_w or _ocr_labeled_measurement(
        records, page, ("lead width", "terminal width", "ball width", "ball diameter", "bump diameter"),
        "b", "component_terminal",
    )
    if family not in ARRAY_FAMILIES:
        terminal_l = terminal_l or _ocr_labeled_measurement(records, page, ("lead length", "terminal length"), "L", "component_terminal")

    def variant_measurement(symbol: str, scope: str) -> Measurement | None:
        ordered_pages = [page] + [item.number for item in records if item.number != page and item.ocr_confidence is not None]
        return next((
            found for dimension_page in ordered_pages
            for found in [_ocr_variant_table_measurement(records, dimension_page, family, candidate.pin_count, symbol, scope)]
            if found is not None
        ), None)

    variant_d = variant_measurement("D", "package_outline")
    variant_e = variant_measurement("E", "package_outline")
    variant_pitch = variant_measurement("e", "component_terminal")
    variant_b = variant_measurement("b", "component_terminal")
    variant_l = variant_measurement("L", "component_terminal")
    body_w = reconcile_body((body_w if xy_body_bound else variant_d or body_w), declared_w)
    body_l = reconcile_body((body_l if xy_body_bound else variant_e or body_l), declared_l)
    pitch, terminal_w = variant_pitch or pitch, variant_b or terminal_w
    if family not in ARRAY_FAMILIES:
        terminal_l = variant_l or terminal_l

    # Package identity, dimension table and pin map are often split across
    # consecutive pages. Resolve the local document cluster generically instead
    # of treating the highest-scoring identity page as the only source page.
    count_pattern = re.compile(
        rf"(?:\b{re.escape(family)}\b.{{0,80}}\b{candidate.pin_count}\b|"
        rf"\b{candidate.pin_count}\b.{{0,80}}\b(?:pin|ball|bump|terminal|lead)s?\b|"
        rf"\b(?:pin|ball|bump|terminal|lead)\s*(?:count)?\s*\w*\s*{candidate.pin_count}\b)",
        re.I | re.S,
    ) if candidate.pin_count else None
    adjacent_pages = [
        item.number for item in records
        if (
            abs(item.number - page) <= 3
            and (family in item.families or "dimension_candidate" in item.roles)
        ) or (
            count_pattern is not None
            and bool(count_pattern.search(item.text))
            and "dimension_candidate" in item.roles
        )
    ]
    for dimension_page in sorted(set(adjacent_pages), key=lambda number: (abs(number - page), number)):
        if body_w is None or body_l is None:
            nearby_w, nearby_l = _body_pair(records, dimension_page)
            body_w, body_l = body_w or nearby_w, body_l or nearby_l
        pitch = pitch or _ocr_labeled_measurement(
            records, dimension_page, ("lead pitch", "terminal pitch", "ball pitch", "ball/bump pitch"), "e", "component_terminal"
        )
        terminal_w = terminal_w or _ocr_labeled_measurement(
            records, dimension_page,
            ("lead width", "terminal width", "ball width", "ball/bump width", "ball diameter", "bump diameter"),
            "b", "component_terminal"
        )
        if family not in ARRAY_FAMILIES:
            terminal_l = terminal_l or _ocr_labeled_measurement(
                records, dimension_page, ("lead length", "terminal length"), "L", "component_terminal"
            )
    perimeter_land = _manufacturer_perimeter_land(records, family, page)
    if family in ARRAY_FAMILIES:
        # D2/E2 on array-package drawings are body/grid dimensions, not an
        # exposed pad. Only a dedicated array-EP extractor may bind an EP
        # after the drawing explicitly labels exposed or thermal metal.
        exposed_w = None
        exposed_l = None
    elif perimeter_land is not None and family in {"DFN", "SON"}:
        # This drawing style identifies the center exposed metal as D1/E1;
        # D2 belongs to the PCB land envelope and must never become EP width.
        exposed_w = variant_measurement("D1", "package_outline")
        exposed_l = variant_measurement("E1", "package_outline")
    else:
        exposed_w = (
            _row_measurement(records, page, ("ep size", "exposed pad"), ("D2",), "package_outline")
            or variant_measurement("D2", "package_outline")
            or _ocr_symbol_measurement(records, page, marker, "D2", "package_outline")
            or _ocr_labeled_measurement(records, page, ("ep size", "exposed pad"), "D2", "package_outline")
        )
        exposed_l = (
            _row_measurement(records, page, ("ep size", "exposed pad"), ("E2",), "package_outline")
            or variant_measurement("E2", "package_outline")
            or _ocr_symbol_measurement(records, page, marker, "E2", "package_outline")
            or _ocr_labeled_measurement(records, page, ("ep size", "exposed pad"), "E2", "package_outline")
        )
    side_pin_counts: list[int] = []
    multirow_variant = family not in ARRAY_FAMILIES and any(
        variant_measurement(symbol, "component_terminal") is not None for symbol in ("b2", "b3", "e1")
    )
    if family not in ARRAY_FAMILIES and not multirow_variant and pitch is not None:
        nd = variant_measurement("Nd", "component_terminal")
        ne = variant_measurement("Ne", "component_terminal")

        def scalar(item: Measurement | None) -> float | None:
            if item is None:
                return None
            return item.value_mm if item.value_mm is not None else (
                item.nominal_mm if item.nominal_mm is not None else (
                    (item.min_mm + item.max_mm) / 2 if item.min_mm is not None and item.max_mm is not None else None
                )
            )

        pitch_value, nd_value, ne_value = scalar(pitch), scalar(nd), scalar(ne)
        if pitch_value and nd_value and ne_value:
            horizontal = round(nd_value / pitch_value) + 1
            vertical = round(ne_value / pitch_value) + 1
            if abs((horizontal - 1) * pitch_value - nd_value) <= 0.02 and abs((vertical - 1) * pitch_value - ne_value) <= 0.02:
                side_pin_counts = [vertical, horizontal, vertical, horizontal]
    rows: list[str] = []
    columns: int | None = None
    missing_positions: list[str] = []
    pin_count = candidate.pin_count
    pin_map_page: int | None = None
    if family in ARRAY_FAMILIES:
        nearest_topology: tuple[int, list[str], int, list[str], int] | None = None
        for record in records:
            if family not in record.families:
                continue
            topology = _pin_map_topology(record, pin_count)
            if topology:
                topology_rows, topology_columns, topology_missing = topology
                item = (abs(record.number - page), topology_rows, topology_columns, topology_missing, record.number)
                if nearest_topology is None or item[0] < nearest_topology[0]:
                    nearest_topology = item
        if nearest_topology:
            _, rows, columns, missing_positions, pin_map_page = nearest_topology
        nearest: tuple[int, int, list[str], int, int] | None = None
        for record in (records if not rows else ()):
            if family not in record.families:
                continue
            shape = (
                _pin_map_shape(record)
                or _explicit_coordinate_shape(record, pin_count)
                or _drawing_pin_map_shape(record, pin_count)
            )
            if shape:
                if pin_count and shape[0] != pin_count:
                    continue
                item = (abs(record.number - page), shape[0], shape[1], shape[2], record.number)
                if nearest is None or item[0] < nearest[0]:
                    nearest = item
        if nearest and not rows:
            _, map_count, rows, columns, pin_map_page = nearest
            pin_count = pin_count or map_count
        if not pin_count:
            record = next((item for item in records if item.number == page), None)
            if record:
                code_match = re.search(r"\b([A-Z]{2,5})(\d{4})\b", record.text)
                repeat_counts = [int(item) for item in re.findall(r"\b(\d{2,4})X\b", record.text)]
                if code_match:
                    candidate.package_code = code_match.group(0)
                    pin_count = int(code_match.group(2))
                elif repeat_counts:
                    pin_count = max(repeat_counts)
        if not candidate.package_code:
            record = next((item for item in records if item.number == page), None)
            if record:
                code_match = re.search(r"\b([A-Z]{2,5})(\d{4})\b", record.text)
                if code_match:
                    candidate.package_code = code_match.group(0)
        if pin_count and (not rows or not columns):
            for record in records:
                shape = _explicit_coordinate_shape(record, pin_count) or _drawing_pin_map_shape(record, pin_count)
                if shape:
                    _, rows, columns = shape
                    pin_map_page = record.number
                    break
        # A sentence that explicitly names ball/grid pitch is stronger than a
        # symbol-table OCR result. This corrects rows where OCR appends the
        # following tolerance (for example e=0.35 being misread as 0.10).
        explicit_pitch = _inline_pitch_measurement(records, family, nearby_pages)
        explicit_pitch = explicit_pitch or _inline_pitch_measurement(records, family, [item.number for item in records])
        pitch = explicit_pitch or pitch
        # Some compact CSP drawings provide only ``0.3 TYP`` next to the pitch
        # dimension. Keep this last-resort path, then reject implausibly tiny
        # non-semantic TYP values below (often filter thickness or standoff).
        pitch = pitch or _drawing_typ_measurement(records, nearby_pages, r"\b(?:TYP|PITCH)\b", "component_terminal")
        if pitch is not None:
            pitch_value = next(
                (value for value in (pitch.value_mm, pitch.nominal_mm, pitch.min_mm, pitch.max_mm) if value is not None),
                None,
            )
            semantic_pitch = any(re.search(r"\bpitch\b", evidence.raw_text, re.I) for evidence in pitch.evidence)
            if pitch_value is not None and pitch_value < 0.03 and not semantic_pitch:
                pitch = None
        record = next((item for item in records if item.number == page), None)
        array_terminal = _array_ball_range(record, pin_count) if record else None
        if array_terminal is None:
            array_terminal = next(
                (found for item in records if item.number in nearby_pages for found in [_array_ball_range(item, pin_count)] if found),
                None,
            )
        # Repeated-ball notation (for example ``30X 0.23 0.27``) is stronger
        # than a generic table row that can accidentally bind datum tolerance
        # such as ``0.015 C A B`` as the ball diameter.
        terminal_w = array_terminal or terminal_w
        if record and record.ocr_tokens and re.search(r"EDGE\s+BALL\s+CENTER\s+TO\s+CENTER", record.text, re.I):
            from .spatial_dimensions import symbol_values, grid_pitch
            # Separate OCR token positions restore the min/max cells that
            # flattened text can merge with symbols or adjacent rows.
            for symbol, field_name in (("A", "height"), ("b", "ball")):
                spatial = symbol_values(record.ocr_tokens, symbol)
                if spatial:
                    value = _measurement(spatial[0], page, "Spatial package dimension table", spatial[1], "ocr", "package_outline" if symbol == "A" else "component_terminal")
                    value = value.model_copy(update={'evidence':[ev.model_copy(update={'confidence':spatial[2]}) for ev in value.evidence]})
                    if field_name == "height":
                        package_height = value
                    else:
                        terminal_w = value
            derived = grid_pitch(record.ocr_tokens, len(rows), columns or 0)
            if derived:
                pitch = Measurement(value_mm=derived[0], original_value=derived[1], original_unit="mm",
                                    evidence=[_evidence(page, "Edge spans cross-checked with pin-map grid", derived[1],
                                                        "ocr_cross_checked", .95, "component_terminal")])
    part_number, compatible = _part_numbers(records, page, family, path.stem, pin_count)
    perimeter_map = None if family in ARRAY_FAMILIES else _perimeter_pin_map_evidence(records, family, pin_count)
    if perimeter_map and family in {'QFN','QFP'}:
        side_pin_counts = [pin_count // 4] * 4  # independently ordered perimeter map proved each side
    spatial_maps = [(record, item) for record in records for item in record.perimeter_maps
                    if item["family"] == family and item["pin_count"] == pin_count]
    if family in {"QFN", "QFP", "SOIC", "DFN"} and len(spatial_maps) == 1:
        map_record, topology = spatial_maps[0]
        side_pin_counts = topology["side_pin_counts"]
        proof = (
            f"Component Top View: {topology['caption']}; Pin 1 at upper left; "
            f"all {pin_count} numbered terminals observed counterclockwise; "
            f"side counts {side_pin_counts}; region {topology['region_pdf_points']}"
        )
        perimeter_map = ((map_record.number, proof), (map_record.number, proof))
    if family in ARRAY_FAMILIES:
        pin1 = _array_pin1_evidence(records, family, pin_count)
        pin1 = pin1 or _find_text_evidence(records, family, r"(?:A1|pin\s*1).*(?:corner|index|top\s*view|bottom\s*view)")
    else:
        pin1 = perimeter_map[0] if perimeter_map else _find_text_evidence(records, family, r"(?:A1|pin\s*1).*(?:corner|index|top\s*view|bottom\s*view)")
    view = perimeter_map[1] if perimeter_map else _find_text_evidence(records, family, r"(?:top|bottom)\s*view")
    if family in ARRAY_FAMILIES and pin1 and 'Component Top View:' in pin1[1]:
        view = pin1
    land = _find_text_evidence(records, family, r"recommended\s+(?:pcb\s+)?land\s+pattern|recommended\s+footprint|example\s+board\s+layout|land\s+pattern\s+example")
    manufacturer_land = (
        (_manufacturer_array_land(records, family, pin_count)
         or _manufacturer_array_opening_land(records, family, pin_count, page))
        if family in ARRAY_FAMILIES and pin_count else perimeter_land
    )
    vector_land = _vector_perimeter_land(records, family, pin_count, pitch, side_pin_counts)
    if vector_land is not None and manufacturer_land is None:
        manufacturer_land = vector_land
    manufacturer_solder_mask, manufacturer_paste_mask = (
        _manufacturer_array_process_rules(records, family, pin_count)
        if family in ARRAY_FAMILIES and pin_count else (None, None)
    )
    board_layout = next((record for record in records if family in record.families and re.search(r"example\s+board\s+layout", record.text, re.I)), None)
    if family in ARRAY_FAMILIES and not view and board_layout and _drawing_pin_map_shape(board_layout, pin_count):
        view = (board_layout.number, "Component Top View / PCB board-layout view: " + _clean(board_layout.text)[:300])
    # Bind standard perimeter-package body symbols by package topology, never
    # by vendor or part number. For dual gull-wing packages D is body length,
    # E is lead span and E1 is moulded-body width. For quad packages D/E are
    # overall spans while D1/E1 are the moulded body. Keeping these roles here
    # prevents PLACE_BOUND from silently using the lead-to-lead envelope.
    if family in {"SOIC", "SOP", "SSOP", "TSSOP", "MSOP", "SOJ"}:
        standard_length = _row_measurement(records, page, ("body dimension",), ("D",), max_distance=0)
        standard_width = _row_measurement(records, page, ("body dimension",), ("E1",), max_distance=0)
        if standard_length and standard_width:
            body_w, body_l = standard_width, standard_length
    elif family in {"QFP", "PLCC"}:
        standard_width = _row_measurement(records, page, ("body dimension",), ("D1",), max_distance=0)
        standard_length = _row_measurement(records, page, ("body dimension",), ("E1",), max_distance=0)
        if standard_width and standard_length:
            body_w, body_l = standard_width, standard_length
    unresolved: list[UnresolvedItem] = []
    if family not in ARRAY_FAMILIES:
        # A drawing may define E as lead span and E1 as moulded body (and
        # D/D1 similarly). Numeric extraction alone cannot establish that
        # role. Hold any unresolved alternative instead of drawing the span
        # as the body. Explicitly labelled body/declared-size evidence is safe.
        for bound in (body_w,body_l):
            if not bound: continue
            for ev in bound.evidence:
                symbol_match = re.match(r'^([DE])\s*\|', ev.raw_text)
                if not symbol_match: continue
                alternate = _row_measurement(records,ev.page,('body dimension',),(symbol_match[1]+'1',),max_distance=0)
                if alternate:
                    unresolved.append(UnresolvedItem(code='BODY_SYMBOL_BINDING_UNRESOLVED',
                        message='圖紙同時定義本體／外跨的替代尺寸符號，尚未證明所選D/E就是本體。',
                        required_action='以尺寸箭頭或明確符號定義核對D/D1及E/E1，禁止將含引腳尺寸作Place Bound。'))
                    break
    # Spatial parameter tables explicitly name body axes and ball pitch axes.
    # Keep channels independent and accept only matching values for a label.
    from .positioned_tables import parameter_rows
    local_record = next((r for r in records if r.number == page), None)
    named = {}
    if local_record:
        channels = [local_record.ocr_tokens] + [region['tokens'] for region in local_record.ocr_table_regions]
        for tokens in channels:
            for row in parameter_rows(tokens):
                label = re.sub(r'\s+', ' ', row['label'].lower()).strip()
                label = re.sub(r'\s*\(min\)\s*$', '', label)
                named.setdefault(label, []).append(row)
    def parameter(label, scope):
        rows = named.get(label, [])
        if not rows or any(len({r['values'][key] for r in rows if key in r['values']}) > 1
                           for key in ('min_mm','nominal_mm','max_mm')):
            return None
        # A crop missing NOM is incomplete, not disagreement with a whole-page
        # MIN/NOM/MAX row. Compare shared fields; prefer complete evidence.
        row = max(rows,key=lambda r:(len(r['values']),r['confidence']))
        return Measurement(**row['values'], original_value=row['raw'], original_unit='mm',
            evidence=[_evidence(page,'Spatial named parameter table',row['raw'],'ocr',row['confidence'],scope)])
    body_w = parameter('package body dimension x','package_outline') or body_w
    body_l = parameter('package body dimension y','package_outline') or body_l
    package_height = parameter('package height','package_outline') or package_height
    if family in ARRAY_FAMILIES:
        terminal_w = parameter('bump diameter','component_terminal') or terminal_w
    pitch_x, pitch_y = pitch, pitch
    if family in ARRAY_FAMILIES:
        from .spatial_dimensions import labeled_axis_pitches
        axis_values = labeled_axis_pitches('\n'.join(r.text for r in records if r.number == page))
        named_x = parameter('ball pitch x axis','component_terminal')
        named_y = parameter('ball pitch y axis','component_terminal')
        if named_x and named_y:
            pitch_x, pitch_y = named_x, named_y
        elif set(axis_values) == {'X','Y'}:
            pitch_x = _measurement([axis_values['X'][0]],page,'Explicit X pitch',axis_values['X'][1],scope='component_terminal')
            pitch_y = _measurement([axis_values['Y'][0]],page,'Explicit Y pitch',axis_values['Y'][1],scope='component_terminal')
        elif axis_values or _row_measurement(records,page,('second pitch',),('e1',),'component_terminal',0):
            # A second pitch symbol does not say which coordinate axis it
            # belongs to. Never quietly turn a rectangular grid into a square.
            unresolved.append(UnresolvedItem(code='ARRAY_AXIS_BINDING_UNRESOLVED',
                message='發現分軸Pitch，但X/Y與圖紙方向尚未完整綁定。',required_action='核對X/Y尺寸箭頭；禁止將同一Pitch複製到兩軸。'))
    if multirow_variant:
        unresolved.append(UnresolvedItem(
            code="MULTIROW_QFN_TERMINAL_MAP_REQUIRED",
            message="封装尺寸表包含b2/b3/e1等多排端子定义，普通四边单排拓扑不适用。",
            required_action="从Bottom View逐端子建立坐标与编号，并完成图形复核后再生成。",
        ))
    required = (("PIN_COUNT", pin_count), ("PITCH", pitch), ("TERMINAL_WIDTH", terminal_w))
    if family not in ARRAY_FAMILIES:
        required += (("BODY_WIDTH", body_w), ("BODY_LENGTH", body_l))
    for code, value in required:
        if not value:
            unresolved.append(UnresolvedItem(code=f"{code}_UNRESOLVED", message=f"未能从封装候选页可靠提取{code}。", required_action="在PDF原图标注该尺寸；禁止以猜测值生成。"))
    if family not in ARRAY_FAMILIES and not terminal_l:
        unresolved.append(UnresolvedItem(code="TERMINAL_LENGTH_UNRESOLVED", message="端子长度未可靠提取。", required_action="在Package Outline中标注端子长度。"))
    if family in ARRAY_FAMILIES:
        if not body_w:
            unresolved.append(UnresolvedItem(code="BODY_WIDTH_UNRESOLVED", severity="warning", message="原厂Die-size阵列图未给出固定本体宽度D；铜盘阵列不依赖该值。", required_action="Assembly/Place Bound按公司规则确认，禁止把载带腔体尺寸当作芯片本体尺寸。"))
        if not body_l:
            unresolved.append(UnresolvedItem(code="BODY_LENGTH_UNRESOLVED", severity="warning", message="原厂Die-size阵列图未给出固定本体长度E；铜盘阵列不依赖该值。", required_action="Assembly/Place Bound按公司规则确认，禁止把载带腔体尺寸当作芯片本体尺寸。"))
    if not land:
        unresolved.append(UnresolvedItem(code="NO_RECOMMENDED_LAND_PATTERN", message="未找到原厂Recommended Land Pattern。", required_action="依据版本化IPC/公司规则计算，并由Layout确认其不是原厂推荐值。"))
    if not pin1:
        unresolved.append(UnresolvedItem(code="PIN1_UNVERIFIED", message="Pin 1方向未独立验证。", required_action="对照Pin Map与Package Drawing确认。"))
    if not view:
        unresolved.append(UnresolvedItem(code="VIEW_CONVENTION_UNRESOLVED", message="Top/Bottom View未验证。", required_action="确认视图后才能生成Pin坐标。"))
    source_evidence: dict[str, list[SourceEvidence]] = {}
    candidate_record = next((item for item in records if item.number == page), None)
    if candidate_record is not None:
        source_evidence["variant_identity"] = [_evidence(
            page,
            "Package option / variant identity",
            _clean(candidate_record.text)[:500],
            "pdf_text",
            0.94,
            "package_outline",
        )]
    if pin_map_page:
        source_evidence["pin_count"] = [_evidence(
            pin_map_page,
            "Pin configuration map",
            f"{len(rows)} rows x {columns} columns - {len(missing_positions)} NC/unpopulated = {pin_count}",
            "table_extract",
            0.96,
            "pin_map",
        )]
    elif pin_count:
        identity = next((
            record for record in records
            if family in record.families and re.search(rf"\b{family}[-_ ]?{pin_count}\b", record.text, re.I)
        ), None)
        if identity:
            source_evidence["pin_count"] = [_evidence(identity.number, "Package identity / Pin information", _clean(identity.text)[:500], scope="pin_map")]
    if pin1:
        source_evidence["pin_1_position"] = [_evidence(pin1[0], "Pin map / package drawing", pin1[1], scope="pin_1")]
    if view:
        source_evidence["pin_numbering"] = [_evidence(view[0], "Pin configuration view", view[1], scope="pin_map")]
    if manufacturer_land:
        source_evidence["recommended_land_pattern"] = list(manufacturer_land.evidence)
    if manufacturer_solder_mask:
        source_evidence["solder_mask_expansion"] = list(manufacturer_solder_mask.evidence)
    if manufacturer_paste_mask:
        source_evidence["paste_mask_rule"] = list(manufacturer_paste_mask.evidence)
    package_dimension_evidence: list[SourceEvidence] = []
    for measurement in (body_w, body_l, package_height, pitch, terminal_w, terminal_l, exposed_w, exposed_l):
        if measurement:
            package_dimension_evidence.extend(measurement.evidence)
    if package_dimension_evidence:
        source_evidence["package_dimensions"] = list({
            (item.page, item.figure_or_table, item.raw_text): item
            for item in package_dimension_evidence
        }.values())
    return FootprintSpec(
        part_number=part_number,
        compatible_part_numbers=compatible,
        manufacturer=_manufacturer(records),
        package_type=family,
        package_code=candidate.package_code,
        source_file=path.name,
        pin_count=pin_count,
        side_pin_counts=side_pin_counts,
        view_convention=("component_top" if view and re.search(r"top\s*view",view[1],re.I)
                         and not re.search(r"bottom\s*view",view[1],re.I) else "unresolved"),
        row_names=rows,
        column_count=columns,
        missing_positions=missing_positions,
        pitch=pitch,
        pitch_x=pitch_x,
        pitch_y=pitch_y,
        body_width=body_w,
        body_length=body_l,
        package_height=package_height,
        terminal_width_min=terminal_w,
        terminal_width_max=terminal_w,
        terminal_length_min=terminal_l,
        terminal_length_max=terminal_l,
        exposed_pad_width=exposed_w,
        exposed_pad_length=exposed_l,
        pin_1_position=pin1[1] if pin1 else None,
        pin_numbering=(f"JEDEC grid {','.join(rows)} × 1..{columns}; " + view[1]) if view and rows and columns else (view[1] if view else None),
        recommended_land_pattern=manufacturer_land or RuleStatus(status="manufacturer_recommended" if land else "missing", evidence=[_evidence(land[0], "Recommended Land Pattern", land[1], scope="land_pattern")] if land else [], note=None if land else "需要依IPC-7351/7352或公司规范计算。", source_tier="device_datasheet" if land else "missing"),
        solder_mask_expansion=manufacturer_solder_mask or RuleStatus(status="missing", note="等待公司Padstack/阻焊规则。"),
        paste_mask_rule=manufacturer_paste_mask or RuleStatus(status="missing", note="等待公司钢网规则。"),
        courtyard=RuleStatus(status="missing", note="等待公司Courtyard规则。"),
        assembly_outline=RuleStatus(
            status="calculated" if body_w and body_l else "missing",
            value={"shape": "body_rectangle"} if body_w and body_l else None,
            note=None if body_w and body_l else "D/E未给数值；仅可显示Ball Land Envelope评审框，不得作为Assembly Outline。",
        ),
        source_evidence=source_evidence,
        confidence={"page_role_classification": 0.88, "table_semantic_extraction": 0.92 if any((body_w, body_l, pitch, terminal_w)) else 0.0},
        unresolved_items=unresolved,
    )


def analyze_universal(pdf_path: str | Path, progress: Callable[[int, int], None] | None = None) -> tuple[list[PageRecord], list[FootprintSpec]]:
    """Discover every package candidate without product-specific routing.

    A new datasheet never requires a product-specific code branch. Image-only
    or ambiguous drawings retain unresolved evidence so the staging pipeline
    can generate a clearly marked PoC while formal publication remains gated.
    """
    from .document_access import require_readable_document
    path = Path(pdf_path)
    require_readable_document(path)
    records = _read_pages(path, progress)
    from .dimension_tables import read_document_tables
    dimensions, schema_issues = read_document_tables(records)
    # Consult original vector strokes only for explicit common-dimension
    # tables; never infer a lost tolerance sign from number magnitudes.
    import pdfplumber
    from .vector_tolerances import read_vector_tolerances
    with pdfplumber.open(path) as pdf:
        for record in records:
            if not re.search(r'COMMON\s+DIMENSIONS', record.text, re.I):
                continue
            recovered = read_vector_tolerances(pdf.pages[record.number-1], record.number)
            for key, value in recovered.items():
                if key not in dimensions.get(record.number, {}):
                    dimensions.setdefault(record.number, {})[key] = value
    for record in records:
        record.dimension_schema_values = dimensions.get(record.number, {})
        record.dimension_schema_issues = [issue for issue in schema_issues if issue["page"] == record.number]
    scan_summary = {
        "full_document_scanned": True,
        "scan_strategy": "two_phase_full_page_inventory_with_candidate_and_sparse_image_ocr",
        "extraction_schedule": "land_outline_priority_then_complete_fallback",
        "native_inventory_only_pages": [r.number for r in records if r.text_layout_audit.get('processing') == 'native_text_inventory'],
        "deep_evidence_pages": [r.number for r in records if r.text_layout_audit.get('processing') == 'deep_evidence'],
        "extraction_page_order": [r.number for r in sorted(records, key=lambda r: r.text_layout_audit.get('extraction_sequence', r.number))],
        "scan_capabilities": ['sparse_outlined_vector_ocr','spatial_dimension_tables','region_ocr_crosscheck'],
        "page_count": len(records),
        "scanned_page_count": len(records),
        "extraction_origin": "generic_evidence_parser",
        "dimension_schema_issues": schema_issues,
        "perimeter_map_evidence": [
            {"page": record.number, **topology}
            for record in records for topology in record.perimeter_maps
        ],
        "vector_land_evidence": [
            {"page": record.number, **pattern}
            for record in records for pattern in record.vector_land_patterns
        ],
        "dimension_table_evidence": [
            {'page':record.number,'scope':table['scope'],'method':table['method'],
             'bbox_pdf_points':[value/(OCR_RENDER_DPI/72 if table['method']=='ocr' else 1) for value in table['bbox']],
             'rows':table['rows'],'review_codes':table.get('review_codes',[]),
             'semantic_binding':'unbound_symbols_not_approved_geometry'}
            for record in records for table in record.located_dimension_tables
        ],
        "ocr_pages": [item.number for item in records if item.ocr_confidence is not None],
        "role_pages": {
            role: [item.number for item in records if role in item.roles]
            for role in (
                "package_outline", "land_pattern", "stencil", "pin_map",
                "mechanical_section", "dimension_candidate",
            )
        },
        "page_object_counts": {
            str(item.number): item.object_counts for item in records if item.roles
        },
    }
    from .pin_reconciliation import reconcile_pin_sources
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    specs = []
    for candidate in _discover(records):
        spec = _candidate_spec(path, records, candidate)
        if candidate.family in {'DIODE','SOT','TO','CONNECTOR'}:
            # Keep raw table evidence, but do not label generic D/E/A as physical
            # body/height on families whose drawing binding is not implemented.
            for field_name in ('body_width','body_length','package_height','pitch','terminal_width_min','terminal_length_min'):
                setattr(spec,field_name,None)
            spec.unresolved_items.append(UnresolvedItem(code='PHYSICAL_SYMBOL_BINDING_REQUIRED',
                message='已辨識封裝型式；尺寸符號與本體／引腳／焊盤的關聯尚未完成圖面驗證。',
                required_action='比對原圖的尺寸線端點與各端子座標；不可套用 D/E 或等距雙排的預設幾何。'))
        used_pages = {candidate.page} | {ev.page for value in (spec.body_width,spec.body_length,spec.pitch,spec.package_height,spec.terminal_width_min,spec.terminal_length_min) if value for ev in value.evidence}
        conflicts = _relevant_dimension_schema_issues(spec, schema_issues, used_pages)
        if conflicts:
            spec.unresolved_items.append(UnresolvedItem(code="DIMENSION_SCHEMA_CONFLICT",
                message="尺寸表有不可讀數字、單位或欄位矛盾；不得以OCR推測值取代。",
                required_action="檢查document_scan.dimension_schema_issues所列的頁／列／原文。"))
        scan = dict(scan_summary)
        if spec.package_type in ARRAY_FAMILIES:
            reconciliation = reconcile_pin_sources(records, spec.package_type, spec.pin_count, source_hash)
            if reconciliation:
                scan["pin_reconciliation"] = reconciliation
                if reconciliation["status"] != "pass":
                    spec.unresolved_items.append(UnresolvedItem(
                        code="PIN_SOURCE_DISAGREEMENT", severity="warning",
                        message="Pin List与Pin Map存在差异；逐球差异已写入pin_review_queue.json。",
                        required_action="EE核对图表差异；图中有证据的球位可进入Layout暂存评审，正式发布仍需确认。",
                    ))
        revisions = scan.get("pin_reconciliation", {}).get("document_revisions", [])
        specs.append(spec.model_copy(update={"document_scan": scan, "source_sha256": source_hash,
                                             "source_revision": revisions[0] if len(revisions) == 1 else spec.source_revision}))
    return records, specs


def extract_universal(pdf_path: str | Path) -> list[FootprintSpec]:
    return analyze_universal(pdf_path)[1]


def write_visual_evidence(pdf_path: str | Path, spec: FootprintSpec, target: str | Path) -> Path:
    """Render every cited PDF page and write the field-to-page review manifest."""
    source = Path(pdf_path)
    destination = Path(target)
    destination.mkdir(parents=True, exist_ok=True)
    citations: dict[int, list[dict[str, object]]] = {}

    def add(field: str, evidence: SourceEvidence) -> None:
        citations.setdefault(evidence.page, []).append({
            "field": field,
            "figure_or_table": evidence.figure_or_table,
            "raw_text": evidence.raw_text,
            "extraction_method": evidence.extraction_method,
            "confidence": evidence.confidence,
        })

    for field in (
        "pitch", "body_width", "body_length", "package_height", "terminal_width_min",
        "terminal_width_max", "terminal_length_min", "terminal_length_max",
        "exposed_pad_width", "exposed_pad_length",
    ):
        measurement = getattr(spec, field)
        if measurement:
            for evidence in measurement.evidence:
                add(field, evidence)
    for field, evidence_items in spec.source_evidence.items():
        for evidence in evidence_items:
            add(field, evidence)

    manifest: list[dict[str, object]] = []
    with pdfplumber.open(source) as pdf:
        for page_number in sorted(citations):
            if page_number < 1 or page_number > len(pdf.pages):
                continue
            image_name = f"page_{page_number:03d}.png"
            _render_ocr_page(pdf.pages[page_number - 1], destination / image_name)
            manifest.append({
                "page": page_number,
                "image": image_name,
                "citations": citations[page_number],
            })
    manifest_path = destination / "visual_evidence_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path
