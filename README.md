# Datasheet to Cadence OrCAD & Allegro Library

Experimental, evidence-driven PDF-to-CAD library tooling. Contributions and independent code review are welcome.

The Python engine reads datasheets, extracts package dimensions and pin information, builds a structured model, and produces OrCAD symbol-library or Allegro footprint workflows. The developer may use AI to improve code, but production extraction must not depend on an assistant manually filling answers for individual documents.

**Status: research/engineering preview, not production-qualified autonomous library creation.** This is a sanitized public source snapshot based on v0.5.53rc1 (2026-10-29), not the internal portable executable or a mirror of its data and Git history.

## What works, and what does not

- Native PDF text, positioned tables, OCR fallback and evidence tracking exist.
- Tesseract and RapidOCR integration includes installed-language selection and positional numeric cross-checking. A fallback engine is not independent confirmation.
- Package discovery, variant filtering, dimensional models, geometry audits and CAD generation/readback exist.
- Some QFP, SOIC and BGA/WLCSP variants have passed private Allegro 17.4 native generation/readback. A family name does not mean every variant is supported.
- OrCAD grouping/multipart/order/layout has undergone repeated development and was relatively reliable in private engineering trials. No complete public OLB acceptance rate is claimed.
- Multi-level/cross-page tables, sparse arrays, dimension roles, OCR punctuation, component-vs-land semantics and broad variant coverage remain incomplete.
- New circular-land vector binding and explicit external-package-document matching are present, but are not integrated into the desktop's default generation path.

## Evidence levels: do not mix these numbers

The private evaluation corpus contains 42 wearable-electronics component PDFs. They were development/regression samples, **not proof of retraining the underlying OCR models**.

| Private historical result | Scope |
|---|---|
| Latest complete parser run | 42 PDFs, 54 candidates, 0 parser errors |
| Review-eligible | 13 candidates across 10 PDFs; eligibility is not native success |
| Native PASS from original PDFs | 12 candidates across 9 PDFs, accumulated across runs |
| Explicit official-drawing assistance | 1 additional PDF; not standalone-PDF extraction |
| Additional native warning case | 1 PDF, excluded from PASS |
| Last internal packaged executable check | One 8-pin example, Allegro 17.4, 70 checks, DRC 0 |

These private outcomes are **not reproducible claims for this public snapshot**: documents, extracted answer fixtures, native reference libraries and board-derived defaults are intentionally absent. Native readback proves agreement with the model, not that the model interpreted the source correctly. 17.4 results do not establish 17.2 compatibility.

## Quick start

Python 3.10+; development was primarily on Windows/Python 3.12.

```sh
python -m venv .venv
# Activate the environment for your shell, then:
python -m pip install -e ".[dev]"
python -m pytest -q
# Use a PDF you are legally permitted to process:
ds2allegro inspect /path/to/your.pdf --output output/inspection
```

The public synthetic suite was run locally: **89 passed, 0 skipped**. It does not require private PDFs or Cadence. This was tested using an existing dependency environment; a clean installation on all platforms has not been certified.

For image PDFs, install Tesseract (and appropriate language packs) and Poppler on PATH. RapidOCR model/runtime dependencies are installed separately through package dependencies; their licenses remain their own. No OCR weights or third-party executables are redistributed here.

For the desktop install `pywebview`, use Windows with WebView2, then `python desktop.py`. Private catalog/template features may be unavailable. No public desktop binary is provided. Native CAD requires your own supported, licensed Cadence installation.

```sh
python scripts/inspect_package_document.py primary.pdf official-drawing.pdf output/association
python -m datasheet_to_allegro.native_verification spec.json output/native /path/to/allegro.exe --target-version 17.4 --gate-mode review
```

The second command consumes a model, not a PDF. Inspect and trace its origin first. It creates real CAD artifacts and invokes your installed application. Review mode does not authorize production publication.

## Read first

- [Architecture and module map](docs/ARCHITECTURE.md)
- [Development history](docs/DEVELOPMENT_HISTORY.md)
- [Known problems and contribution roadmap](docs/KNOWN_ISSUES.md)
- [Public/private differences and exclusions](docs/PUBLICATION_SCOPE.md)
- [Contributing and bug reports](CONTRIBUTING.md)
- [Chinese overview](docs/README.zh-CN.md)

## Principles

No manufacturer/part-number/filename/hash answer dispatch. Package structures and document-declared symbols may guide generic parsing. Keep raw units, tolerance bounds, page/region and source role. Do not infer missing balls by factoring a count, invent NC pins, mix variants, or substitute copper dimensions for component dimensions. Preserve conflicts. Protect OrCAD grouping and layout when changing shared extraction.

Outputs are engineering drafts until reviewed by hardware and PCB-layout engineers. Missing critical pad size, placement, numbering or view evidence must not become guessed geometry.

## License

Project code is available under [MIT](LICENSE). Third-party CAD products, libraries, OCR engines/models, datasheets and standards are not licensed by this repository. No affiliation with or endorsement by Cadence is implied.
