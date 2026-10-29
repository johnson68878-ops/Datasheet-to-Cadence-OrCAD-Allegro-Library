# Development history (sanitized)

This is a curated technical history, not the original internal diary or Git history. Customer identities, raw source extracts, internal board rules, file lists and engineering correspondence are excluded. Dates refer to development checkpoints, not universal qualification milestones.

| Period / checkpoint | Work and lessons |
|---|---|
| Initial development through September 2026 | Established PDF inspection, footprint models, geometry/SKILL, desktop workflow and native feedback. Added OrCAD grouping and design comparisons. Early reference/sample-oriented behavior did not demonstrate generic PDF understanding. |
| October 14, v0.5.52 | Portable Windows testing and version-specific CAD work. Successful native creation was insufficient to establish complete source interpretation or all-version support. |
| October 15-20 | Increasing emphasis on source evidence, no identity-based dispatch, unit/tolerance correctness, variant isolation and retaining failures. Distinguished component terminals from copper land dimensions. |
| October 21 baseline | 42-document evaluation remained incomplete in coverage; 56 candidate observations and only a small eligible subset were reported in earlier runs. Historical candidate counts must not be combined as one comparable native run. |
| October 21-28 | Improved merged/multi-level tables, candidate boundaries, OrCAD stability and draft/release separation. Established reproducible batch reporting and native verification records. Supplemental source review suggested stronger coordinates, physical table tracks and completeness proof. |
| October 29: terminal role isolation | Excluded stencil/manufacturing-page numbers from component-terminal fallback. This exposed, rather than solved, missing dimensions. |
| October 29: datum recognition | Prevented geometric-tolerance datum symbols from being treated as dimension-table symbols. A previously populated field became correctly unresolved; no new native coverage. |
| October 29: perimeter backend | Selected the existing direct package-symbol backend by supported SMD geometry instead of forcing ordinary peripheral pads through another launch mode. Five QFP/SOIC variants passed 17.4 generation/readback; historical licensing failures were retained in records. |
| October 29: array map handling | Recovered row axes sharing lines with function labels under a complete count/view caption. Prevented OCR symbols from borrowing later height notes. One additional source PDF achieved native PASS. |
| October 29: vector tolerance signs | Recovered missing vector-drawn plus/minus signs in constrained physical table cells, retaining bounds rather than interpreting two numbers as arbitrary min/max. |
| October 29: explicit official drawing | Added two-document association with source hashes, identifiers, topology, view and common-dimension checks. One additional assisted-source native PASS; kept separate from standalone-PDF coverage. |
| October 29: isolated pin assignment | PCB thermal-test prose incorrectly classified a whole page as a through-hole package. Isolating a complete oriented pin table under a unique variant removed four blockers in one document, but did not produce a new DRA. |
| October 29: circle evidence | Added opt-in below-line/horizontal labels, cubic-circle validation, witness-bound diameter and adjacent-center pitch. Rejected disconnected lines, conflicting labels and partial backtracking arcs. Not yet integrated into default generation. |
| October 29, v0.5.53rc1 internal test build | Packaged an engineering preview. Packaged OCR/renderer checks and one native 8-pin readback passed. No new all-document native or 17.2 qualification was claimed. |
| Public source snapshot | Removed proprietary reference assets and board-derived defaults; neutralized branding; retained synthetic tests and documented limitations. This intentionally differs from the internal executable. |

## Lessons retained

1. More permissive gates are not a substitute for correct source roles.
2. Native CAD PASS is model/output consistency, not independent proof of PDF interpretation.
3. Missing secondary OCR is different from conflicting OCR, but neither licenses guessing.
4. Count equality does not prove a complete pin inventory or a rectangular populated array.
5. A reference land drawing does not supply the component's ball diameter.
6. Single-case success must be followed by unrelated identities, conflicting examples and corpus comparison.
7. Preserve missing evidence and failed approaches in reports; do not turn skipped private tests into public successes.

The public repository begins with a new sanitized commit history. Subsequent fixes should document the trigger, structural rule, evidence, changed families, tests and unresolved cases.
