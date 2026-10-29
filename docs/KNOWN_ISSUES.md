# Known issues and contribution roadmap

## Priority 1: source correctness and broader PDF coverage

- **Coordinate contracts:** PDF points, raster pixels, crop origins and rotations are not yet represented by a single end-to-end TokenIR. Add reversible transforms and tests for rotated/cropped evidence before trusting more positional matches.
- **OCR disagreements:** distinguish engine unavailable, no token at location, and conflicting values. Local retries may improve coverage; do not silently overwrite evidence or infer decimals from expected dimensions.
- **Physical table structure:** improve column tracks, merged header paths and cross-page continuation/termination. A function/mux fragment must not replace the physical pin inventory.
- **Variant isolation:** shared family labels do not establish common geometry. Multiple package drawings, conditions and ordering codes require explicit scope.
- **Dimension roles:** bind arrows/witnesses to body, terminal, copper or center pitch. Do not interpret a GD&T tolerance as terminal width or a center-to-axis distance as full pitch.
- **Circular reference land integration:** evidence functions exist, but package/page association, model mapping and draft contracts are unfinished. Keep component ball diameter unknown when only copper is given.
- **Sparse arrays and nonstandard packages:** need explicit occupancy and numbered positions; do not factor pin counts or add NC pins.

## Priority 2: CAD and manufacturing review

- Separate reference land, calculated land and approved manufacturer/company rules. Published defaults are not certified IPC compliance.
- Validate copper, mask and paste independently. A reported historical dimension disagreement was not closed merely by successful native readback.
- Exercise native generation/readback across supported structures and actual installed CAD versions. License availability and application crashes are separate from parser errors.
- Preserve OrCAD grouping, multipart policy, order/side and layout. Change only with a demonstrated failing regression.
- Repeated GUI generation, native application lifecycle, ASCII paths and error reporting need broader workstation testing.

## Priority 3: maintainability and public reproducibility

- Split the large universal extractor into typed stages without losing evidence.
- Review cache identities for all engine/model/language/preprocessing parameters.
- Build legally redistributable synthetic drawing PDFs and adversarial tables; do not upload real customer documents or screenshots.
- Add region overlays showing OCR boxes, dimensions, scope and conflicts.
- Improve clean-install and cross-platform parser CI. Native CAD cannot be assumed available on CI.

## What a useful contribution looks like

A minimal synthetic failing example, a generic structural fix, a renamed/unrelated identity test, a deliberate conflict test, and a report of what remains unsupported. For drawing algorithms, prove the geometric association rather than tuning a threshold until one document passes.

Never supply an expected-answer map keyed by part number, filename, manufacturer or hash. Do not inflate success counts by removing failure evidence. Keep parser eligibility and actual native checks separate.
