# Parser changes and regression protection

Read `docs/GENERIC_PARSER_REGRESSION_HISTORY.md` before changing PDF/OCR,
geometry, generation gates, or shared pin extraction. Append an entry after
each change with evidence, affected families, tests and unresolved cases.

## Generic rules only

- Never dispatch extraction or geometry by manufacturer, part number, source
  filename, source hash, or a registry of expected answers. These are provenance
  metadata, not parsing inputs. Package families and document-defined symbols
  are valid structural inputs.
- Test a new rule with unrelated identities and a deliberately conflicting
  example. A renamed identical drawing must produce the same physical result.
- Keep measured values, units, tolerance bounds, source page and region. Do not
  convert missing/ambiguous evidence to a typical value just to pass a gate.
- Only bind dimensions and pins within the selected package variant. A shared
  family name alone does not prove that neighboring drawings belong together.
- Preserve offline evidence for regression; production must never load tests,
  reviewed answer fixtures, or corpus snapshots.

## OrCAD protection

Preserve functional grouping, multipart policy, pin order/side and symbol
layout. Changes to these require a demonstrated failing regression. Run
`tests/test_orcad_library.py`, `tests/test_structured_pin_tables.py` and
`tests/test_architecture_contract.py` after touching shared extraction code.

## Evidence for completion

Report parser eligibility separately from actual DRA/OLB generation and native
readback. Never claim 17.2 compatibility from a 17.4 run. Report skipped tests
and missing source PDFs explicitly. Keep the prior release executable intact
until the new build has been verified.
